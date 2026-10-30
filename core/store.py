"""
core/store.py

SAINT Store (Halo): one search over every place Halo gets apps from, installed through one queue.

    featured  SAINT's picks (packaging/linux/apps/catalog.json): Roblox, Roblox Studio, VS Code, Blender...
    repo      Arch Linux's official repositories      pacman -Ss
    flathub   Flathub                                  flathub.org search API (or `flatpak search`)
    aur       the Arch User Repository                 aur.archlinux.org RPC v5 (votes, popularity, out of date)
    windows   Windows apps through Wine                core/winapps.py (winget's catalog)

    search("blender")            -> [Result, ...] best first (exact names, then featured, repo, flathub, aur)
    plan(result)                 -> [core.saintos_apps.Step, ...] argument lists for core.saintos_apps.run_plan

Searching is read-only. Install commands are built only from names that pass strict patterns, so a
search result (which comes from the network) can't smuggle options or shell syntax into a command.
AUR packages are built by paru as the user (never root); the AUR is user-submitted, so results carry
votes, popularity and the PKGBUILD link, and the Store says so before installing.
"""

import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

from core import saintos_apps as sa

log = logging.getLogger("saint.store")

SOURCES = ("featured", "repo", "flathub", "aur", "windows")
SOURCE_LABEL = {"featured": "SAINT pick", "repo": "Arch", "flathub": "Flathub", "aur": "AUR", "windows": "Windows app"}
AUR_RPC = "https://aur.archlinux.org/rpc/v5/search/{q}?by=name-desc"
AUR_PKG = "https://aur.archlinux.org/packages/{name}"
FLATHUB_SEARCH = "https://flathub.org/api/v2/search"
_PKG = re.compile(r"^[a-z0-9@_+][a-z0-9@._+-]{0,127}$")
_REPO_LINE = re.compile(r"^(?P<repo>[a-z0-9-]+)/(?P<name>\S+) (?P<version>\S+)(?P<rest>.*)$")


@dataclass
class Result:
    source: str
    id: str                          # package name, Flatpak id, catalog id or winget id
    name: str
    summary: str = ""
    version: str = ""
    installed: bool = False
    extra: Dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return SOURCE_LABEL.get(self.source, self.source)


def _ok_query(q: str) -> str:
    q = " ".join(q.split())[:64]
    if not q or q.startswith("-"):
        raise ValueError("empty or invalid search")
    return q


def search_featured(q: str, catalog: Optional[Dict[str, sa.App]] = None) -> List[Result]:
    catalog = catalog if catalog is not None else sa.load_catalog()
    words = q.lower().split()
    hits = []
    for a in catalog.values():
        hay = f"{a.id} {a.name} {a.about} {a.category}".lower()
        if all(w in hay for w in words):
            hits.append(Result("featured", a.id, a.name, a.about, extra={"method": a.method, "ref": a.ref}))
    return hits


def search_repo(q: str, run: Callable = subprocess.run) -> List[Result]:
    try:
        out = run(["pacman", "-Ss", "--", q], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    hits, cur = [], None
    for line in out.splitlines():
        m = _REPO_LINE.match(line)
        if m:
            cur = Result("repo", m["name"], m["name"], version=m["version"], installed="[installed" in m["rest"],
                         extra={"repo": m["repo"]})
            hits.append(cur)
        elif cur is not None and line.startswith("    "):
            cur.summary = line.strip()
    return [h for h in hits if _PKG.match(h.id)]


def search_aur(q: str, fetch: Optional[Callable] = None) -> List[Result]:
    fetch = fetch or _get_json
    try:
        data = fetch(AUR_RPC.format(q=quote(q, safe="")))
    except Exception as e:                                   # offline, AUR down: the other sources still answer
        log.info("store.aur_unavailable %s", e)
        return []
    hits = []
    for r in (data or {}).get("results", []):
        name = r.get("Name", "")
        if not _PKG.match(name):
            continue
        hits.append(Result("aur", name, name, r.get("Description") or "", r.get("Version", ""),
                           extra={"votes": r.get("NumVotes", 0), "popularity": r.get("Popularity", 0.0),
                                  "out_of_date": bool(r.get("OutOfDate")), "maintainer": r.get("Maintainer"),
                                  "url": AUR_PKG.format(name=name)}))
    hits.sort(key=lambda h: (-float(h.extra["popularity"] or 0), -int(h.extra["votes"] or 0)))
    return hits


def search_flathub(q: str, post: Optional[Callable] = None, run: Callable = subprocess.run) -> List[Result]:
    post = post or _post_json
    hits = []
    try:
        data = post(FLATHUB_SEARCH, {"query": q})
        for h in (data or {}).get("hits", []):
            app_id = h.get("app_id", "")
            if sa._SAFE_FLATPAK.match(app_id):
                hits.append(Result("flathub", app_id, h.get("name") or app_id, h.get("summary") or ""))
        return hits
    except Exception as e:
        log.info("store.flathub_api_unavailable %s", e)
    try:                                                     # the local appstream data, when the API isn't there
        out = run(["flatpak", "search", "--columns=application,name,description", "--", q],
                  capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and sa._SAFE_FLATPAK.match(parts[0]):
            hits.append(Result("flathub", parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return hits


def search(q: str, sources=SOURCES, catalog=None, run: Callable = subprocess.run, fetch=None, post=None,
           windows: Optional[Callable] = None, limit: int = 40) -> List[Result]:
    """Best first: an exact name match, then SAINT's picks, Arch, Flathub, the AUR, Windows apps."""
    q = _ok_query(q)
    results: List[Result] = []
    if "featured" in sources:
        results += search_featured(q, catalog)
    if "repo" in sources:
        results += search_repo(q, run)
    if "flathub" in sources:
        results += search_flathub(q, post, run)
    if "aur" in sources:
        results += search_aur(q, fetch)
    if "windows" in sources:
        from core import winapps
        results += (windows or winapps.search)(q)
    featured_refs = {r.extra.get("ref") for r in results if r.source == "featured"}
    results = [r for r in results if r.source == "featured" or r.id not in featured_refs]   # a pick shows once
    order = {s: i for i, s in enumerate(SOURCES)}
    ql = q.lower()
    results.sort(key=lambda r: (r.name.lower() != ql and r.id.lower() != ql, order[r.source]))
    return results[:limit]


def plan(result: Result, catalog=None, home: Optional[str] = None) -> List[sa.Step]:
    """The install steps for one search result (argument lists only)."""
    if result.source == "featured":
        catalog = catalog if catalog is not None else sa.load_catalog()
        return sa.plan([result.id], catalog, home=home, manager="pacman")
    if result.source == "repo":
        _check(result.id)
        return [sa.Step(f"{result.name} (Arch)", ["pkexec", "pacman", "-Syu", "--needed", "--noconfirm", result.id],
                        [result.id], needs_admin=True)]
    if result.source == "aur":
        _check(result.id)
        return [sa.Step(f"{result.name} (AUR)", ["paru", "-S", "--needed", "--noconfirm", "--skipreview", "--sudo",
                                                 "pkexec", result.id], [result.id], needs_admin=True)]
    if result.source == "flathub":
        if not sa._SAFE_FLATPAK.match(result.id):
            raise ValueError(f"unsafe Flatpak id {result.id!r}")
        return [sa.Step("Flathub (for your user)", ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                                                    sa.FLATHUB], []),
                sa.Step(f"{result.name} (Flathub)", ["flatpak", "install", "--user", "-y", "--noninteractive",
                                                     "flathub", result.id], [result.id])]
    if result.source == "windows":
        from core import winapps
        return winapps.plan(result)
    raise ValueError(f"unknown source {result.source!r}")


def _check(name: str):
    if not _PKG.match(name):
        raise ValueError(f"unsafe package name {name!r}")


def _get_json(url: str):
    import requests
    r = requests.get(url, timeout=10, headers={"User-Agent": "SAINT-Store/1"})
    r.raise_for_status()
    return r.json()


def _post_json(url: str, body: dict):
    import requests
    r = requests.post(url, json=body, timeout=10, headers={"User-Agent": "SAINT-Store/1"})
    r.raise_for_status()
    return r.json()
