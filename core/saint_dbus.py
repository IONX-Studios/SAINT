"""
core/saint_dbus.py

SAINT on the session bus (Linux): org.saintos.Saint at /Saint, for the parts of Halo that live outside
SAINT's process — the KWin script (top screen edge), the reactive wallpaper and the Plasma widgets.

    methods  ToggleOverlay()  Show()  OpenPage(s name)  State() -> s
    signal   StateChanged(s state, d level)     assistant state ("idle", "listening", "thinking",
                                                "speaking", ...) and the microphone level 0..1

The methods go through the same closed command set as core/instance.py. No-op where there's no
session bus (Windows, a bare console).
"""

import json
import logging
import time

from PySide6.QtCore import ClassInfo, QObject, Signal, Slot

log = logging.getLogger("saint.dbus")
SERVICE = "org.saintos.Saint"
PATH = "/Saint"
LEVEL_HZ = 15                      # microphone level updates per second at most (the wallpaper animates on them)


@ClassInfo({"D-Bus Interface": SERVICE})
class _Interface(QObject):
    StateChanged = Signal(str, float)

    def __init__(self, handler, state_getter):
        super().__init__()
        self._handler = handler
        self._state = state_getter

    @Slot()
    def ToggleOverlay(self):
        self._handler("toggle-overlay")

    @Slot()
    def Show(self):
        self._handler("show")

    @Slot(str)
    def OpenPage(self, name):
        self._handler(f"page:{name}")

    @Slot(result=str)
    def State(self):
        return json.dumps(self._state())


class SaintBus:
    def __init__(self, handler, state_getter):
        self.iface = None
        self._last = ("", 0.0, 0.0)
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        iface = _Interface(handler, state_getter)
        if not bus.registerObject(PATH, iface, QDBusConnection.ExportAllSlots | QDBusConnection.ExportAllSignals):
            log.warning("dbus.register_object_failed %s", bus.lastError().message())
            return
        if not bus.registerService(SERVICE):
            log.warning("dbus.register_service_failed %s", bus.lastError().message())
            bus.unregisterObject(PATH)
            return
        self.iface = iface
        log.info("dbus.ready %s", SERVICE)

    @property
    def active(self) -> bool:
        return self.iface is not None

    def publish(self, state: str, level: float):
        """Send StateChanged when the state changes, and level changes at most LEVEL_HZ times a second."""
        if not self.iface:
            return
        now = time.monotonic()
        last_state, last_level, last_t = self._last
        if state == last_state and (abs(level - last_level) < 0.02 or now - last_t < 1 / LEVEL_HZ):
            return
        self._last = (state, level, now)
        self.iface.StateChanged.emit(state, float(level))
