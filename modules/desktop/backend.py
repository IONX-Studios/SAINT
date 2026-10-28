"""
modules/desktop/backend.py

The window-manager primitives under DesktopController, one implementation per platform.

DesktopController keeps every decision — which window "it" means, which monitor
"my second screen" is, ambiguity, the Settings switches, game mode, blocked key
combos — and calls only these primitives to act:

    monitors()                      raw monitor rectangles (controller sorts and labels them)
    windows()                       raw top-level windows, front-most first
    show(handle, state)             "restore" | "maximize" | "minimize"
    set_pos(handle, x, y, w, h)     move/resize (move=False keeps the position)
    activate(handle)                bring to the front
    close(handle)                   polite close (the app may show a save prompt)
    is_window(handle) / pid(handle)

Win32Windows wraps the pywin32 calls the controller always made. On Linux the
KWin backend (modules/desktop/linux/kwin.py) does the same through Plasma's
scripting API. ``window_manager()`` picks one, or raises ToolError with the
reason from core/platform_caps.py.
"""

import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from modules.automation.tools import ToolError

log = logging.getLogger("saint.desktop")


@dataclass
class RawMonitor:
    rect: Tuple[int, int, int, int]      # left, top, right, bottom
    work: Tuple[int, int, int, int]      # the same without panels / the taskbar
    primary: bool
    scale: float = 1.0


@dataclass
class RawWindow:
    handle: object                       # HWND (int) on Windows, KWin's internal id (str) on Linux
    title: str
    process: str
    left: int
    top: int
    width: int
    height: int
    minimized: bool
    maximized: bool
    foreground: bool
    pid: int = 0


class Win32Windows:
    """The pywin32 calls DesktopController has always made, unchanged."""

    name = "win32"

    def __init__(self):
        import win32con
        import win32gui
        self.win32con, self.win32gui = win32con, win32gui

    def show(self, handle, state: str):
        flag = {"restore": self.win32con.SW_RESTORE, "maximize": self.win32con.SW_MAXIMIZE,
                "minimize": self.win32con.SW_MINIMIZE}[state]
        self.win32gui.ShowWindow(handle, flag)

    def set_pos(self, handle, x: int, y: int, width: int, height: int, move: bool = True, activate: bool = True):
        c = self.win32con
        flags = c.SWP_NOZORDER | (0 if move else c.SWP_NOMOVE) | (0 if activate else c.SWP_NOACTIVATE)
        self.win32gui.SetWindowPos(handle, 0, x, y, width, height, flags)


_linux_backend = None


def window_manager():
    """The primitives for this platform (Win32Windows / KWinWindows), or ToolError("UNSUPPORTED")."""
    global _linux_backend
    try:
        return Win32Windows()
    except ImportError:
        pass
    if _linux_backend is None:
        from core.platform_caps import detect_session, unavailable_reason
        session = detect_session()
        if session.os == "linux" and session.kde and session.display == "wayland":
            from modules.desktop.linux.kwin import KWinWindows
            _linux_backend = KWinWindows()
        else:
            raise ToolError(unavailable_reason("windows") or
                            "Window control needs KDE Plasma on Linux (KWin's scripting interface).",
                            "UNSUPPORTED")
    return _linux_backend


def wait_until(check, timeout: float = 1.0, step: float = 0.05) -> bool:
    """Poll ``check()`` until it's true or ``timeout`` passes (window managers apply changes asynchronously)."""
    deadline = time.time() + timeout
    while True:
        if check():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(step)
