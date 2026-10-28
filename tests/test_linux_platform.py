"""
SAINTOS / Linux port: platform detection, XDG data paths, desktop-entry app
discovery, the read-only preflight tool and the Linux dependency manifest.

These run on any OS: the Linux parts are exercised with injected environments,
fake `which` results and fixture .desktop files (mocked — they don't prove a
real Fedora desktop works; tests marked `real` below touch this machine).
"""

import os
import types
from pathlib import Path, PurePosixPath

import pytest

from core import paths, platform_caps
from modules.desktop import apps
from tools import preflight

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# core/paths.py
# --------------------------------------------------------------------------- #
class _FakeOs:
    def __init__(self, name, environ):
        self.name, self.environ, self.path = name, environ, os.path


def test_linux_user_data_follows_xdg(monkeypatch):
    monkeypatch.setattr(paths, "os", _FakeOs("posix", {"XDG_DATA_HOME": "/home/me/.xdg"}))
    assert Path(paths._local_app_data()).as_posix().endswith("/home/me/.xdg")
    assert paths.installed_exe() is None                   # there is no SAINT.exe on Linux


def test_linux_user_data_defaults_to_local_share(monkeypatch):
    monkeypatch.setattr(paths, "os", _FakeOs("posix", {}))
    assert Path(paths._local_app_data()).as_posix().endswith("/.local/share")


def test_windows_user_data_unchanged(monkeypatch):
    monkeypatch.setattr(paths, "os", _FakeOs("nt", {"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}))
    assert str(paths._local_app_data()).replace("\\", "/").endswith("Users/me/AppData/Local")


@pytest.mark.parametrize("root,expected", [("/usr/share/saint", True), ("/opt/saint", True),
                                           ("/home/me/SAINT", False)])
def test_system_install_detection(monkeypatch, root, expected):
    monkeypatch.setattr(paths, "os", _FakeOs("posix", {}))
    monkeypatch.setattr(paths, "FROZEN", False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", PurePosixPath(root))
    assert paths.system_install() is expected


def test_system_install_never_on_windows(monkeypatch):
    monkeypatch.setattr(paths, "os", _FakeOs("nt", {}))
    monkeypatch.setattr(paths, "PROJECT_ROOT", PurePosixPath("/usr/share/saint"))
    assert paths.system_install() is False


def test_test_suite_data_dir_still_wins():
    assert str(paths.base_data_dir()) == os.environ["SAINT_DATA_DIR"]


# --------------------------------------------------------------------------- #
# modules/desktop/apps.py — XDG desktop entries
# --------------------------------------------------------------------------- #
def _entry(folder: Path, name: str, body: str) -> Path:
    p = folder / name
    p.write_text("[Desktop Entry]\n" + body, encoding="utf-8")
    return p


@pytest.fixture
def app_dir(tmp_path):
    d = tmp_path / "applications"
    (d / "kde").mkdir(parents=True)
    _entry(d, "org.kde.dolphin.desktop", "Type=Application\nName=Dolphin\nName[de]=Dolphin\n"
           "Exec=dolphin %u\nStartupWMClass=dolphin\n")
    _entry(d, "com.spotify.Client.desktop", "Type=Application\nName=Spotify\n"
           "Exec=/usr/bin/flatpak run --branch=stable --command=spotify com.spotify.Client %U\n")
    _entry(d, "hidden.desktop", "Type=Application\nName=Hidden Helper\nExec=helper\nNoDisplay=true\n")
    _entry(d, "link.desktop", "Type=Link\nName=A Website\nURL=https://example.org\n")
    _entry(d, "noexec.desktop", "Type=Application\nName=Broken\n")
    _entry(d, "uninstall.desktop", "Type=Application\nName=Uninstall Thing\nExec=rm-thing\n")
    _entry(d / "kde", "org.kde.kate.desktop", "Type=Application\nName=Kate\nExec=kate -b %U\n"
           "Comment=50% faster; a value with % and = signs\n")
    (d / "garbage.desktop").write_bytes(b"\xff\xfe not an ini file [[[")
    return d


def test_desktop_entry_parsing(app_dir):
    dolphin = apps.read_desktop_entry(str(app_dir / "org.kde.dolphin.desktop"))
    assert (dolphin.name, dolphin.kind, dolphin.process_hint) == ("Dolphin", "desktop", "dolphin")
    spotify = apps.read_desktop_entry(str(app_dir / "com.spotify.Client.desktop"))
    assert spotify.process_hint == "client"            # flatpak id's last part (no StartupWMClass given)
    kate = apps.read_desktop_entry(str(app_dir / "kde" / "org.kde.kate.desktop"))
    assert kate.process_hint == "kate"
    for skipped in ("hidden.desktop", "link.desktop", "noexec.desktop", "garbage.desktop"):
        assert apps.read_desktop_entry(str(app_dir / skipped)) is None, skipped


def test_scan_desktop_entries_and_resolve(app_dir, monkeypatch):
    cat = apps.AppCatalog()
    entries = {}
    cat._scan_desktop_entries(entries, dirs=[str(app_dir), str(app_dir / "missing")])
    assert set(entries) == {"dolphin", "spotify", "kate"}  # uninstallers, hidden and broken are left out
    cat._entries = entries
    monkeypatch.setattr(apps, "_BUILTIN", apps._BUILTIN_LINUX)
    assert cat.resolve("open kate".split(" ", 1)[1]).target.endswith("org.kde.kate.desktop")
    assert cat.resolve("spotify").kind == "desktop"
    assert cat.resolve("terminal").target == "konsole"   # KDE built-in


def test_user_entries_override_system_ones(tmp_path):
    user, system = tmp_path / "user", tmp_path / "system"
    user.mkdir(), system.mkdir()
    _entry(user, "kate.desktop", "Type=Application\nName=Kate\nExec=/home/me/bin/kate\n")
    _entry(system, "kate.desktop", "Type=Application\nName=Kate\nExec=kate\n")
    entries = {}
    apps.AppCatalog()._scan_desktop_entries(entries, dirs=[str(user), str(system)])
    assert entries["kate"].target.startswith(str(user))


def test_xdg_application_dirs(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/home/me/.local/share")
    monkeypatch.setenv("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    dirs = [d.replace("\\", "/") for d in apps._xdg_application_dirs()]
    assert dirs[0] == "/home/me/.local/share/applications"
    assert "/usr/share/applications" in dirs and "/var/lib/flatpak/exports/share/applications" in dirs


def test_linux_launch_never_uses_a_shell(monkeypatch):
    have = {"gio", "xdg-open"}
    monkeypatch.setattr(apps.shutil, "which", lambda n: f"/usr/bin/{n}" if n in have else None)
    desk = apps.AppEntry("Kate", "desktop", "/usr/share/applications/org.kde.kate.desktop", "desktop_entry")
    assert apps._linux_launch_command(desk) == ["gio", "launch", desk.target]
    assert apps._linux_launch_command(apps.AppEntry("x", "uri", "steam://open/games", "custom")) == \
        ["xdg-open", "steam://open/games"]
    assert apps._linux_launch_command(apps.AppEntry("k", "exe", "konsole", "builtin")) == ["konsole"]
    have.clear()
    have.add("kioclient")
    assert apps._linux_launch_command(desk)[:2] == ["kioclient", "exec"]
    have.clear()
    with pytest.raises(FileNotFoundError):
        apps._linux_launch_command(desk)


def test_windows_builtins_unchanged_on_windows():
    if os.name == "nt":
        assert apps._BUILTIN is apps._BUILTIN_WINDOWS
        assert apps._BUILTIN["notepad"] == ("exe", "notepad.exe", "notepad")


# --------------------------------------------------------------------------- #
# core/platform_caps.py
# --------------------------------------------------------------------------- #
FEDORA = {"ID": "fedora", "VERSION_ID": "44"}


def test_detect_kde_wayland():
    s = platform_caps.detect_session({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"},
                                     "linux", FEDORA)
    assert (s.os, s.display, s.kde, s.distro, s.distro_version) == ("linux", "wayland", True, "fedora", "44")


def test_detect_display_from_sockets_when_session_type_missing():
    s = platform_caps.detect_session({"WAYLAND_DISPLAY": "wayland-0"}, "linux", {})
    assert s.display == "wayland"
    assert platform_caps.detect_session({"DISPLAY": ":0"}, "linux", {}).display == "x11"
    assert platform_caps.detect_session({}, "linux", {}).display == ""


def test_detect_windows():
    assert platform_caps.detect_session({}, "win32").os == "windows"


_ALL_TOOLS = {"qdbus6", "ydotool", "spectacle", "wl-copy", "notify-send", "wpctl", "playerctl",
              "brightnessctl", "systemctl", "gio", "xdg-open", "tesseract"}


def _caps(session, tools, modules=("keyring", "secretstorage", "pyatspi")):
    return {c.name: c for c in platform_caps.linux_capabilities(
        session, which=lambda n: n if n in tools else None, has_module=lambda m: m in modules,
        exists=lambda p: True)}


def test_complete_fedora_kde_reports_everything_available():
    s = platform_caps.Session("linux", "KDE", "wayland", "fedora", "44")
    caps = _caps(s, _ALL_TOOLS)
    missing = [n for n, c in caps.items() if not c.available]
    assert missing == []
    assert "KWin" in caps["windows"].provider and caps["input"].provider == "ydotool"


def test_missing_tools_are_explained_not_hidden():
    s = platform_caps.Session("linux", "KDE", "wayland", "fedora", "44")
    caps = _caps(s, set(), modules=())
    for name, c in caps.items():
        assert not c.available, name
        assert c.hint, f"{name} is unavailable without saying why"
    assert "ydotool" in caps["input"].hint and "tesseract" in caps["ocr"].hint


def test_wayland_without_kde_has_no_window_list():
    s = platform_caps.Session("linux", "GNOME", "wayland", "fedora", "44")
    assert not _caps(s, _ALL_TOOLS)["windows"].available


def test_x11_uses_x_tools():
    s = platform_caps.Session("linux", "KDE", "x11", "fedora", "44")
    caps = _caps(s, {"wmctrl", "xdotool", "xclip"})
    assert caps["windows"].provider == "wmctrl" and caps["input"].provider == "xdotool"
    assert caps["clipboard"].provider == "xclip"


@pytest.mark.real
def test_capabilities_on_this_machine_dont_raise():
    caps = platform_caps.capabilities()
    assert caps and all(isinstance(c.available, bool) for c in caps)


# --------------------------------------------------------------------------- #
# tools/preflight.py — read-only by construction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd", [["mkfs.ext4", "/dev/sda1"], ["diskpart"], ["parted", "/dev/nvme0n1"],
                                 ["dd", "if=/dev/zero"], ["efibootmgr", "-o", "0001"], ["rm", "-rf", "/"]])
def test_preflight_refuses_anything_not_read_only(cmd):
    with pytest.raises(PermissionError):
        preflight._run(cmd)


@pytest.mark.parametrize("script", ["Resize-Partition -DriveLetter C -Size 100GB", "Clear-Disk -Number 0",
                                    "Get-Disk; Initialize-Disk 1"])
def test_preflight_refuses_disk_changing_powershell(script):
    with pytest.raises(PermissionError):
        preflight._run(["powershell", "-Command", script])


def test_preflight_source_has_no_destructive_calls():
    src = (ROOT / "tools" / "preflight.py").read_text(encoding="utf-8")
    body = src.split("_FORBIDDEN_WORDS = {", 1)[1].split("}", 1)[1]     # everything after the deny-list
    for word in ("Resize-Partition", "Clear-Disk", "New-Partition", "mkfs", "bcdedit", "efibootmgr"):
        assert word not in body


def test_preflight_markdown_labels_unknowns():
    md = preflight.to_markdown({"platform": "linux", "boot": {"mode": "UEFI",
                                                             "secure_boot": preflight.unknown("mokutil missing")},
                                "gpu": preflight.unknown("lspci missing")})
    assert "**unknown** — mokutil missing" in md and "| (all) | **unknown** — lspci missing |" in md
    assert "Nothing on this machine was changed" in md


@pytest.mark.real
def test_preflight_collects_on_this_machine():
    report = preflight.collect()
    assert {"machine", "cpu_ram", "storage", "boot", "saint"} <= set(report)
    assert isinstance(report["saint"]["free_space_home_gb"], float)


# --------------------------------------------------------------------------- #
# Dependency manifests
# --------------------------------------------------------------------------- #
_WINDOWS_ONLY = ("pywin32", "uiautomation", "pycaw", "winrt-", "pyautogui")


def test_windows_only_requirements_are_marked():
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.lower().startswith(_WINDOWS_ONLY):
            assert 'sys_platform == "win32"' in line, line


def test_linux_requirements_include_the_shared_ones():
    text = (ROOT / "requirements-linux.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in text and "SecretStorage" in text


def test_linux_scripts_never_touch_disks_or_boot():
    banned = ("mkfs", "parted ", "fdisk", "wipefs", "efibootmgr", "grub2-install", "dd if=", "mokutil --",
              "bootctl install", "diskpart", "bcdedit")
    for script in list((ROOT / "packaging" / "linux").glob("*.sh")) + [ROOT / "run.sh"]:
        text = "\n".join(l for l in script.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
        for word in banned:
            assert word not in text, f"{script.name} mentions {word!r}"


# --------------------------------------------------------------------------- #
# modules/steam/library.py
# --------------------------------------------------------------------------- #
def test_steam_found_in_linux_locations(monkeypatch, tmp_path):
    from modules.steam import library
    import builtins
    real_import = builtins.__import__

    def no_winreg(name, *a, **kw):
        if name == "winreg":
            raise ModuleNotFoundError("No module named 'winreg'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_winreg)
    monkeypatch.setattr(library, "os", types.SimpleNamespace(name="posix", environ={"XDG_DATA_HOME": str(tmp_path)},
                                                              path=os.path))
    assert library.steam_path() is None                    # no Steam: None, not an ImportError
    (tmp_path / "Steam").mkdir()
    assert library.steam_path() == str(tmp_path / "Steam")
