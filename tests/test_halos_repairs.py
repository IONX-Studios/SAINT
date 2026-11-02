"""Halo OS repairs (2026-11-02): install failure reasons, honest storage numbers, the Halo's geometry,
the Alt+` default and the Kokoro voice shipped in the image."""

import json
import os
from collections import namedtuple
from pathlib import Path

import pytest

from core import saintos_apps as sa
from modules.files import scan

ROOT = Path(__file__).resolve().parents[1]
Part = namedtuple("Part", "device mountpoint fstype opts")
Usage = namedtuple("Usage", "total used free percent")
Stat = namedtuple("Stat", "st_dev")


# --------------------------------------------------------------------------- install failures
@pytest.mark.parametrize("lines, live, expect", [
    (["error: Failed to install org.gnome.Platform: No space left on device"], True, "disk space (the live session"),
    (["error: Unable to connect to dl.flathub.org: Could not resolve hostname"], False, "network"),
    (["error: No remote refs found for ‘com.heroicgameslauncher.hgl’"], False, "doesn't have an app"),
    (["Error executing command as another user: Request dismissed"], False, "password prompt"),
    (["error: failed to init transaction (unable to lock database)"], False, "Another install"),
    (["something odd happened", ""], False, "flatpak failed: something odd happened"),
    ([], False, "stopped without saying why"),
])
def test_explain_failure(lines, live, expect):
    assert expect in sa.explain_failure(lines, ["flatpak", "install"], live=live)


class _Proc:
    def __init__(self, lines, code):
        self.stdout, self._code = iter(lines), code

    def wait(self):
        return self._code


def test_run_plan_reports_why_each_app_failed():
    steps = [sa.Step("Flathub", ["flatpak", "remote-add"], []),
             sa.Step("Heroic", ["flatpak", "install", "com.heroicgameslauncher.hgl"], ["heroic"]),
             sa.Step("VLC", ["flatpak", "install", "org.videolan.VLC"], ["vlc"])]
    outputs = {"remote-add": (["ok"], 0), "com.heroicgameslauncher.hgl": (["No space left on device"], 1),
               "org.videolan.VLC": (["Installing… 100%"], 0)}

    def popen(argv, **_kw):
        return _Proc(*outputs[argv[-1]])
    errors, lines = {}, []
    result = sa.run_plan(steps, on_line=lines.append, popen=popen, errors=errors, live=False)
    assert result == {"heroic": False, "vlc": True}
    assert errors == {"heroic": "Not enough disk space."}
    assert any(l.startswith("    FAILED: Not enough disk space") for l in lines)
    title, body = sa.summary(result, {}, names={"heroic": "Heroic Games Launcher", "vlc": "VLC"}, errors=errors)
    assert title == "Installed 1, 1 failed"
    assert "✗ Heroic Games Launcher (Not enough disk space.)" in body


def test_run_plan_missing_tool_is_named():
    def popen(argv, **_kw):
        raise FileNotFoundError(argv[0])
    errors = {}
    sa.run_plan([sa.Step("X", ["flatpak", "install", "x.y.Z"], ["z"])], on_line=lambda _l: None, popen=popen,
                errors=errors, live=False)
    assert errors == {"z": "flatpak isn't installed on this system."}


def test_a_failed_shared_step_explains_the_apps_after_it():
    steps = [sa.Step("Flathub", ["flatpak", "remote-add"], []),
             sa.Step("Heroic", ["flatpak", "install", "h.h.H"], ["heroic"])]
    outputs = {"remote-add": (["Could not resolve hostname dl.flathub.org"], 1), "h.h.H": (["error: no remote"], 1)}
    errors = {}
    sa.run_plan(steps, on_line=lambda _l: None, popen=lambda argv, **k: _Proc(*outputs[argv[-1]]), errors=errors,
                live=False)
    assert "network" in errors["heroic"]


# --------------------------------------------------------------------------- storage
def test_linux_filesystems_counts_each_filesystem_once_and_skips_images():
    parts = [Part("/dev/nvme0n1p2", "/", "btrfs", ""), Part("/dev/nvme0n1p2", "/home", "btrfs", ""),
             Part("/dev/nvme0n1p2", "/var/log", "btrfs", ""), Part("/dev/nvme0n1p1", "/boot/efi", "vfat", ""),
             Part("/dev/sr0", "/run/archiso/bootmnt", "iso9660", ""), Part("/dev/loop0", "/run/archiso/airootfs",
                                                                          "erofs", ""),
             Part("tmpfs", "/tmp", "tmpfs", ""), Part("/dev/sdb1", "/run/media/me/USB", "exfat", ""),
             Part("nas:/share", "/mnt/nas", "nfs4", "")]
    devs = {"/": 1, "/home": 1, "/var/log": 1, "/boot/efi": 2, "/run/media/me/USB": 3, "/mnt/nas": 4}
    sizes = {"/": Usage(500, 200, 275, 42.1), "/boot/efi": Usage(1, 0.2, 0.8, 20.0),
             "/run/media/me/USB": Usage(64, 10, 54, 15.6), "/mnt/nas": Usage(4000, 1000, 3000, 25.0)}
    sizes["/home"] = sizes["/var/log"] = sizes["/"]
    out = scan.linux_filesystems(parts, usage=lambda m: sizes[m], stat=lambda m: Stat(devs[m]))
    assert [d["drive"] for d in out] == ["/", "/boot/efi", "/run/media/me/USB", "/mnt/nas"]
    root = out[0]
    assert root["name"] == "System" and root["reserved"] == 25 and root["kind"] == "internal"
    assert out[2]["kind"] == "removable" and out[3]["kind"] == "network"


def test_live_session_root_is_labelled_as_memory():
    out = scan.linux_filesystems([Part("airootfs", "/", "overlay", "")], usage=lambda m: Usage(4, 1, 3, 25.0),
                                 stat=lambda m: Stat(9))
    assert out[0]["name"] == "Live session" and out[0]["kind"] == "live" and "in memory" in out[0]["label"]


def test_disks_reports_unallocated_space_separately():
    gib = 1024 ** 3
    lsblk = {"blockdevices": [
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "size": 1000 * gib, "type": "disk", "model": "Fast SSD ",
         "rm": False, "tran": "nvme", "children": [{"name": "p1", "size": 1 * gib, "type": "part"},
                                                    {"name": "p2", "size": 600 * gib, "type": "part"}]},
        {"name": "sda", "path": "/dev/sda", "size": 64 * gib, "type": "disk", "model": "USB", "rm": True,
         "children": [{"name": "sda1", "size": 64 * gib - 1024 ** 2, "type": "part"}]},
        {"name": "loop0", "size": gib, "type": "loop"}, {"name": "zram0", "size": gib, "type": "disk"}]}
    out = scan.disks(run=lambda argv: json.dumps(lsblk))
    assert [d["path"] for d in out] == ["/dev/nvme0n1", "/dev/sda"]
    assert out[0]["unallocated"] == 399 * gib and out[0]["model"] == "Fast SSD"
    assert out[1]["unallocated"] == 0                       # 1 MiB of GPT/alignment isn't free space


# --------------------------------------------------------------------------- Halo geometry
def test_halo_edges_frame_the_screen_and_never_cross_the_middle():
    pytest.importorskip("PySide6")
    from ui.halo import THICK, edge_rects
    w, h = 1920, 1080
    r = edge_rects(w, h)
    assert r["top"].width() == w and r["top"].height() == THICK and r["top"].y() == 0
    assert r["bottom"].bottom() == h - 1 and r["right"].right() == w - 1 and r["left"].x() == 0
    centre = (w // 2, h // 2)
    assert not any(rect.contains(*centre) for rect in r.values())
    covered = sum(rect.width() * rect.height() for rect in r.values())
    assert covered == 2 * w * THICK + 2 * THICK * (h - 2 * THICK)          # no overlap between bands


# --------------------------------------------------------------------------- image defaults
def test_alt_grave_is_released_from_kwin_in_the_image():
    pkgbuild = (ROOT / "packaging/arch/pkgbuilds/saint/PKGBUILD").read_text(encoding="utf-8")
    assert "Walk Through Windows of Current Application=none,Alt+`" in pkgbuild
    assert 'etc/xdg/kglobalshortcutsrc' in pkgbuild


def test_kokoro_voice_ships_with_pinned_checksums():
    pkgbuild = (ROOT / "packaging/arch/pkgbuilds/saint/PKGBUILD").read_text(encoding="utf-8")
    assert "kokoro-v1.0.onnx" in pkgbuild and "voices-v1.0.bin" in pkgbuild
    assert "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5" in pkgbuild
    assert 'app/data/tts/kokoro-onnx' in pkgbuild and "test_kokoro(play=False, download=False)" in pkgbuild


def test_live_session_has_room_for_app_installs():
    for f in ["packaging/arch/profile/efiboot/loader/entries/01-halo.conf", "packaging/arch/profile/syslinux/syslinux.cfg"]:
        assert "cow_spacesize=50%" in (ROOT / f).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- health checks
def test_ollama_reachable_without_the_model_is_not_working():
    from core import health
    tags = {"models": [{"name": "llama3.2:3b"}]}
    assert health.ollama("http://x", "llama3.2:3b", get=lambda u: tags)["state"] == health.WORKING
    assert health.ollama("http://x", "llama3.2", get=lambda u: tags)["state"] == health.WORKING
    r = health.ollama("http://x", "qwen3:8b", get=lambda u: tags)
    assert r["state"] == health.DEGRADED and "isn't downloaded" in r["detail"]
    assert health.ollama("http://x", "m", get=lambda u: {"models": []})["state"] == health.DEGRADED


def test_ollama_down_is_missing_or_failed(monkeypatch):
    from core import health
    import shutil

    def down(_u):
        raise ConnectionError("refused")
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert health.ollama("http://x", "m", get=down)["state"] == health.MISSING
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ollama")
    assert health.ollama("http://x", "m", get=down)["state"] == health.FAILED


def test_ollama_inference_needs_a_real_answer():
    from core import health
    assert health.ollama_inference("http://x", "m", post=lambda *a: {"response": "ready"})["state"] == health.WORKING
    assert health.ollama_inference("http://x", "m", post=lambda *a: {"response": " "})["state"] == health.FAILED


def test_tts_health_reports_the_engine_that_speaks():
    from types import SimpleNamespace
    from core import health
    assert health.tts(SimpleNamespace(_engine_type="espeak", _device_info={}))["state"] == health.DEGRADED
    assert health.tts(SimpleNamespace(_engine_type="kokoro", _device_info={}))["state"] == health.WORKING
    assert health.tts(None)["state"] == health.NOT_TESTED


# --------------------------------------------------------------------------- build 7: themes, widgets, task manager
def test_halo_colour_schemes_match_saints_palette():
    pytest.importorskip("PySide6")
    import importlib.util
    spec = importlib.util.spec_from_file_location("mcs", ROOT / "packaging/linux/branding/make_color_schemes.py")
    mcs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcs)
    for dark, name in ((True, "HaloDark"), (False, "HaloLight")):
        shipped = (ROOT / f"packaging/linux/rootfs/usr/share/color-schemes/{name}.colors").read_text(encoding="utf-8")
        assert shipped.replace("\r\n", "\n") == mcs.scheme(dark), f"{name}.colors is stale: run make_color_schemes.py"
        assert f"ColorScheme={name}" in shipped and "DecorationFocus=254,170,52" in shipped


def test_halo_dark_is_the_default_and_light_is_one_switch_away():
    lf = ROOT / "packaging/linux/rootfs/usr/share/plasma/look-and-feel"
    kdeglobals = (ROOT / "packaging/linux/rootfs/etc/xdg/kdeglobals").read_text(encoding="utf-8")
    assert "ColorScheme=HaloDark" in kdeglobals
    assert "DefaultDarkLookAndFeel=org.saintos.desktop" in kdeglobals
    assert "DefaultLightLookAndFeel=org.saintos.desktop.light" in kdeglobals
    assert "ColorScheme=HaloDark" in (lf / "org.saintos.desktop/contents/defaults").read_text(encoding="utf-8")
    assert "ColorScheme=HaloLight" in (lf / "org.saintos.desktop.light/contents/defaults").read_text(encoding="utf-8")
    meta = json.loads((lf / "org.saintos.desktop.light/metadata.json").read_text(encoding="utf-8"))
    assert meta["KPlugin"]["Id"] == "org.saintos.desktop.light"
    branding = (ROOT / "packaging/arch/pkgbuilds/halo-branding/PKGBUILD").read_text(encoding="utf-8")
    assert "Halo{Dark,Light}.colors" in branding and '"Name": "Halo Light"' in branding


def test_widgets_and_task_manager_ship_in_the_image():
    plasmoids = ROOT / "packaging/linux/rootfs/usr/share/plasma/plasmoids"
    for w in ("halo", "music"):
        meta = json.loads((plasmoids / f"org.saintos.{w}/metadata.json").read_text(encoding="utf-8"))
        assert meta["KPlugin"]["Id"] == f"org.saintos.{w}"
        assert (plasmoids / f"org.saintos.{w}/contents/ui/main.qml").exists()
    pkg = (ROOT / "packaging/arch/pkgbuilds/saint/PKGBUILD").read_text(encoding="utf-8")
    assert all(f"plasmoids/org.saintos.{w}" in pkg for w in ("saint", "halo", "music")) and "saint-taskmgr" in pkg
    assert "[services][saint-taskmgr.desktop]\n_launch=Ctrl+Shift+Esc" in pkg
    accel = (ROOT / "packaging/linux/rootfs/usr/share/kglobalaccel/saint-taskmgr.desktop").read_text(encoding="utf-8")
    assert "X-KDE-Shortcuts=Ctrl+Shift+Esc" in accel and "Exec=/usr/bin/saint-taskmgr" in accel
    launcher = (ROOT / "packaging/linux/rootfs/usr/bin/saint-taskmgr").read_bytes()
    assert b"\r" not in launcher and b"-m ui.taskmanager" in launcher


def test_saint_follows_the_desktop_theme_on_linux():
    from core import config as cfg
    assert cfg.DEFAULT_THEME == ("Dark" if os.name == "nt" else "System")
