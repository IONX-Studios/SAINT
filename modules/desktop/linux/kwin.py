"""
modules/desktop/linux/kwin.py

Window control on KDE Plasma (Wayland) through KWin's scripting interface.

Wayland gives applications no list of other apps' windows; on Plasma the
supported way is a KWin script. For every request SAINT:

  1. writes a tiny JavaScript file that does one thing (list windows, move one, ...),
  2. loads and runs it with org.kde.kwin.Scripting over the session D-Bus,
  3. the script answers by calling back ``deliver(json)`` on a D-Bus name this
     process owns (org.saint.KWinBridge.p<pid>),
  4. SAINT unloads the script.

No root, no extra daemon. Window handles are KWin's internal window ids (UUID
strings). Coordinates are KWin's logical pixels.

Transport is jeepney (pure-Python D-Bus, also used by the Secret Service
backend). ``KWinWindows(transport=...)`` lets the tests replace it.
"""

import json
import logging
import os
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Optional

from modules.automation.tools import ToolError
from modules.desktop.backend import RawMonitor, RawWindow

log = logging.getLogger("saint.desktop.kwin")

BRIDGE_IFACE = "org.saint.KWinBridge"
TIMEOUT = 3.0

# Shared by every script: find a window by id, describe one, send the answer back.
_PRELUDE = r"""
const BUS = %(bus)s, IFACE = %(iface)s;
function done(value) { callDBus(BUS, "/", IFACE, "deliver", JSON.stringify(value)); }
function byId(id) {
    const all = workspace.stackingOrder;
    for (let i = 0; i < all.length; i++) { if (all[i].internalId.toString() === id) return all[i]; }
    return null;
}
function describe(w) {
    const g = w.frameGeometry, m = workspace.clientArea(KWin.MaximizeArea, w);
    return {id: w.internalId.toString(), title: w.caption, cls: w.resourceClass, pid: w.pid,
            x: g.x, y: g.y, w: g.width, h: g.height, min: w.minimized,
            max: g.x === m.x && g.y === m.y && g.width === m.width && g.height === m.height,
            active: workspace.activeWindow === w};
}
try {
%(body)s
} catch (e) { done({error: String(e)}); }
"""

_LIST = r"""
    const screens = workspace.screens.map(function (s) {
        const g = s.geometry, a = workspace.clientArea(KWin.MaximizeArea, s, workspace.currentDesktop);
        return {name: s.name, x: g.x, y: g.y, w: g.width, h: g.height,
                wx: a.x, wy: a.y, ww: a.width, wh: a.height, scale: s.devicePixelRatio};
    });
    const wins = [];
    const order = workspace.stackingOrder;              // bottom to top
    for (let i = order.length - 1; i >= 0; i--) {
        const w = order[i];
        if (w.normalWindow && !w.skipTaskbar && w.caption) wins.push(describe(w));
    }
    done({screens: screens, windows: wins});
"""

_ACTION = r"""
    const w = byId(%(id)s);
    if (!w) { done({error: "gone"}); }
    else {
%(action)s
        done(describe(w));
    }
"""

_ACTIONS = {
    "activate": "        w.minimized = false; workspace.activeWindow = w;",
    "restore": "        w.minimized = false; w.setMaximize(false, false);",
    "maximize": "        w.minimized = false; w.setMaximize(true, true);",
    "minimize": "        w.minimized = true;",
    "close": "        w.closeWindow();",
}


def build_script(body: str, bus: str) -> str:
    return _PRELUDE % {"bus": json.dumps(bus), "iface": json.dumps(BRIDGE_IFACE), "body": body}


def action_body(handle: str, action: str, geometry: Optional[Dict[str, int]] = None) -> str:
    if action == "set_pos":
        g = {k: int(v) for k, v in (geometry or {}).items()}
        code = (f"        w.setMaximize(false, false); "
                f"w.frameGeometry = {{x: {g['x']}, y: {g['y']}, width: {g['width']}, height: {g['height']}}};")
    else:
        code = _ACTIONS[action]
    return _ACTION % {"id": json.dumps(str(handle)), "action": code}


def _process_name(pid: int, fallback: str) -> str:
    try:
        import psutil
        return psutil.Process(pid).name() if pid else fallback
    except Exception:
        return fallback


class JeepneyTransport:
    """Load a KWin script, run it, and wait for its ``deliver`` call."""

    def __init__(self):
        from jeepney import DBusAddress, MatchRule, MessageType, new_method_call, new_method_return
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
        self._j = (DBusAddress, MatchRule, MessageType, new_method_call, new_method_return)
        self.conn = open_dbus_connection(bus="SESSION")
        self.bus_name = f"{BRIDGE_IFACE}.p{os.getpid()}"
        reply = self.conn.send_and_get_reply(message_bus.RequestName(self.bus_name, 4))   # DO_NOT_QUEUE
        if reply.body and reply.body[0] not in (1, 4):          # PRIMARY_OWNER / ALREADY_OWNER
            raise ToolError("Couldn't register SAINT's KWin bridge on the session bus.", "UNSUPPORTED")

    def _kwin(self, path: str, iface: str, method: str, sig: str = "", body: tuple = ()):
        DBusAddress, _, MessageType, new_method_call, _ = self._j
        reply = self.conn.send_and_get_reply(
            new_method_call(DBusAddress(path, bus_name="org.kde.KWin", interface=iface), method, sig, body),
            timeout=TIMEOUT)
        if reply.header.message_type == MessageType.error:
            raise ToolError(f"KWin refused the request: {reply.body[0] if reply.body else 'error'}", "KWIN_ERROR")
        return reply.body

    def run(self, script: str) -> Any:
        _, MatchRule, MessageType, _, new_method_return = self._j
        plugin = f"saint_{uuid.uuid4().hex[:12]}"
        fd, path = tempfile.mkstemp(prefix="saint-kwin-", suffix=".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(script)
            rule = MatchRule(type="method_call", interface=BRIDGE_IFACE, member="deliver")
            with self.conn.filter(rule) as queue:
                script_id = self._kwin("/Scripting", "org.kde.kwin.Scripting", "loadScript", "ss", (path, plugin))[0]
                try:
                    self._kwin(f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run")
                    try:
                        msg = self.conn.recv_until_filtered(queue, timeout=TIMEOUT)
                    except TimeoutError:
                        raise ToolError("KWin didn't answer (is this a Plasma 6 session?).", "KWIN_TIMEOUT")
                    self.conn.send(new_method_return(msg))
                    return json.loads(msg.body[0])
                finally:
                    try:
                        self._kwin("/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s", (plugin,))
                    except Exception as e:
                        log.warning("kwin.unload_failed %s %s", plugin, e)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class KWinWindows:
    name = "kwin"

    def __init__(self, transport: Optional[Any] = None):
        self._transport = transport

    @property
    def transport(self):
        if self._transport is None:
            try:
                self._transport = JeepneyTransport()
            except ImportError:
                raise ToolError("Window control on Linux needs the jeepney package (pip install jeepney).",
                                "UNSUPPORTED")
            except (OSError, ConnectionError) as e:
                raise ToolError(f"Couldn't reach the desktop's session bus: {e}", "UNSUPPORTED")
        return self._transport

    def _run(self, body: str) -> Any:
        t = self.transport
        result = t.run(build_script(body, t.bus_name))
        if isinstance(result, dict) and result.get("error"):
            if result["error"] == "gone":
                raise ToolError("That window has closed.", "NOT_FOUND")
            raise ToolError(f"KWin script failed: {result['error']}", "KWIN_ERROR")
        return result

    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, list]:
        return self._run(_LIST)

    def monitors(self) -> List[RawMonitor]:
        screens = self.snapshot()["screens"]
        # KWin lists outputs in priority order (Display Configuration's "primary" first).
        return [RawMonitor((s["x"], s["y"], s["x"] + s["w"], s["y"] + s["h"]),
                           (s["wx"], s["wy"], s["wx"] + s["ww"], s["wy"] + s["wh"]),
                           i == 0, round(float(s.get("scale") or 1.0), 2))
                for i, s in enumerate(screens)]

    @staticmethod
    def _raw(d: Dict[str, Any]) -> RawWindow:
        return RawWindow(d["id"], d.get("title") or "", _process_name(int(d.get("pid") or 0), d.get("cls") or ""),
                         int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"]), bool(d.get("min")),
                         bool(d.get("max")), bool(d.get("active")), int(d.get("pid") or 0))

    def windows(self) -> List[RawWindow]:
        return [self._raw(d) for d in self.snapshot()["windows"]]

    def window(self, handle) -> RawWindow:
        hit = next((w for w in self.windows() if w.handle == handle), None)
        if hit is None:
            raise ToolError("That window has closed.", "NOT_FOUND")
        return hit

    def show(self, handle, state: str):
        self._run(action_body(handle, state))

    def set_pos(self, handle, x: int, y: int, width: int, height: int, move: bool = True, activate: bool = True):
        if not move:
            cur = self.window(handle)
            x, y = cur.left, cur.top
        self._run(action_body(handle, "set_pos", {"x": x, "y": y, "width": width, "height": height}))

    def activate(self, handle) -> RawWindow:
        return self._raw(self._run(action_body(handle, "activate")))

    def close(self, handle):
        self._run(action_body(handle, "close"))

    def is_window(self, handle) -> bool:
        return any(w.handle == handle for w in self.windows())
