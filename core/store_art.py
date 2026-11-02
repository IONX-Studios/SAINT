"""
core/store_art.py

Real app artwork for the Store: icon, screenshots, developer and brand colour from Flathub's AppStream data
(the publisher's own metadata, https://flathub.org/api/v2/appstream/<id>). Nothing is made up: an app with no
Flathub entry gets no picture (the Store draws its initial instead).

Everything is cached on disk (metadata for a week, images until they change), so the Store opens instantly
after the first visit and works offline with what it has seen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, Optional

log = logging.getLogger("saint.store_art")

APPSTREAM = "https://flathub.org/api/v2/appstream/{id}"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{3,128}$")
_SAFE_HOSTS = ("https://dl.flathub.org/", "https://flathub.org/")
META_TTL = 7 * 24 * 3600


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (os.path.join(os.environ.get("LOCALAPPDATA", ""), "SAINT", "cache")
                                                if os.name == "nt" else os.path.expanduser("~/.cache"))
    d = Path(base) / ("saint/store" if os.name != "nt" else "store")
    d.mkdir(parents=True, exist_ok=True)
    return d


def flathub_id(result, catalog=None) -> Optional[str]:
    """The Flathub id whose artwork shows this Store result: the Flatpak itself, or a pick's ``art`` id."""
    if result.source == "flathub":
        return result.id
    if result.source == "featured":
        extra = result.extra or {}
        if extra.get("art"):
            return extra["art"]
        if extra.get("method") == "flatpak":
            return extra.get("ref")
    return None


def _pick_screenshot(shots, want_width: int = 752) -> Optional[str]:
    """The screenshot size closest to ``want_width`` from the first screenshot."""
    if not shots:
        return None
    sizes = shots[0].get("sizes", []) if isinstance(shots[0], dict) else []
    best = None
    for s in sizes:
        try:
            w = int(s.get("width", 0))
        except (TypeError, ValueError):
            continue
        if best is None or abs(w - want_width) < abs(best[0] - want_width):
            best = (w, s.get("src"))
    return best[1] if best else None


def parse_appstream(data: dict) -> Dict:
    brand = {}
    for b in data.get("branding") or []:
        if b.get("type") == "primary" and b.get("value"):
            brand[b.get("scheme_preference") or "any"] = b["value"]
    return {"icon": data.get("icon"), "screenshot": _pick_screenshot(data.get("screenshots") or []),
            "developer": data.get("developer_name") or "", "summary": data.get("summary") or "",
            "brand_dark": brand.get("dark") or brand.get("any"), "brand_light": brand.get("light") or brand.get("any")}


def metadata(app_id: str, fetch: Optional[Callable[[str], dict]] = None, now: Optional[float] = None) -> Dict:
    """Flathub's facts about one app ({} when it isn't on Flathub or the network is down and nothing is cached)."""
    if not app_id or not _SAFE_ID.match(app_id):
        return {}
    path = cache_dir() / f"{app_id}.json"
    now = now if now is not None else time.time()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if now - cached.get("_at", 0) < META_TTL:
            return cached
    except (OSError, ValueError):
        cached = {}
    try:
        meta = parse_appstream((fetch or _get_json)(APPSTREAM.format(id=app_id)) or {})
    except Exception as e:
        log.info("store_art.metadata_unavailable %s: %s", app_id, e)
        return cached                                 # stale beats nothing
    meta["_at"] = now
    try:
        path.write_text(json.dumps(meta), encoding="utf-8")
    except OSError:
        pass
    return meta


def image(url: str, fetch: Optional[Callable[[str], bytes]] = None) -> Optional[str]:
    """A local copy of a Flathub image (icon/screenshot); None if it can't be had."""
    if not url or not url.startswith(_SAFE_HOSTS):
        return None
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    path = cache_dir() / (hashlib.sha1(url.encode()).hexdigest() + (ext if ext in (".png", ".jpg", ".jpeg", ".webp")
                                                                     else ".img"))
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    try:
        data = (fetch or _get_bytes)(url)
    except Exception as e:
        log.info("store_art.image_unavailable %s: %s", url, e)
        return None
    if not data or len(data) > 8 * 2 ** 20:
        return None
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return str(path)


def _get_json(url: str):
    import requests
    r = requests.get(url, timeout=10, headers={"User-Agent": "SAINT-Store/1"})
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json()


def _get_bytes(url: str) -> bytes:
    import requests
    r = requests.get(url, timeout=15, headers={"User-Agent": "SAINT-Store/1"})
    r.raise_for_status()
    return r.content
