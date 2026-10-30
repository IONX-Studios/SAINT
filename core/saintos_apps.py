"""
core/saintos_apps.py

SAINTOS App Installer: pick the apps you always use, install them in one go (like Ninite).

The catalog (packaging/linux/apps/catalog.json, installed to /usr/share/saintos/apps.json)
is the only source of what can be installed. A selection is turned into a short plan:

    1. system   one admin prompt for every distribution package (includes what npm apps need,
                e.g. nodejs): `pkexec pacman -S --needed --noconfirm ...` on Halo (Arch; the catalog's
                "arch" names), `pkexec dnf install -y --skip-unavailable ...` on Fedora ("ref")
    2. flatpak  `flatpak remote-add --user --if-not-exists flathub ...` then one
                `flatpak install --user -y --noninteractive flathub ...` (no admin needed)
    3. npm      `npm install -g --prefix ~/.local <package>` per app (no admin needed)

Every step is an argument list (no shell). Package names and Flatpak ids come from the catalog
and are checked against a strict pattern, so nothing a user or a model types reaches a command.

    python -m core.saintos_apps --list
    python -m core.saintos_apps steam claude-code discord        (asks once for the admin password
                                                                    only when Fedora packages are in it)
"""

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"
_CATALOG_PATHS = [Path("/usr/share/saintos/apps.json"),
                  Path(__file__).resolve().parent.parent / "packaging" / "linux" / "apps" / "catalog.json"]
_SAFE_DNF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SAFE_FLATPAK = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
_SAFE_NPM = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
METHODS = ("dnf", "flatpak", "npm")
# What an npm app's "needs" are called on Arch (npm is its own package there).
_ARCH_NEEDS = {"nodejs": ["nodejs", "npm"]}


def system_manager(which: Callable[[str], Optional[str]] = shutil.which) -> str:
    """'pacman' on Halo (Arch), else 'dnf' (SAINTOS on Fedora)."""
    return "pacman" if which("pacman") else "dnf"


@dataclass
class App:
    id: str
    name: str
    category: str
    method: str
    ref: str
    about: str = ""
    needs: List[str] = field(default_factory=list)
    arch: str = ""                  # pacman package names for a "dnf" (distribution package) app

    @property
    def refs(self) -> List[str]:
        return self.ref.split() if self.method == "dnf" else [self.ref]

    def system_refs(self, manager: str) -> List[str]:
        return self.arch.split() if manager == "pacman" else self.refs

    def system_needs(self, manager: str) -> List[str]:
        if manager != "pacman":
            return list(self.needs)
        return [p for n in self.needs for p in _ARCH_NEEDS.get(n, [n])]


@dataclass
class Step:
    label: str
    argv: List[str]
    apps: List[str]
    needs_admin: bool = False


def load_catalog(path: Optional[Path] = None) -> Dict[str, App]:
    for p in [path] if path else _CATALOG_PATHS:
        if p and p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            apps = {}
            for a in data["apps"]:
                app = App(**a)
                validate(app)
                apps[app.id] = app
            return apps
    raise FileNotFoundError("SAINTOS app catalog not found (looked in " + ", ".join(map(str, _CATALOG_PATHS)) + ")")


def validate(app: App):
    if app.method not in METHODS:
        raise ValueError(f"{app.id}: unknown install method {app.method!r}")
    pattern = {"dnf": _SAFE_DNF, "flatpak": _SAFE_FLATPAK, "npm": _SAFE_NPM}[app.method]
    for ref in app.refs:
        if not pattern.match(ref):
            raise ValueError(f"{app.id}: unsafe {app.method} reference {ref!r}")
    if app.method == "dnf" and not app.arch.split():
        raise ValueError(f"{app.id}: a distribution package needs its Arch name(s) in \"arch\"")
    for ref in app.arch.split():
        if not _SAFE_DNF.match(ref):
            raise ValueError(f"{app.id}: unsafe pacman package {ref!r}")
    for dep in app.needs:
        if not _SAFE_DNF.match(dep):
            raise ValueError(f"{app.id}: unsafe dependency {dep!r}")


def plan(selected: Iterable[str], catalog: Dict[str, App], home: Optional[str] = None,
         manager: Optional[str] = None) -> List[Step]:
    """Turn app ids into the commands that install them. Unknown ids raise KeyError.
    ``manager`` is 'pacman' or 'dnf' (default: whichever this system has)."""
    apps = [catalog[i] for i in dict.fromkeys(selected)]           # keeps order, drops repeats
    home = home or os.path.expanduser("~")
    manager = manager or system_manager()
    steps: List[Step] = []
    sys_pkgs, sys_apps = [], []
    for a in apps:
        if a.method == "dnf":
            sys_pkgs += a.system_refs(manager)
            sys_apps.append(a.id)
        sys_pkgs += a.system_needs(manager)
    if sys_pkgs:
        pkgs = list(dict.fromkeys(sys_pkgs))
        if manager == "pacman":
            label, argv = "Arch packages: ", ["pkexec", "pacman", "-S", "--needed", "--noconfirm", *pkgs]
        else:
            label, argv = "Fedora packages: ", ["pkexec", "dnf", "install", "-y", "--skip-unavailable", *pkgs]
        steps.append(Step(label + ", ".join(pkgs), argv, sys_apps or [a.id for a in apps if a.needs],
                          needs_admin=True))
    flat = [a for a in apps if a.method == "flatpak"]
    if flat:
        steps.append(Step("Flathub (for your user)", ["flatpak", "remote-add", "--user", "--if-not-exists",
                                                      "flathub", FLATHUB], []))
        # One app per step: progress and the result are per app, and one failure doesn't take the rest along.
        for a in flat:
            steps.append(Step(f"{a.name} (Flathub)", ["flatpak", "install", "--user", "-y", "--noninteractive",
                                                      "flathub", a.ref], [a.id]))
    for a in apps:
        if a.method == "npm":
            steps.append(Step(f"{a.name} (npm, into ~/.local)",
                              ["npm", "install", "-g", "--prefix", posixpath.join(home, ".local"), a.ref], [a.id]))
    return steps


def missing_tools(steps: List[Step], which: Callable[[str], Optional[str]] = shutil.which) -> List[str]:
    tools = {s.argv[0] for s in steps}
    system = [s for s in steps if s.argv[:2] in (["pkexec", "dnf"], ["pkexec", "pacman"])]
    tools.update(s.argv[1] for s in system)
    # npm arrives with the system-package step (nodejs), so it doesn't have to exist beforehand.
    if any("nodejs" in s.argv for s in system):
        tools.discard("npm")
    return sorted(t for t in tools if not which(t))


def installed(app: App, run: Callable = subprocess.run, manager: Optional[str] = None) -> bool:
    """Is it already installed? (flatpak info / pacman -Q or rpm -q / ~/.local/bin for npm)"""
    try:
        if app.method == "flatpak":
            return run(["flatpak", "info", app.ref], capture_output=True).returncode == 0
        if app.method == "dnf":
            manager = manager or system_manager()
            query = ["pacman", "-Q"] if manager == "pacman" else ["rpm", "-q"]
            return run([*query, *app.system_refs(manager)], capture_output=True).returncode == 0
        if app.method == "npm":
            return (Path.home() / ".local" / "lib" / "node_modules" / app.ref).exists()
    except OSError:
        return False
    return False


def blockers(cmdline: str = "", exists: Callable[[str], bool] = os.path.exists) -> Tuple[List[str], List[str]]:
    """What would make an install fail or not last, said before it starts: (errors, warnings)."""
    errors, warnings = [], []
    if not cmdline:
        try:
            cmdline = Path("/proc/cmdline").read_text()
        except OSError:
            cmdline = ""
    if exists("/var/lib/pacman/db.lck"):
        errors.append("Another app install or update is running (pacman is busy). Try again when it's finished.")
    if "archisobasedir=" in cmdline or "rd.live.image" in cmdline:
        warnings.append("This is the live session: apps install into memory and are gone after a restart. "
                        "Install Halo first to keep them.")
    return errors, warnings


_PERCENT = re.compile(r"(\d{1,3})\s?%")


def run_plan(steps: List[Step], on_line: Callable[[str], None] = print,
             popen: Callable = subprocess.Popen,
             on_step: Optional[Callable[[int, int, Step, str, int], None]] = None) -> Dict[str, bool]:
    """Run each step, streaming its output. A failed step doesn't stop the others; the result says which
    apps' steps succeeded (app id -> True/False).

    ``on_step(index, total, step, status, percent)`` reports progress: status is "started", "progress"
    (percent from the tool's own output), "done" or "failed"."""
    result: Dict[str, bool] = {}
    report = on_step or (lambda *a: None)
    for i, step in enumerate(steps):
        on_line(f"==> {step.label}")
        report(i, len(steps), step, "started", 0)
        last = -1
        try:
            proc = popen(step.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                line = line.rstrip()
                on_line(line)
                m = _PERCENT.findall(line)
                if m and (pct := min(100, int(m[-1]))) != last:
                    last = pct
                    report(i, len(steps), step, "progress", pct)
            ok = proc.wait() == 0
        except FileNotFoundError as e:
            on_line(f"    {e}")
            ok = False
        on_line("    done" if ok else f"    FAILED ({' '.join(step.argv[:3])} ...)")
        report(i, len(steps), step, "done" if ok else "failed", 100 if ok else last)
        for app_id in step.apps:
            result[app_id] = ok and result.get(app_id, True)
    return result


def summary(result: Dict[str, bool], catalog: Dict[str, App]) -> Tuple[str, str]:
    """The notification after an install: (title, body) naming every app installed and every one that failed."""
    ok = [catalog[i].name for i, good in result.items() if good and i in catalog]
    bad = [catalog[i].name for i, good in result.items() if not good and i in catalog]
    if ok and not bad:
        title = f"Installed {len(ok)} app{'s' if len(ok) != 1 else ''}"
    elif ok:
        title = f"Installed {len(ok)}, {len(bad)} failed"
    else:
        title = "Nothing was installed"
    body = "\n".join([*(f"✓ {n}" for n in ok), *(f"✗ {n}" for n in bad)])
    return title, body or "No apps were selected."


def finish(result: Dict[str, bool], catalog: Dict[str, App], run: Callable = subprocess.run,
           which: Callable[[str], Optional[str]] = shutil.which) -> Tuple[str, str]:
    """After an install: rebuild the app menu so new apps show up without logging out, and say what was
    installed in a desktop notification (Linux). Returns the notification's (title, body)."""
    title, body = summary(result, catalog)
    quiet = dict(capture_output=True, timeout=60)
    for cmd in (["kbuildsycoca6"], ["update-desktop-database", os.path.expanduser("~/.local/share/applications")]):
        if which(cmd[0]):
            try:
                run(cmd, **quiet)
            except (OSError, subprocess.TimeoutExpired):
                pass
    if which("notify-send"):
        try:
            run(["notify-send", "--app-name=SAINT", "--icon=saint", title, body], **quiet)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return title, body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SAINTOS App Installer")
    ap.add_argument("apps", nargs="*", help="app ids to install (see --list)")
    ap.add_argument("--list", action="store_true", help="show the catalog")
    ap.add_argument("--dry-run", action="store_true", help="print the commands instead of running them")
    args = ap.parse_args(argv)
    catalog = load_catalog()
    if args.list or not args.apps:
        for cat in dict.fromkeys(a.category for a in catalog.values()):
            print(f"\n{cat}")
            for a in (a for a in catalog.values() if a.category == cat):
                print(f"  {a.id:<16} {a.name:<24} {a.about}")
        return 0
    unknown = [i for i in args.apps if i not in catalog]
    if unknown:
        print(f"Unknown app id(s): {', '.join(unknown)} (see --list)", file=sys.stderr)
        return 2
    steps = plan(args.apps, catalog)
    if args.dry_run:
        for s in steps:
            print(" ".join(s.argv))
        return 0
    missing = missing_tools(steps)
    if missing:
        print(f"Missing: {', '.join(missing)}", file=sys.stderr)
        return 1
    result = run_plan(steps)
    finish(result, catalog)
    failed = [i for i, ok in result.items() if not ok]
    print("\nInstalled: " + (", ".join(i for i, ok in result.items() if ok) or "nothing"))
    if failed:
        print("Failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
