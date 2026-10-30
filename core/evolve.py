"""
core/evolve.py

Evolve (Halo): change the OS with Claude or another AI agent — "make window animations slower", "a widget
with my GPU temperature", "when my headphones connect, pause Spotify".

    1. new_change(request)  a workspace ~/.local/share/saint/evolve/<id>/ (a git repository) with
                            AGENTS.md/CLAUDE.md: what may change, where, and how it takes effect
    2. run_agent(...)       the agent writes there and nowhere else: home/ (files under your home
                            folder), system/ (files under /), packages.txt, apply.json
    3. review(ws)           exactly what changed (git), whether it touches the system, and anything refused
    4. apply(ws)            home files copied (old ones backed up); system files and packages only with
                            your password, between two snapper snapshots; then the reloads it asked for
                            (from a fixed list) or "after a restart"
    5. undo(ws)             home files restored from the backup; system changes reversed with
                            snapper undochange <before>..<after>

The agent is trusted with nothing: SAINT checks every path against the extension points below,
refuses links, oversized files and anything outside them, and runs only its own fixed commands.
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "saint" / "evolve"
MAX_FILE = 512 * 1024

# Where an agent's files may go. home/ is relative to your home folder, system/ to /.
HOME_ALLOWED = (".local/share/kwin/scripts/", ".local/share/kwin/effects/", ".local/share/plasma/plasmoids/",
                ".local/share/plasma/wallpapers/", ".local/share/plasma/look-and-feel/", ".local/share/color-schemes/",
                ".local/share/sounds/", ".local/share/icons/", ".local/share/applications/", ".local/share/saint/extensions/",
                ".config/autostart/", ".config/kwinrulesrc", ".config/kglobalshortcutsrc", ".config/kdeglobals",
                ".config/kwinrc", ".config/plasmarc", ".config/systemd/user/")
SYSTEM_ALLOWED = ("etc/udev/rules.d/", "etc/sysctl.d/", "etc/modprobe.d/", "etc/systemd/system/", "etc/xdg/",
                  "etc/environment.d/", "etc/fonts/conf.d/", "usr/local/bin/", "usr/local/share/")
SYSTEM_NEVER = ("etc/sudoers", "etc/passwd", "etc/shadow", "etc/group", "etc/pam.d/", "etc/polkit-1/",
                "etc/ssh/", "etc/pacman")
RELOADS: Dict[str, List[str]] = {
    "kwin": ["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
    "plasma": ["systemctl", "--user", "restart", "plasma-plasmashell.service"],
    "menu": ["kbuildsycoca6"],
    "systemd-user": ["systemctl", "--user", "daemon-reload"],
    "saint": ["saint", "--quit"],             # SAINT starts again from its pin / the next "Hey SAINT"
}
_PKG = re.compile(r"^[a-z0-9@_+][a-z0-9@._+-]{0,127}$")

AGENT_GUIDE = """# Evolve workspace (SAINT, Halo)

The user asked for this change to their Halo desktop (Arch Linux, KDE Plasma 6 on Wayland):

> {request}

Make it by writing files **in this folder only**:

- `home/<path>`: files to put under the user's home folder. Allowed places:
{home}
- `system/<path>`: files to put under `/` (applied with the user's password, after a snapshot). Allowed places:
{system}
- `packages.txt`: Arch packages to install, one per line (optional).
- `apply.json`: how the change takes effect, e.g. `{{"live": ["kwin"], "restart": false, "summary": "..."}}`.
  `live` may only list: {reloads}. Set `"restart": true` when it only works after a restart.

Rules: no symbolic links; files under 512 KB; don't touch anything else. KWin scripts and effects are
JavaScript packages with metadata.json; Plasma widgets are QML packages (Plasma 6). Prefer small, readable
changes, and say in apply.json's summary what you changed, in one or two sentences.
"""


def agents(which: Callable = shutil.which) -> Dict[str, Optional[List[str]]]:
    """The agents that can run here -> the argv prefix SAINT runs with the prompt appended."""
    found = {}
    if which("claude"):
        found["Claude Code"] = ["claude", "-p", "--permission-mode", "acceptEdits",
                                "--allowedTools", "Read,Write,Edit,Glob,Grep"]
    if which("codex"):
        found["Codex"] = ["codex", "exec", "--sandbox", "workspace-write"]
    if which("gemini"):
        found["Gemini CLI"] = ["gemini", "--sandbox", "-p"]
    found["Local model (Ollama)"] = None          # SAINT writes the files from the model's JSON answer
    return found


@dataclass
class Review:
    home: List[str] = field(default_factory=list)
    system: List[str] = field(default_factory=list)
    packages: List[str] = field(default_factory=list)
    refused: List[str] = field(default_factory=list)
    live: List[str] = field(default_factory=list)
    restart: bool = False
    summary: str = ""
    diff: str = ""

    @property
    def touches_system(self) -> bool:
        return bool(self.system or self.packages)


def _git(ws: Path, *args, check=True) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "SAINT Evolve", "GIT_AUTHOR_EMAIL": "evolve@saint.local",
           "GIT_COMMITTER_NAME": "SAINT Evolve", "GIT_COMMITTER_EMAIL": "evolve@saint.local"}
    res = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, env=env)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def new_change(request: str, root: Path = ROOT) -> Path:
    request = " ".join(request.split())[:2000]
    if not request:
        raise ValueError("Say what you'd like to change.")
    ws = root / time.strftime("%Y%m%d-%H%M%S")
    (ws / "home").mkdir(parents=True)
    (ws / "system").mkdir()
    guide = AGENT_GUIDE.format(request=request,
                               home="\n".join(f"  - `home/{p}`" for p in HOME_ALLOWED),
                               system="\n".join(f"  - `system/{p}`" for p in SYSTEM_ALLOWED),
                               reloads=", ".join(RELOADS))
    for name in ("AGENTS.md", "CLAUDE.md"):
        (ws / name).write_text(guide, encoding="utf-8")
    (ws / "request.txt").write_text(request + "\n", encoding="utf-8")
    (ws / ".gitignore").write_text("backup/\nstate.json\n", encoding="utf-8")
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "request")
    return ws


def run_agent(ws: Path, agent: str, on_line: Callable[[str], None] = print, ask_model: Optional[Callable] = None) -> int:
    """Run ``agent`` in the workspace. Its output streams to ``on_line``; returns its exit code."""
    request = (ws / "request.txt").read_text(encoding="utf-8").strip()
    argv = agents().get(agent)
    if argv is None:
        return _local_model(ws, request, on_line, ask_model)
    prompt = f"{request}\n\nFollow AGENTS.md in this folder exactly."
    proc = subprocess.Popen([*argv, prompt], cwd=str(ws), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        on_line(line.rstrip())
    return proc.wait()


def _local_model(ws: Path, request: str, on_line, ask_model=None) -> int:
    """A local model writes the files as JSON: {"files": [{"path", "content"}], "packages": [], "apply": {...}}."""
    ask_model = ask_model or _ask_ollama
    guide = (ws / "AGENTS.md").read_text(encoding="utf-8")
    on_line("Asking the local model…")
    answer = ask_model(guide + "\n\nAnswer with JSON only: {\"files\": [{\"path\": \"home/...\", \"content\": \"...\"}], "
                               "\"packages\": [], \"apply\": {\"live\": [], \"restart\": false, \"summary\": \"\"}}")
    try:
        data = json.loads(answer)
    except (TypeError, ValueError):
        on_line("The model didn't answer with JSON.")
        return 1
    for f in data.get("files", [])[:50]:
        rel = str(f.get("path", ""))
        if not (rel.startswith("home/") or rel.startswith("system/")) or ".." in Path(rel).parts:
            on_line(f"skipped {rel!r}")
            continue
        out = ws / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(str(f.get("content", ""))[:MAX_FILE], encoding="utf-8")
        on_line(f"wrote {rel}")
    if data.get("packages"):
        (ws / "packages.txt").write_text("\n".join(map(str, data["packages"])) + "\n", encoding="utf-8")
    (ws / "apply.json").write_text(json.dumps(data.get("apply") or {}), encoding="utf-8")
    return 0


def _ask_ollama(prompt: str) -> str:
    import requests
    from core.config import config
    r = requests.post(config.get("ai.base_url", "http://localhost:11434").rstrip("/") + "/api/generate",
                      json={"model": config.get("ai.model", "llama3.2:3b"), "prompt": prompt, "format": "json",
                            "stream": False}, timeout=600)
    r.raise_for_status()
    return r.json().get("response", "")


def _allowed(rel: str, allowed, never=()) -> bool:
    return any(rel == a.rstrip("/") or rel.startswith(a) for a in allowed) and not any(rel.startswith(n) for n in never)


def review(ws: Path) -> Review:
    """What the agent changed, sorted into what SAINT will apply and what it refuses."""
    rv = Review()
    _git(ws, "add", "-A")
    rv.diff = _git(ws, "diff", "--cached", "--stat") + "\n" + _git(ws, "diff", "--cached")
    changed = [l for l in _git(ws, "diff", "--cached", "--name-only").splitlines() if l]
    for rel in changed:
        p = ws / rel
        if rel in ("apply.json", "packages.txt") or not p.exists():
            continue
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_FILE:
            rv.refused.append(f"{rel} (a link, not a file, or over 512 KB)")
        elif rel.startswith("home/") and _allowed(rel[5:], HOME_ALLOWED):
            rv.home.append(rel[5:])
        elif rel.startswith("system/") and _allowed(rel[7:], SYSTEM_ALLOWED, SYSTEM_NEVER):
            rv.system.append(rel[7:])
        elif rel not in ("AGENTS.md", "CLAUDE.md", "request.txt", ".gitignore"):
            rv.refused.append(f"{rel} (outside what Evolve may change)")
    pk = ws / "packages.txt"
    if pk.exists():
        for name in pk.read_text(encoding="utf-8").split():
            (rv.packages if _PKG.match(name) else rv.refused).append(name)
    try:
        plan = json.loads((ws / "apply.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plan = {}
    rv.live = [r for r in plan.get("live", []) if r in RELOADS]
    rv.restart = bool(plan.get("restart")) or bool(rv.system)
    rv.summary = str(plan.get("summary", ""))[:500]
    return rv


def apply(ws: Path, rv: Review, home: Path = Path.home(), run: Callable = subprocess.run) -> List[str]:
    """Apply a reviewed change. Returns what was done, line by line. Raises if the system part fails."""
    done, state = [], {"home_new": [], "snapshots": None}
    backup = ws / "backup" / "home"
    for rel in rv.home:
        dest = home / rel
        if dest.exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup / rel)
        else:
            state["home_new"].append(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ws / "home" / rel, dest)
        done.append(f"~/{rel}")
    if rv.touches_system:
        pre = _snapshot(f"Evolve {ws.name}: before", run)
        cmds = []
        for rel in rv.system:
            cmds.append(["pkexec", "install", "-D", "-m", "0644", str(ws / "system" / rel), "/" + rel])
        if rv.packages:
            cmds.append(["pkexec", "pacman", "-Syu", "--needed", "--noconfirm", *rv.packages])
        for c in cmds:
            res = run(c, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"{' '.join(c[:3])}: {(res.stderr or '').strip()[:200]}")
        post = _snapshot(f"Evolve {ws.name}: after", run)
        state["snapshots"] = [pre, post]
        done += [f"/{r}" for r in rv.system] + ([f"packages: {' '.join(rv.packages)}"] if rv.packages else [])
    for r in rv.live:
        run(RELOADS[r], capture_output=True)
        done.append(f"reloaded {r}")
    (ws / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _git(ws, "commit", "-q", "-m", f"applied: {rv.summary or ws.name}", check=False)
    return done


def undo(ws: Path, home: Path = Path.home(), run: Callable = subprocess.run) -> List[str]:
    rv_home = []
    state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    backup = ws / "backup" / "home"
    for f in (p for p in backup.rglob("*") if p.is_file()) if backup.exists() else []:
        rel = f.relative_to(backup).as_posix()
        shutil.copy2(f, home / rel)
        rv_home.append(f"restored ~/{rel}")
    for rel in state.get("home_new", []):
        (home / rel).unlink(missing_ok=True)
        rv_home.append(f"removed ~/{rel}")
    snaps = state.get("snapshots")
    if snaps and all(isinstance(n, int) for n in snaps):
        res = run(["pkexec", "snapper", "-c", "root", "undochange", f"{snaps[0]}..{snaps[1]}"],
                  capture_output=True, text=True)
        rv_home.append("system change reversed" if res.returncode == 0 else f"system undo failed: {res.stderr.strip()[:200]}")
    (ws / "state.json").write_text(json.dumps({**state, "undone": True}), encoding="utf-8")
    return rv_home


def history(root: Path = ROOT) -> List[Dict]:
    out = []
    for ws in sorted(root.glob("*/request.txt"), reverse=True):
        d = ws.parent
        try:
            state = json.loads((d / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = None
        out.append({"id": d.name, "path": str(d), "request": ws.read_text(encoding="utf-8").strip(),
                    "status": "not applied" if state is None else "undone" if state.get("undone") else "applied"})
    return out


def _snapshot(desc: str, run) -> Optional[int]:
    res = run(["pkexec", "snapper", "-c", "root", "create", "--print-number", "--description", desc[:80]],
              capture_output=True, text=True)
    try:
        return int(res.stdout.strip())
    except (TypeError, ValueError):
        return None
