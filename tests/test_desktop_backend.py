"""
modules/desktop/backend.py + modules/desktop/linux/kwin.py

DesktopController on Linux runs its usual logic over KWin primitives. These
tests use a fake KWin (a transport that answers the scripts from an in-memory
desktop) — mocked: they prove the controller and the script protocol fit
together, not that a real Plasma session accepts the scripts. That is checked
by the `real` test at the bottom, which only runs inside a Plasma session.
"""

import json
import os
import re

import pytest

from core.config import config
from modules.automation.tools import ToolError
from modules.desktop import backend, controller as ctl
from modules.desktop.linux import kwin


class FakeKWin:
    """Answers SAINT's KWin scripts the way KWin would, from a little model of the desktop."""

    bus_name = "org.saint.KWinBridge.p1"

    def __init__(self):
        self.screens = [dict(name="eDP-1", x=0, y=0, w=1920, h=1200, wx=0, wy=0, ww=1920, wh=1152, scale=1.25),
                        dict(name="HDMI-A-1", x=1920, y=0, w=2560, h=1440, wx=1920, wy=0, ww=2560, wh=1392,
                             scale=1.0)]
        self.wins = [  # front-most first
            dict(id="{a}", title="Kate — notes.txt", cls="org.kde.kate", pid=101, x=100, y=100, w=800, h=600,
                 min=False, max=False, active=True),
            dict(id="{b}", title="Spotify Premium", cls="spotify", pid=102, x=2000, y=50, w=1200, h=900,
                 min=False, max=False, active=False),
            dict(id="{c}", title="Dolphin", cls="org.kde.dolphin", pid=103, x=50, y=60, w=900, h=700,
                 min=True, max=False, active=False),
        ]
        self.scripts = []

    def _win(self, wid):
        return next((w for w in self.wins if w["id"] == wid), None)

    def run(self, script: str):
        self.scripts.append(script)
        assert 'callDBus(BUS, "/", IFACE, "deliver"' in script
        assert json.dumps(self.bus_name) in script
        if "workspace.screens.map" in script:
            return {"screens": self.screens, "windows": [dict(w) for w in self.wins]}
        wid = json.loads(re.search(r"const w = byId\((\"[^\"]*\")\)", script).group(1))
        w = self._win(wid)
        if w is None:
            return {"error": "gone"}
        if "workspace.activeWindow = w" in script:
            for o in self.wins:
                o["active"] = o is w
            w["min"] = False
        elif "w.closeWindow()" in script:
            self.wins.remove(w)
        elif "w.frameGeometry = " in script:
            g = re.search(r"x: (-?\d+), y: (-?\d+), width: (\d+), height: (\d+)", script).groups()
            w.update(x=int(g[0]), y=int(g[1]), w=int(g[2]), h=int(g[3]), max=False)
        elif "setMaximize(true, true)" in script:
            s = next(s for s in self.screens if s["x"] <= w["x"] < s["x"] + s["w"])
            w.update(x=s["wx"], y=s["wy"], w=s["ww"], h=s["wh"], max=True, min=False)
        elif "w.minimized = true" in script:
            w["min"] = True
        elif "setMaximize(false, false)" in script:
            w.update(min=False, max=False)
        return dict(w)


@pytest.fixture
def linux_desktop(monkeypatch):
    fake = FakeKWin()
    wm = kwin.KWinWindows(transport=fake)
    monkeypatch.setattr(ctl, "HAS_WIN32", False)
    monkeypatch.setattr(backend, "window_manager", lambda: wm)
    monkeypatch.setattr(kwin, "_process_name", lambda pid, fallback: fallback.split(".")[-1])
    monkeypatch.setattr(ctl, "_game_guard", lambda: None)
    old = {k: config.get(k) for k in ("desktop.enabled", "desktop.allow_window_control")}
    config.set("desktop.enabled", True)
    config.set("desktop.allow_window_control", True)
    yield ctl.DesktopController(), fake
    for k, v in old.items():
        config.set(k, v)


def test_monitors_from_kwin(linux_desktop):
    dc, _ = linux_desktop
    mons = dc.monitors()
    assert [(m.index, m.primary, m.position) for m in mons] == [(1, True, "main"), (2, False, "right")]
    assert mons[0].work == (0, 0, 1920, 1152) and mons[0].scale == 1.25
    assert dc.resolve_monitor("second", 1).index == 2


def test_windows_from_kwin(linux_desktop):
    dc, _ = linux_desktop
    wins = dc.list_windows()
    assert [w.title for w in wins] == ["Kate — notes.txt", "Spotify Premium", "Dolphin"]
    assert [w.monitor for w in wins] == [1, 2, 1]
    assert wins[0].foreground and wins[2].minimized and wins[1].process == "spotify"


def test_focus_and_find(linux_desktop):
    dc, fake = linux_desktop
    info = dc.focus("spotify")
    assert info.foreground and info.hwnd == "{b}"
    assert dc.focus("dolphin").minimized is False                 # activating un-minimizes


def test_move_to_other_monitor(linux_desktop):
    dc, fake = linux_desktop
    info = dc.move_to_monitor("kate", "second")
    assert info.monitor == 2 and fake._win("{a}")["x"] >= 1920


def test_arrange_snap_and_maximize(linux_desktop):
    dc, fake = linux_desktop
    left = dc.arrange("kate", "snap_left")
    assert (left.left, left.top, left.width, left.height) == (0, 0, 960, 1152)
    assert dc.arrange("kate", "maximize").maximized
    assert dc.arrange("kate", "minimize").minimized


def test_close_is_polite_and_confirmed(linux_desktop):
    dc, fake = linux_desktop
    res = dc._close(dc.find_window("dolphin"))
    assert res["closed"] and fake._win("{c}") is None


def test_closed_window_is_reported(linux_desktop):
    dc, fake = linux_desktop
    w = dc.find_window("kate")
    fake.wins.remove(fake._win("{a}"))
    with pytest.raises(ToolError) as e:
        dc._activate(w)
    assert "closed" in str(e.value)


def test_settings_switch_still_applies(linux_desktop):
    dc, fake = linux_desktop
    config.set("desktop.allow_window_control", False)
    n = len(fake.scripts)
    with pytest.raises(ToolError):
        dc.focus("kate")
    assert len(fake.scripts) == n                                   # refused before touching KWin


def test_script_quotes_untrusted_ids():
    body = kwin.action_body('x"); workspace.windowList().forEach(w => w.closeWindow()); ("', "activate")
    assert 'byId("x\\"); workspace' in body                         # JSON-quoted: stays one string literal
    with pytest.raises(KeyError):
        kwin.action_body("{a}", "run_shell")                        # only the known actions exist
    with pytest.raises((KeyError, ValueError, TypeError)):
        kwin.action_body("{a}", "set_pos", {"x": "1; evil()", "y": 0, "width": 1, "height": 1})


def test_kwin_errors_become_tool_errors():
    class Broken(FakeKWin):
        def run(self, script):
            return {"error": "TypeError: workspace.stackingOrder is undefined"}
    with pytest.raises(ToolError) as e:
        kwin.KWinWindows(transport=Broken()).windows()
    assert "KWin script failed" in str(e.value)


def test_unsupported_desktop_explains_itself(monkeypatch):
    monkeypatch.setattr(backend, "Win32Windows", lambda: (_ for _ in ()).throw(ImportError("no pywin32")))
    monkeypatch.setattr(backend, "_linux_backend", None)
    from core import platform_caps
    monkeypatch.setattr(platform_caps, "detect_session",
                        lambda: platform_caps.Session("linux", "GNOME", "wayland", "fedora", "44"))
    with pytest.raises(ToolError) as e:
        backend.window_manager()
    assert "isn't available" in str(e.value) or "KDE Plasma" in str(e.value)


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps the pywin32 backend")
@pytest.mark.real
def test_real_kwin_session_lists_windows():
    from core.platform_caps import detect_session
    s = detect_session()
    if not (s.kde and s.display == "wayland"):
        pytest.skip("not a Plasma Wayland session")
    wm = kwin.KWinWindows()
    assert wm.monitors(), "KWin reported no screens"
    assert isinstance(wm.windows(), list)


@pytest.mark.skipif(os.name != "nt", reason="pywin32 only")
def test_windows_backend_is_pywin32():
    assert backend.window_manager().name == "win32"
