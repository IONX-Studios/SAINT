"""SAINT Store (core/store.py) and the Windows app agent (core/winapps.py). Network and package tools
are replaced by fixtures; the commands built are checked exactly."""

import sqlite3

import pytest

from core import store, winapps


class Run:
    def __init__(self, out=""):
        self.out = out
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return type("R", (), {"stdout": self.out, "returncode": 0})()


PACMAN_SS = """extra/blender 17:4.5.3-1 [installed]
    A fully integrated 3D graphics creation suite
extra/blender-plugin-x 1.0-1
    Something with a hostile name
"""


def test_repo_search_parses_pacman(monkeypatch):
    hits = store.search_repo("blender", run=Run(PACMAN_SS))
    assert [(h.id, h.version, h.installed) for h in hits] == [("blender", "17:4.5.3-1", True), ("blender-plugin-x", "1.0-1", False)]
    assert hits[0].summary.startswith("A fully integrated")


def test_aur_search_ranks_by_popularity_and_drops_unsafe_names():
    data = {"results": [
        {"Name": "visual-studio-code-insiders-bin", "Version": "1", "NumVotes": 50, "Popularity": 0.5},
        {"Name": "visual-studio-code-bin", "Version": "1.105", "Description": "VS Code (Microsoft build)",
         "NumVotes": 1400, "Popularity": 22.1, "OutOfDate": None, "Maintainer": "dcelasun"},
        {"Name": "--overwrite=*", "Version": "1", "NumVotes": 9999, "Popularity": 99},
    ]}
    hits = store.search_aur("code", fetch=lambda url: data)
    assert [h.id for h in hits] == ["visual-studio-code-bin", "visual-studio-code-insiders-bin"]
    assert hits[0].extra["url"] == "https://aur.archlinux.org/packages/visual-studio-code-bin"
    assert store.search_aur("code", fetch=lambda url: (_ for _ in ()).throw(OSError("offline"))) == []


def test_search_puts_exact_names_and_saints_picks_first():
    flathub = {"hits": [{"app_id": "org.blender.Blender", "name": "Blender", "summary": "3D"},
                        {"app_id": "bad id; rm", "name": "x"}]}
    res = store.search("Blender", run=Run(PACMAN_SS), fetch=lambda u: {"results": []},
                       post=lambda u, b: flathub, windows=lambda q: [])
    assert res[0].source == "featured" and res[0].id == "blender"
    assert not any(r.source == "flathub" and r.id == "org.blender.Blender" for r in res)   # the pick shows once
    assert {r.source for r in res} >= {"featured", "repo"}
    with pytest.raises(ValueError):
        store.search("--help")


def test_store_install_commands():
    s = store.plan(store.Result("repo", "blender", "Blender"))
    assert s[0].argv == ["pkexec", "pacman", "-Syu", "--needed", "--noconfirm", "blender"]
    s = store.plan(store.Result("aur", "visual-studio-code-bin", "VS Code"))
    assert s[0].argv == ["paru", "-S", "--needed", "--noconfirm", "--skipreview", "--sudo", "pkexec", "visual-studio-code-bin"]
    s = store.plan(store.Result("flathub", "org.vinegarhq.Sober", "Sober"))
    assert s[-1].argv[-2:] == ["flathub", "org.vinegarhq.Sober"] and s[-1].argv[2] == "--user"
    s = store.plan(store.Result("featured", "roblox-studio", "Roblox Studio"))
    assert s[-1].argv[-1] == "org.vinegarhq.Vinegar"
    for bad in (store.Result("repo", "--root=/", "x"), store.Result("aur", "a;b", "x"), store.Result("flathub", "x", "x")):
        with pytest.raises(ValueError):
            store.plan(bad)


# --------------------------------------------------------------------------- Windows apps
NPP = {
    "PackageIdentifier": "Notepad++.Notepad++",
    "Installers": [
        {"Architecture": "arm64", "InstallerType": "nullsoft", "InstallerUrl": "https://example.org/npp.arm64.exe",
         "InstallerSha256": "A" * 64},
        {"Architecture": "x86", "InstallerType": "nullsoft", "InstallerUrl": "https://example.org/npp.exe",
         "InstallerSha256": "B" * 64},
        {"Architecture": "x64", "InstallerType": "nullsoft", "InstallerUrl": "https://example.org/npp.x64.exe",
         "InstallerSha256": "C" * 64},
    ],
}


def _index(tmp_path):
    db = tmp_path / "index.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table ids(rowid integer primary key, id text); create table names(rowid integer primary key, name text);
        create table versions(rowid integer primary key, version text);
        create table pathparts(rowid integer primary key, parent int, pathpart text);
        create table manifest(rowid integer primary key, id int, name int, moniker int, version int, channel int, pathpart int);
        insert into ids values (1, 'Notepad++.Notepad++'), (2, 'RiotGames.Valorant.EU');
        insert into names values (1, 'Notepad++'), (2, 'Valorant');
        insert into versions values (1, '8.8.9'), (2, '8.10'), (3, '1.0');
        insert into pathparts values (1, null, 'manifests'), (2, 1, 'n'), (3, 2, 'Notepad++'), (4, 3, 'Notepad++'),
                                     (5, 4, '8.10'), (6, 5, '2035'), (7, 4, '8.8.9'), (8, 7, '1999');
        insert into manifest values (1, 1, 1, 0, 1, 0, 8), (2, 1, 1, 0, 2, 0, 6), (3, 2, 2, 0, 3, 0, 1);
    """)
    con.commit()
    con.close()
    return db


def test_windows_search_takes_the_newest_version_and_marks_blocked_apps(tmp_path):
    db = _index(tmp_path)
    hits = winapps.search("notepad++", db=db)
    assert [(h.id, h.version, h.extra["path"]) for h in hits] == [
        ("Notepad++.Notepad++", "8.10", "manifests/n/Notepad++/Notepad++/8.10/2035")]
    val = winapps.search("valorant", db=db)[0]
    assert "kernel driver" in val.extra["blocked"]


def test_windows_install_plan_is_verified_and_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(winapps, "PREFIXES", tmp_path / "wine")
    monkeypatch.setattr(winapps, "CACHE", tmp_path / "cache")
    r = store.Result("windows", "Notepad++.Notepad++", "Notepad++", extra={"path": "manifests/n/x/1/2"})
    steps = winapps.plan(r, man=NPP, which=lambda t: None, python="py")
    assert steps[0].argv == ["pkexec", "pacman", "-Syu", "--needed", "--noconfirm", "wine", "wine-mono", "wine-gecko"]
    fetch = steps[1].argv
    assert fetch[0] == "py" and fetch[2:5] == ["fetch", "https://example.org/npp.x64.exe", "c" * 64]   # x64 first
    inst = steps[2].argv
    assert inst[:3] == ["env", f"WINEPREFIX={tmp_path / 'wine' / 'Notepad__.Notepad__'}", "WINEDEBUG=-all"]
    assert inst[3] == "wine" and inst[-1] == "/S"
    assert len(winapps.plan(r, man=NPP, which=lambda t: "/usr/bin/wine", python="py")) == 2   # Wine already there


def test_windows_installers_wine_cant_run_are_refused_with_reasons():
    assert winapps.pick_installer({"Installers": [{"Architecture": "x64", "InstallerType": "inno",
                                                   "InstallerUrl": "https://e/x.exe", "InstallerSha256": "D" * 64}]})
    for bad in ({"Installers": [{"Architecture": "x64", "InstallerType": "msix", "InstallerUrl": "https://e/x.msix",
                                 "InstallerSha256": "D" * 64}]},
                {"Installers": [{"Architecture": "x64", "InstallerType": "exe", "InstallerUrl": "https://e/x.exe",
                                 "InstallerSha256": "D" * 64}]},                       # no silent switch known
                {"Installers": [{"Architecture": "x64", "InstallerType": "nullsoft", "InstallerUrl": "http://e/x.exe",
                                 "InstallerSha256": "D" * 64}]}):                      # not https
        with pytest.raises(ValueError):
            winapps.pick_installer(bad)
    exe = {"Installers": [{"Architecture": "x64", "InstallerType": "exe", "InstallerUrl": "https://e/x.exe",
                           "InstallerSha256": "D" * 64, "InstallerSwitches": {"Silent": "/quiet & calc"}}]}
    with pytest.raises(ValueError):
        winapps.pick_installer(exe)                                                    # unsafe switches
    assert winapps.blocked("Roblox.Roblox")[1] == "roblox"
    assert winapps.blocked("Valve.Steam")[1] == "steam"
    assert winapps.blocked("Intel.ChipsetDriver", "Intel Chipset Driver")
    assert winapps.blocked("Notepad++.Notepad++", "Notepad++") is None
    r = store.Result("windows", "Roblox.Roblox", "Roblox", extra={"path": "manifests/r/x"})
    with pytest.raises(ValueError, match="Hyperion"):
        winapps.plan(r, man=NPP)


def test_download_refuses_a_wrong_hash(tmp_path, monkeypatch):
    import hashlib
    import requests

    class Resp:
        headers = {"content-length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b"hello"
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    dest = tmp_path / "x.exe"
    assert winapps.fetch("https://e/x.exe", "0" * 64, str(dest)) == 1 and not dest.exists()
    assert winapps.fetch("https://e/x.exe", hashlib.sha256(b"hello").hexdigest(), str(dest)) == 0
    assert dest.read_bytes() == b"hello"
    assert winapps.fetch("http://e/x.exe", "0" * 64, str(dest)) == 2
