"""
modules/remote_desktop/media.py — Halo Remote host glue for a Linux desktop (Plasma 6 Wayland).

STATUS: implemented, untested. It needs a Wayland session with xdg-desktop-portal(-kde), PipeWire,
GStreamer >= 1.22 with webrtcbin (gst-plugins-bad, libnice), python-gobject, gst-python, libsoup3,
wl-clipboard and kdialog — none of which exist on the Windows machine this was written on. Every decision
it makes is delegated to host.py, which is unit-tested (tests/test_remote_host.py).

One GLib main loop runs everything:

    relay (libsoup WebSocket) --signed messages--> SessionController (host.py) --Actions--> here
    portal RemoteDesktop + ScreenCast -> PipeWire fd -> GStreamer pipeline -> webrtcbin
    webrtcbin data channels: "control" -> InputMapper -> portal Notify* calls; "files" -> FileReceiver
"""

import json
import logging
import os
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Gst, GstSdp, GstWebRTC, Soup  # noqa: E402

from modules.link.identity import PeerStore  # noqa: E402
from modules.remote_desktop import host as H  # noqa: E402
from modules.remote_desktop import protocol as P  # noqa: E402

log = logging.getLogger("halo.remote.media")

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
RD = "org.freedesktop.portal.RemoteDesktop"
SC = "org.freedesktop.portal.ScreenCast"


# ---------------------------------------------------------------------- #
# xdg-desktop-portal
# ---------------------------------------------------------------------- #
class Portal:
    """RemoteDesktop + ScreenCast session. The first start shows KDE's "Allow remote control?" dialog
    on the PC; the restore token kept in data/link/remote_portal.json makes later starts silent."""

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.session = None
        self.stream = None                     # (node_id, width, height)
        self.fd = -1
        self._n = 0
        self.token_path = H._data_path("link", "remote_portal.json")

    def _token(self):
        try:
            with open(self.token_path, encoding="utf-8") as f:
                return json.load(f).get("restore_token", "")
        except (OSError, ValueError):
            return ""

    def _save_token(self, token):
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump({"restore_token": token}, f)

    def _request(self, iface, method, args_fmt, args, options, on_done):
        self._n += 1
        token = "halo%d_%d" % (os.getpid(), self._n)
        path = "%s/request/%s/%s" % (PORTAL_PATH, self.sender, token)
        sub = [None]

        def response(_conn, _sender, _path, _iface, _signal, params):
            self.bus.signal_unsubscribe(sub[0])
            code, results = params.unpack()
            on_done(code, results)

        sub[0] = self.bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", path, None,
                                           Gio.DBusSignalFlags.NO_MATCH_RULE, response)
        options = dict(options, handle_token=GLib.Variant("s", token))
        self.bus.call_sync(PORTAL, PORTAL_PATH, iface, method,
                           GLib.Variant(args_fmt, tuple(args) + (options,)), None, Gio.DBusCallFlags.NONE, -1, None)

    def start(self, on_ready, on_error):
        def created(code, res):
            if code != 0:
                return on_error("portal_create_failed")
            self.session = res["session_handle"]
            opts = {"types": GLib.Variant("u", 3), "persist_mode": GLib.Variant("u", 2)}
            if self._token():
                opts["restore_token"] = GLib.Variant("s", self._token())
            self._request(RD, "SelectDevices", "(oa{sv})", [self.session], opts, devices)

        def devices(code, _res):
            if code != 0:
                return on_error("portal_devices_refused")
            opts = {"types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", False),
                    "cursor_mode": GLib.Variant("u", 2)}
            self._request(SC, "SelectSources", "(oa{sv})", [self.session], opts, sources)

        def sources(code, _res):
            if code != 0:
                return on_error("portal_sources_refused")
            self._request(RD, "Start", "(osa{sv})", [self.session, ""], {}, started)

        def started(code, res):
            if code != 0:
                return on_error("portal_start_refused")
            if res.get("restore_token"):
                self._save_token(res["restore_token"])
            node, props = res["streams"][0]
            w, h = props.get("size", (1920, 1080))
            self.stream = (node, w, h)
            ret, fds = self.bus.call_with_unix_fd_list_sync(
                PORTAL, PORTAL_PATH, SC, "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE, -1, None, None)
            self.fd = fds.get(ret.unpack()[0])
            on_ready(self.fd, node, w, h)

        self._request(RD, "CreateSession", "(a{sv})", [],
                      {"session_handle_token": GLib.Variant("s", "halo%d" % os.getpid())}, created)

    def notify(self, call: H.PortalCall):
        if not self.session:
            return
        fmt = {"NotifyPointerMotionAbsolute": "(oa{sv}udd)", "NotifyPointerButton": "(oa{sv}iu)",
               "NotifyPointerAxis": "(oa{sv}dd)", "NotifyKeyboardKeycode": "(oa{sv}iu)",
               "NotifyKeyboardKeysym": "(oa{sv}iu)"}[call.method]
        opts = {"finish": GLib.Variant("b", True)} if call.method == "NotifyPointerAxis" else {}
        self.bus.call(PORTAL, PORTAL_PATH, RD, call.method, GLib.Variant(fmt, (self.session, opts) + call.args),
                      None, Gio.DBusCallFlags.NONE, -1, None, None, None)

    def close(self):
        if self.session:
            self.bus.call(PORTAL, self.session, "org.freedesktop.portal.Session", "Close", None, None,
                          Gio.DBusCallFlags.NONE, -1, None, None, None)
        if self.fd >= 0:
            os.close(self.fd)
        self.session, self.fd = None, -1


# ---------------------------------------------------------------------- #
# One remote-desktop session's media
# ---------------------------------------------------------------------- #
class Media:
    def __init__(self, app, session: H.Session):
        self.app, self.session = app, session
        self.portal = Portal()
        self.pipeline = self.webrtc = None
        self.control = self.files = None
        self.mapper = None
        self.receiver = H.FileReceiver()
        self.clip_proc = None
        self.last_clip = None
        self.frames = self.bytes = 0
        self.stats_timer = 0

    def start(self):
        self.portal.start(self._portal_ready, lambda why: self.app.end(why))

    def _portal_ready(self, fd, node, w, h):
        self.mapper = H.InputMapper(node, w, h)
        enc = H.pick_encoder(lambda name: Gst.ElementFactory.find(name) is not None)
        self.pipeline = Gst.parse_launch(H.pipeline_description(fd, node, enc, self.session.want))
        self.webrtc = self.pipeline.get_by_name("webrtc")
        stun, turns = H.webrtcbin_ice(self.app.ice_servers)
        if stun:
            self.webrtc.set_property("stun-server", stun)
        for t in turns:
            self.webrtc.emit("add-turn-server", t)
        self.webrtc.connect("on-negotiation-needed", self._negotiate)
        self.webrtc.connect("on-ice-candidate", self._local_ice)
        self.webrtc.connect("notify::connection-state", self._state)
        pay = self.pipeline.get_by_name("webrtc").get_static_pad("sink_0")
        if pay is not None:
            pay.add_probe(Gst.PadProbeType.BUFFER, self._count)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.control = self.webrtc.emit("create-data-channel", "control", None)
        self.files = self.webrtc.emit("create-data-channel", "files", None)
        self.control.connect("on-message-string", self._on_control)
        self.files.connect("on-message-string", self._on_file_msg)
        self.files.connect("on-message-data", self._on_file_data)
        self.control.connect("on-open", lambda ch: self._watch_clipboard())
        self.stats_timer = GLib.timeout_add_seconds(1, self._stats)

    def _count(self, _pad, info):
        self.frames += 1
        self.bytes += info.get_buffer().get_size()
        return Gst.PadProbeReturn.OK

    def _negotiate(self, webrtc):
        def created(promise):
            offer = promise.get_reply().get_value("offer")
            webrtc.emit("set-local-description", offer, Gst.Promise.new())
            self.app.run(self.app.controller.local_sdp(offer.sdp.as_text()))
        webrtc.emit("create-offer", None, Gst.Promise.new_with_change_func(created))

    def remote_answer(self, sdp: str):
        _, msg = GstSdp.SDPMessage.new_from_text(sdp)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, msg)
        self.webrtc.emit("set-remote-description", answer, Gst.Promise.new())

    def remote_ice(self, cand: dict):
        if self.webrtc is not None and isinstance(cand.get("candidate"), str) and cand["candidate"]:
            self.webrtc.emit("add-ice-candidate", int(cand.get("sdpMLineIndex") or 0), cand["candidate"])

    def _local_ice(self, _webrtc, mline, candidate):
        self.app.run(self.app.controller.local_ice({"candidate": candidate, "sdpMLineIndex": mline}))

    def _state(self, webrtc, _pspec):
        state = webrtc.get_property("connection-state")
        if state == GstWebRTC.WebRTCPeerConnectionState.CONNECTED:
            self.app.controller.on_connected()
        elif state in (GstWebRTC.WebRTCPeerConnectionState.FAILED, GstWebRTC.WebRTCPeerConnectionState.CLOSED):
            self.app.end("connection_lost")

    # control channel ----------------------------------------------------
    def _on_control(self, _ch, text):
        try:
            msg = P.parse(text, P.CONTROL_SCHEMAS)
        except P.ProtocolError as e:
            log.debug("remote.bad_control %s", e.reason)
            return
        self.app.controller.on_input()
        if msg["type"] == "clipboard":
            if msg["mime"].startswith("text/") and len(msg["data"]) <= H.MAX_CLIPBOARD:
                self.last_clip = msg["data"]
                proc = Gio.Subprocess.new(["wl-copy", "--type", "text/plain"], Gio.SubprocessFlags.STDIN_PIPE)
                proc.communicate_utf8_async(msg["data"], None, None)
            return
        for call in self.mapper.map(msg):
            self.portal.notify(call)

    def _watch_clipboard(self):
        try:
            self.clip_proc = Gio.Subprocess.new(
                ["wl-paste", "--type", "text/plain", "--watch", "sh", "-c", "base64 -w0; echo"],
                Gio.SubprocessFlags.STDOUT_PIPE)
        except GLib.Error:
            log.info("remote.no_wl_paste: clipboard sync from the PC is off")
            return
        stream = Gio.DataInputStream.new(self.clip_proc.get_stdout_pipe())

        def line(src, res):
            try:
                data, _ = src.read_line_finish_utf8(res)
            except GLib.Error:
                return
            if data is None:
                return
            text = GLib.base64_decode(data).decode("utf-8", "replace") if data else ""
            if text and text != self.last_clip and len(text) <= H.MAX_CLIPBOARD and self.control is not None:
                self.last_clip = text
                self.control.emit("send-string", json.dumps({"type": "clipboard", "mime": "text/plain",
                                                             "data": text}))
            src.read_line_async(GLib.PRIORITY_DEFAULT, None, line)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, line)

    # files channel ------------------------------------------------------
    def _on_file_msg(self, ch, text):
        try:
            msg = P.parse(text, P.FILE_SCHEMAS)
        except P.ProtocolError:
            return
        if msg["type"] == "file.offer":
            ch.emit("send-string", json.dumps(self.receiver.offer(msg)))

    def _on_file_data(self, ch, data):
        done = self.receiver.chunk(data.get_data())
        if done is not None:
            ch.emit("send-string", json.dumps(done))

    # stats --------------------------------------------------------------
    def _stats(self):
        if self.control is None or self.webrtc is None:
            return False
        frames, sent = self.frames, self.bytes
        self.frames = self.bytes = 0

        def got(promise):
            rtt, path = 0.0, "p2p"
            reply = promise.get_reply()
            if reply is not None:
                def each(_field, value):
                    nonlocal rtt, path
                    if isinstance(value, Gst.Structure):
                        if value.has_field("round-trip-time"):
                            rtt = max(rtt, value.get_value("round-trip-time") * 1000.0)
                        if value.has_field("candidate-type") and value.get_value("candidate-type") == "relay":
                            path = "relay"
                    return True
                reply.foreach(each)
            try:
                self.control.emit("send-string", json.dumps({"type": "stats", "rtt_ms": round(rtt, 1), "fps": frames,
                                                             "kbps": round(sent * 8 / 1000), "path": path}))
            except Exception:
                pass
        self.webrtc.emit("get-stats", None, Gst.Promise.new_with_change_func(got))
        return True

    def stop(self):
        if self.mapper is not None:
            for call in self.mapper.release_all():
                self.portal.notify(call)
        if self.stats_timer:
            GLib.source_remove(self.stats_timer)
        if self.clip_proc is not None:
            self.clip_proc.force_exit()
        self.receiver.abort()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.portal.close()


# ---------------------------------------------------------------------- #
# The service
# ---------------------------------------------------------------------- #
class HostApp:
    def __init__(self):
        self.private, self.public, self.name = H.load_device_key()
        self.device_id = P.device_id_for(self.public)
        self.peers = PeerStore()
        self.pairing = H.RelayPairing()
        self.config = H.HostConfig.load()
        self.ice_servers = []
        self.controller = H.SessionController(self.private, self.peers, lambda: self.ice_servers)
        self.media = None
        self.ws = None
        self.soup = Soup.Session()
        self.backoff = 2
        self.loop = GLib.MainLoop()
        self._monitors = []
        for path in (self.peers.path, self.peers.revoked_path):
            mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
            mon.connect("changed", lambda *_: self._devices_changed())
            self._monitors.append(mon)

    # relay --------------------------------------------------------------
    def connect(self):
        self.config = H.HostConfig.load()
        if not self.config.relay_url:
            log.info("remote.no_relay: waiting (halo-remote set-relay wss://…/ws)")
            GLib.timeout_add_seconds(30, lambda: self.connect() and False)
            return
        msg = Soup.Message.new("GET", self.config.relay_url)
        self.soup.websocket_connect_async(msg, None, None, GLib.PRIORITY_DEFAULT, None, self._connected)

    def _connected(self, session, res):
        try:
            self.ws = session.websocket_connect_finish(res)
        except GLib.Error as e:
            log.warning("remote.relay_unreachable %s (retry in %ds)", e.message, self.backoff)
            return self._retry()
        self.backoff = 2
        self.ws.connect("message", self._on_ws)
        self.ws.connect("closed", lambda *_: self._retry())
        self.send({"type": "hello", "v": P.VERSION, "device_id": self.device_id, "role": P.HOST,
                   "pubkey": P.b64(self.public), "name": self.name})

    def _retry(self):
        self.ws = None
        GLib.timeout_add_seconds(self.backoff, lambda: self.connect() and False)
        self.backoff = min(self.backoff * 2, 60)

    def send(self, msg):
        if self.ws is not None and self.ws.get_state() == Soup.WebsocketState.OPEN:
            self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    def _on_ws(self, _ws, kind, data):
        if kind != Soup.WebsocketDataType.TEXT:
            return
        try:
            msg = P.parse(data.get_data())
        except P.ProtocolError as e:
            log.debug("remote.bad_relay_message %s", e.reason)
            return
        t = msg["type"]
        if t == "relay.challenge":
            self.send(P.relay_proof(self.private, msg, P.HOST))
        elif t == "welcome":
            self.ice_servers = msg["ice_servers"]
            self._devices_changed()
            log.info("remote.online relay=%s", self.config.relay_url)
        elif t == "error":
            log.warning("remote.relay_error %s", msg["reason"])
        elif t == "pair.request":
            self._pair(msg)
        elif t in P.ROUTED_TYPES:
            self.run(self.controller.on_message(msg))

    def _pair(self, msg):
        psk = self.pairing.psk()
        if psk is None or msg["to"] != self.device_id or not self.pairing.check(msg):
            self.send(P.routed("pair.deny", self.device_id, msg["from"], reason="bad_code"))
            return
        peer = H.accept_pairing(self.peers, msg)
        self.send(P.pair_mac(P.routed("pair.accept", self.device_id, peer.id, pubkey=P.b64(self.public),
                                      name=self.name), psk))
        self._devices_changed()

    def _devices_changed(self):
        self.controller.refresh_keys()
        self.send({"type": "devices", "keys": P.allowed_devices(self.peers)})
        self.run(self.controller.tick())

    # actions ------------------------------------------------------------
    def run(self, actions):
        for a in actions:
            if a.kind == "send":
                self.send(a.message)
            elif a.kind == "ask":
                self._ask(a.session)
            elif a.kind == "start_media":
                self.media = Media(self, a.session)
                self.media.start()
            elif a.kind == "stop_media" and self.media is not None:
                self.media.stop()
                self.media = None
            elif a.kind == "remote_sdp" and self.media is not None:
                self.media.remote_answer(a.message["sdp"])
            elif a.kind == "remote_ice" and self.media is not None:
                self.media.remote_ice(a.message["candidate"])

    def end(self, reason):
        self.run(self.controller.end(reason))

    def _ask(self, session):
        text = "%s wants to see and control this PC. Allow?" % (session.name or "A paired device")
        try:
            proc = Gio.Subprocess.new(["timeout", str(int(H.ASK_TIMEOUT)), "kdialog", "--title", "Halo Remote",
                                       "--yesno", text], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            log.warning("remote.no_prompt: kdialog missing, declining %s", session.peer_id)
            return self.run(self.controller.on_user_answer(session.session_id, False))

        def done(p, res):
            p.wait_finish(res)
            ok = p.get_if_exited() and p.get_exit_status() == 0
            self.run(self.controller.on_user_answer(session.session_id, ok))
        proc.wait_async(None, done)

    def main(self):
        GLib.timeout_add_seconds(1, lambda: self.run(self.controller.tick()) or True)
        self.connect()
        self.loop.run()


def main() -> int:
    Gst.init(None)
    if Gst.ElementFactory.find("webrtcbin") is None:
        log.error("remote.no_webrtcbin: install gst-plugins-bad and libnice")
        return 1
    try:
        app = HostApp()
    except H.KeyUnavailable as e:
        log.error("remote.no_key %s", e)
        return 1
    app.main()
    return 0
