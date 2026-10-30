"""
core/winapps.py

The Windows app agent (Halo): "SAINT, install Notepad++" gets a Windows-only app running with Wine.

    1. Native first: the Store lists a Linux build (Arch, Flathub, AUR) of the same app above this one.
    2. Find the app in winget's public catalog (Microsoft's community repository of Windows installers):
       cdn.winget.microsoft.com/cache/source.msix -> index.db (SQLite), cached for a day.
    3. Its manifest gives the installer's URL, SHA-256 and type (NSIS, Inno, MSI, Burn, plain exe, zip).
    4. Download it, check the SHA-256 (any mismatch stops the install), install it silently with Wine
       into the app's own prefix ~/.local/share/saint/wine/<app>. Wine puts the app in the start menu.

Apps that can't work under Wine (kernel anti-cheat, drivers, Microsoft Store-only packages) are
refused with the reason, and pointed at a Linux alternative when there is one, instead of installing
something that won't start.

Every command is an argument list; ids, URLs and installer switches are checked against strict patterns.
This file also runs as a small helper (python winapps.py fetch|portable ...) for the install steps.
"""

import hashlib
import os
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

CATALOG_URL = "https://cdn.winget.microsoft.com/cache/source.msix"
MANIFEST_URL = "https://cdn.winget.microsoft.com/cache/{path}"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "saint" / "winget"
PREFIXES = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "saint" / "wine"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")
_PATH = re.compile(r"^manifests/[A-Za-z0-9/.+_ -]{1,250}$")
_SWITCH = re.compile(r"^[/-][A-Za-z0-9_=:.\\-]{0,64}$")
_SHA = re.compile(r"^[0-9A-Fa-f]{64}$")
WINE_PACKAGES = ["wine", "wine-mono", "wine-gecko"]

# Known not to work under Wine, with the reason and, when there is one, the Store id to use instead.
BLOCKED = {
    "RiotGames.Valorant.": ("Valorant's Vanguard anti-cheat is a Windows kernel driver; it can't run on Linux.", ""),
    "RiotGames.LeagueOfLegends.": ("League of Legends' Vanguard anti-cheat is a Windows kernel driver.", ""),
    "Roblox.Roblox": ("Roblox's Windows client blocks Wine (Hyperion anti-cheat).", "roblox"),
    "Roblox.RobloxStudio": ("Roblox Studio needs Vinegar's tuned Wine setup.", "roblox-studio"),
    "FACEITLTD.FACEITClient": ("FACEIT's anti-cheat is a Windows kernel driver.", ""),
    "Microsoft.Office": ("Microsoft 365's click-to-run installer doesn't work under Wine; Office on the web or "
                         "LibreOffice works instead.", "libreoffice"),
    "Microsoft.VisualStudioCode": ("VS Code has a native Linux build.", "vscode"),
    "Valve.Steam": ("Steam has a native Linux build that runs Windows games with Proton.", "steam"),
    "Discord.Discord": ("Discord has a native Linux build.", "discord"),
    "Spotify.Spotify": ("Spotify has a native Linux build.", "spotify"),
    "BlenderFoundation.Blender": ("Blender has a native Linux build.", "blender"),
}
_DRIVERS = re.compile(r"\b(driver|drivers|chipset|firmware|bios)\b", re.I)


def blocked(winget_id: str, name: str = "") -> Optional[tuple]:
    """(reason, store id of a Linux alternative or "") when the app can't run under Wine; else None."""
    for prefix, why in BLOCKED.items():
        if winget_id == prefix or winget_id.startswith(prefix if prefix.endswith(".") else prefix + "."):
            return why
    if _DRIVERS.search(name) or _DRIVERS.search(winget_id):
        return ("Drivers are installed on Halo by the system, not with Windows installers.", "")
    return None


# --------------------------------------------------------------------------- catalog
def index_db(cache: Path = CACHE, fetch_bytes: Optional[Callable[[str], bytes]] = None,
             max_age: float = 86400) -> Path:
    """winget's catalog as a local SQLite file (downloaded at most once a day)."""
    db = cache / "index.db"
    if db.exists() and time.time() - db.stat().st_mtime < max_age:
        return db
    cache.mkdir(parents=True, exist_ok=True)
    data = (fetch_bytes or _get_bytes)(CATALOG_URL)
    msix = cache / "source.msix"
    msix.write_bytes(data)
    with zipfile.ZipFile(msix) as z, z.open("Public/index.db") as src:
        tmp = cache / "index.db.part"
        tmp.write_bytes(src.read())
    tmp.replace(db)
    msix.unlink()
    return db


def _version_key(v: str):
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-+]", v)]


def _path(con, part: int) -> str:
    parts = []
    while part:
        row = con.execute("select parent, pathpart from pathparts where rowid=?", (part,)).fetchone()
        if not row:
            break
        parts.append(row[1])
        part = row[0]
    return "/".join(reversed(parts))


def search(q: str, db: Optional[Path] = None, limit: int = 12) -> list:
    """Windows apps whose name or id matches ``q``, newest version of each (core.store.Result)."""
    from core.store import Result
    try:
        db = db or index_db()
    except Exception:                       # offline: the Store still has the other sources
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        like = f"%{q.strip()}%"
        rows = con.execute(
            "select i.id, n.name, v.version, m.pathpart from manifest m "
            "join ids i on m.id = i.rowid join names n on m.name = n.rowid join versions v on m.version = v.rowid "
            "where n.name like ? or i.id like ? limit 400", (like, like)).fetchall()
        newest: Dict[str, tuple] = {}
        for wid, name, version, part in rows:
            if _ID.match(wid) and (wid not in newest or _version_key(version) > _version_key(newest[wid][1])):
                newest[wid] = (name, version, part)
        ql = q.strip().lower()
        ordered = sorted(newest.items(), key=lambda kv: (kv[1][0].lower() != ql, len(kv[1][0])))
        out = []
        for wid, (name, version, part) in ordered[:limit]:
            why = blocked(wid, name)
            out.append(Result("windows", wid, name, "Windows app (runs with Wine)", version,
                              extra={"path": _path(con, part), "blocked": why[0] if why else "",
                                     "instead": why[1] if why else ""}))
        return out
    finally:
        con.close()


def manifest(path: str, fetch_text: Optional[Callable[[str], str]] = None) -> dict:
    if not _PATH.match(path) or ".." in path:
        raise ValueError(f"unexpected manifest path {path!r}")
    import yaml
    return yaml.safe_load((fetch_text or _get_text)(MANIFEST_URL.format(path=path))) or {}


# --------------------------------------------------------------------------- installer choice
SUPPORTED = ("nullsoft", "inno", "msi", "wix", "burn", "exe", "zip")


def pick_installer(man: dict) -> dict:
    """The installer to use: x64 before x86, a type Wine can run silently; ValueError says why there's none."""
    installers = man.get("Installers") or []
    default_type = (man.get("InstallerType") or "").lower()
    cands = []
    for inst in installers:
        kind = (inst.get("InstallerType") or default_type).lower()
        arch = (inst.get("Architecture") or "").lower()
        url = inst.get("InstallerUrl") or ""
        sha = inst.get("InstallerSha256") or ""
        if kind not in SUPPORTED or arch not in ("x64", "x86", "neutral") or not url.startswith("https://") \
                or not _SHA.match(sha):
            continue
        if kind == "exe" and not _silent_switches(inst, man):
            continue                         # an exe without a known silent switch would sit on a dialog
        cands.append(({"x64": 0, "neutral": 1, "x86": 2}[arch], {**inst, "InstallerType": kind}))
    if not cands:
        kinds = sorted({(i.get("InstallerType") or default_type) for i in installers}) or ["none"]
        raise ValueError("no installer Wine can run silently (this app ships: " + ", ".join(kinds) + ")")
    cands.sort(key=lambda c: c[0])
    return cands[0][1]


def _silent_switches(inst: dict, man: dict) -> List[str]:
    sw = (inst.get("InstallerSwitches") or man.get("InstallerSwitches") or {})
    raw = sw.get("Silent") or sw.get("SilentWithProgress") or ""
    args = raw.split()
    return args if args and all(_SWITCH.match(a) for a in args) else []


def silent_args(inst: dict, man: Optional[dict] = None) -> List[str]:
    kind = inst["InstallerType"]
    custom = _silent_switches(inst, man or {})
    if kind == "nullsoft":
        return ["/S"]
    if kind == "inno":
        return ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    if kind == "burn":
        return ["/quiet", "/norestart"]
    if kind in ("msi", "wix"):
        return ["/qn", "/norestart"]
    return custom


def prefix_for(winget_id: str) -> Path:
    return PREFIXES / re.sub(r"[^A-Za-z0-9._-]", "_", winget_id)


def plan(result, man: Optional[dict] = None, which: Optional[Callable] = None, python: str = sys.executable) -> list:
    """Install steps for a Windows app (core.saintos_apps.Step). ValueError when it can't be installed."""
    import shutil
    from core.saintos_apps import Step
    which = which or shutil.which
    if not _ID.match(result.id):
        raise ValueError(f"unexpected app id {result.id!r}")
    why = blocked(result.id, result.name)
    if why:
        raise ValueError(why[0])
    man = man if man is not None else manifest(result.extra["path"])
    inst = pick_installer(man)
    kind = inst["InstallerType"]
    url, sha = inst["InstallerUrl"], inst["InstallerSha256"].lower()
    prefix = prefix_for(result.id)
    name = os.path.basename(url.split("?")[0]) or "installer"
    name = re.sub(r"[^A-Za-z0-9._+-]", "_", name)[:100]
    if kind in ("msi", "wix") and not name.lower().endswith(".msi"):
        name += ".msi"
    file = str(CACHE / "downloads" / result.id / name)
    helper = str(Path(__file__).resolve())
    wine_env = ["env", f"WINEPREFIX={prefix}", "WINEDEBUG=-all"]     # Wine adds the app to the start menu
    steps = []
    if not which("wine"):
        steps.append(Step("Wine (runs Windows apps; once)", ["pkexec", "pacman", "-Syu", "--needed", "--noconfirm",
                                                              *WINE_PACKAGES], [], needs_admin=True))
    steps.append(Step(f"Download {result.name} (checking its SHA-256)", [python, helper, "fetch", url, sha, file],
                      [result.id]))
    if kind == "zip":
        nested = [n.get("RelativeFilePath", "") for n in (inst.get("NestedInstallerFiles") or [])]
        exe = next((n for n in nested if n.lower().endswith(".exe") and ".." not in n), "")
        if not exe:
            raise ValueError("this zip doesn't say which program to start")
        steps.append(Step(f"Set up {result.name}", [python, helper, "portable", file, str(prefix), exe, result.name],
                          [result.id]))
    elif kind in ("msi", "wix"):
        steps.append(Step(f"Install {result.name} with Wine", [*wine_env, "wine", "msiexec", "/i", file,
                                                               *silent_args(inst, man)], [result.id]))
    else:
        steps.append(Step(f"Install {result.name} with Wine", [*wine_env, "wine", file, *silent_args(inst, man)],
                          [result.id]))
    return steps


# --------------------------------------------------------------------------- helpers run by the install steps
def fetch(url: str, sha256: str, dest: str) -> int:
    """Download ``url`` to ``dest``; refuse it unless its SHA-256 matches the catalog's."""
    if not url.startswith("https://") or not _SHA.match(sha256):
        print("refusing: not an https URL with a SHA-256")
        return 2
    import requests
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    part = out.with_suffix(out.suffix + ".part")
    with requests.get(url, stream=True, timeout=30, headers={"User-Agent": "SAINT/1"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        last = -1
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total and (pct := done * 100 // total) != last:
                    last = pct
                    print(f"downloading {pct}%", flush=True)
    if h.hexdigest() != sha256.lower():
        part.unlink(missing_ok=True)
        print(f"SHA-256 mismatch: expected {sha256.lower()}, got {h.hexdigest()} — not installing")
        return 1
    part.replace(out)
    print("downloaded and verified")
    return 0


def portable(archive: str, prefix: str, exe: str, name: str) -> int:
    """A zip app: unpack it into the prefix's Program Files and add a start-menu entry for its program."""
    dest = Path(prefix) / "drive_c" / "Program Files" / re.sub(r"[^A-Za-z0-9._ -]", "_", name)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest.resolve())):
                print(f"refusing path outside the app folder: {member}")
                return 1
        z.extractall(dest)
    program = dest / exe.replace("\\", "/")
    apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / f"saint-wine-{re.sub(r'[^A-Za-z0-9_-]', '_', name)}.desktop"
    entry.write_text("[Desktop Entry]\nType=Application\n"
                     f"Name={name}\nComment=Windows app (Wine)\n"
                     f"Exec=env WINEPREFIX=\"{prefix}\" wine \"{program}\"\n"
                     "Icon=wine\nCategories=Wine;\n", encoding="utf-8")
    print(f"{name} is in the start menu")
    return 0


def _get_bytes(url: str) -> bytes:
    import requests
    r = requests.get(url, timeout=60, headers={"User-Agent": "SAINT/1"})
    r.raise_for_status()
    return r.content


def _get_text(url: str) -> str:
    import requests
    r = requests.get(url, timeout=20, headers={"User-Agent": "SAINT/1"})
    r.raise_for_status()
    return r.text


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "fetch":
        sys.exit(fetch(*sys.argv[2:]))
    if len(sys.argv) == 6 and sys.argv[1] == "portable":
        sys.exit(portable(*sys.argv[2:]))
    print("usage: winapps.py fetch URL SHA256 DEST | portable ZIP PREFIX EXE NAME")
    sys.exit(2)
