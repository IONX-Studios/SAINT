"""Store artwork (core/store_art.py): Flathub's own metadata, cached; nothing invented, nothing off-site."""

import json

from core import store_art
from core.store import Result

APPSTREAM = {"icon": "https://dl.flathub.org/media/x/icons/128x128/a.png", "developer_name": "Blender Foundation",
             "summary": "3D creation", "branding": [{"type": "primary", "value": "#ffd5c2", "scheme_preference": "light"},
                                                     {"type": "primary", "value": "#3a1b0a", "scheme_preference": "dark"}],
             "screenshots": [{"sizes": [{"src": "https://dl.flathub.org/s_2000.png", "width": "2000"},
                                        {"src": "https://dl.flathub.org/s_752.png", "width": "752"},
                                        {"src": "https://dl.flathub.org/s_224.png", "width": "224"}]}]}


def test_parse_appstream_keeps_real_fields():
    m = store_art.parse_appstream(APPSTREAM)
    assert m == {"icon": APPSTREAM["icon"], "screenshot": "https://dl.flathub.org/s_752.png",
                 "developer": "Blender Foundation", "summary": "3D creation",
                 "brand_dark": "#3a1b0a", "brand_light": "#ffd5c2"}
    assert store_art.parse_appstream({})["icon"] is None


def test_flathub_id_for_each_kind_of_result():
    assert store_art.flathub_id(Result("flathub", "org.blender.Blender", "Blender")) == "org.blender.Blender"
    assert store_art.flathub_id(Result("featured", "obs", "OBS", extra={"method": "flatpak",
                                                                        "ref": "com.obsproject.Studio"})) \
        == "com.obsproject.Studio"
    assert store_art.flathub_id(Result("featured", "vscode", "VS Code", extra={"method": "aur", "ref": "x",
                                                                                "art": "com.visualstudio.code"})) \
        == "com.visualstudio.code"
    assert store_art.flathub_id(Result("aur", "blender-git", "blender-git")) is None


def test_metadata_is_cached_and_stale_cache_beats_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = []
    m = store_art.metadata("org.blender.Blender", fetch=lambda u: calls.append(u) or APPSTREAM, now=1000)
    assert m["developer"] == "Blender Foundation" and len(calls) == 1
    store_art.metadata("org.blender.Blender", fetch=lambda u: calls.append(u) or APPSTREAM, now=2000)
    assert len(calls) == 1                                   # fresh cache: no second request

    def down(_u):
        raise ConnectionError("offline")
    m = store_art.metadata("org.blender.Blender", fetch=down, now=1000 + store_art.META_TTL + 1)
    assert m["developer"] == "Blender Foundation"            # expired, but offline: keep what we had
    assert store_art.metadata("bad id/../x", fetch=lambda u: APPSTREAM) == {}


def test_images_only_from_flathub_and_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    got = []
    path = store_art.image("https://dl.flathub.org/a.png", fetch=lambda u: got.append(u) or b"\x89PNG data")
    assert path and open(path, "rb").read() == b"\x89PNG data"
    assert store_art.image("https://dl.flathub.org/a.png", fetch=lambda u: got.append(u) or b"x") == path
    assert len(got) == 1
    assert store_art.image("https://evil.example/a.png", fetch=lambda u: b"x") is None
    assert store_art.image("http://dl.flathub.org/a.png", fetch=lambda u: b"x") is None


def test_catalog_art_ids_are_valid():
    data = json.load(open("packaging/linux/apps/catalog.json", encoding="utf-8"))
    for a in data["apps"]:
        if a.get("art"):
            assert store_art._SAFE_ID.match(a["art"])
