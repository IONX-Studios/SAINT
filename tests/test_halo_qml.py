"""Halo's QML (the reactive wallpaper, the SAINT widget) loads without errors and reacts to SAINT's state.
Plasma's own QML types aren't installed here, so its root type is stood in for by a plain Item."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WALLPAPER = ROOT / "packaging/linux/rootfs/usr/share/plasma/wallpapers/org.saintos.wallpaper/contents/ui/main.qml"


def _load(path, tmp_path, root_type):
    pytest.importorskip("PySide6.QtQuick")
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    QGuiApplication.instance() or QGuiApplication([])
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"^import org\.kde\.\S+.*$", "", src, flags=re.M).replace(f"{root_type} {{", "Item {", 1)
    probe = tmp_path / "main.qml"
    probe.write_text(src, encoding="utf-8")
    engine = QQmlEngine()
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(probe)))
    obj = comp.create()
    if comp.isError() and "QtWebSockets" in " ".join(e.toString() for e in comp.errors()):
        pytest.skip("QtWebSockets' QML module isn't in this Qt (Halo installs qt6-websockets)")
    assert obj is not None, [e.toString() for e in comp.errors()]
    obj._keep = (engine, comp)                                  # or Python collects them and the item with them
    return engine, obj


def test_wallpaper_loads_and_follows_saint(tmp_path):
    engine, w = _load(WALLPAPER, tmp_path, "WallpaperItem")
    w.setProperty("width", 1920)
    w.setProperty("height", 1080)
    assert w.property("mode") == "still"                       # SAINT not running: nothing animates
    assert abs(w.property("ringY") - 1080 * 0.42) < 0.5 and abs(w.property("ringR") - 200) < 0.5
    for state, mode in (("command_listening", "listening"), ("processing", "thinking"), ("speaking", "speaking"),
                        ("wake_listening", "still"), ("error", "error")):
        w.setProperty("saintState", state)
        assert w.property("mode") == mode, state
    w.setProperty("width", 3440)                               # ultrawide: the crop moves the ring
    w.setProperty("height", 1440)
    shown = max(1440, 3440 * 9 / 16)
    assert abs(w.property("ringY") - (720 + (0.42 - 0.5) * shown)) < 0.5
