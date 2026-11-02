"""
modules/remote_desktop/host.py — the Halo PC side of Halo Remote (docs/REMOTE_DESKTOP.md).

Runs as the user service ``halo-remote`` on *system* Python (python-gobject, GStreamer and libsoup come from
the distribution; SAINT's venv doesn't have them):

    python3 -m modules.remote_desktop.host enable          # install + start the user service
    python3 -m modules.remote_desktop.host disable
    python3 -m modules.remote_desktop.host status
    python3 -m modules.remote_desktop.host set-relay wss://relay.example.com/ws
    python3 -m modules.remote_desktop.host pair            # one-time code for a browser/phone
    python3 -m modules.remote_desktop.host run             # what the service runs

This file is the part that can be tested anywhere: configuration, pairing through the relay, the decision
whether a request is allowed, the session state machine, the mapping from the ``control`` data channel to
xdg-desktop-portal RemoteDesktop calls, file receiving, and the GStreamer pipeline description.
modules/remote_desktop/media.py drives the portal, PipeWire, GStreamer webrtcbin and the relay socket on a
GLib main loop; it only runs on a Halo/Linux desktop.
"""

import argparse
import hashlib
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from modules.link import crypto
from modules.remote_desktop import protocol as P

log = logging.getLogger("halo.remote")

SERVICE = "halo-remote.service"
ASK_TIMEOUT = 30.0                       # seconds the "Allow <device>?" prompt stays up
NEGOTIATE_TIMEOUT = 30.0                 # accepted -> media flowing
IDLE_TIMEOUT = 12 * 3600                 # no input for this long ends a session
MAX_FILE = 4 * 1024 ** 3                 # 4 GiB per received file
MAX_CLIPBOARD = 4 * 1024 ** 2


def _data_path(*parts) -> str:
    from core.paths import data_path
    return str(data_path(*parts))


# ---------------------------------------------------------------------- #
# Configuration and keys
# ---------------------------------------------------------------------- #
@dataclass
class HostConfig:
    relay_url: str = ""
    path: str = ""

    @classmethod
    def load(cls, path: Optional[str] = None) -> "HostConfig":
        path = path or _data_path("link", "remote.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        return cls(relay_url=str(raw.get("relay_url", "")), path=path)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"relay_url": self.relay_url}, f, indent=2)


def check_relay_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme not in ("wss", "ws") or not parsed.hostname or (parsed.scheme == "ws" and not local):
        raise ValueError("The relay address must be wss://host/ws (ws:// only for localhost).")
    return url.strip()


class KeyUnavailable(Exception):
    pass


def load_device_key(identity_path: Optional[str] = None) -> Tuple[bytes, bytes, str]:
    """(private, public, name) of this PC's SAINT Link identity — never creates one.

    modules.link.identity.Identity makes a new key when it can't read the stored one; here that would
    silently unpair every device, so the host reads the key itself and stops if it can't."""
    path = identity_path or _data_path("link", "identity.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise KeyUnavailable("SAINT Link has no identity yet: open SAINT once (Devices) before enabling Halo Remote.")
    public = bytes.fromhex(data.get("public_key", ""))
    private = bytes.fromhex(data["private_key"]) if data.get("private_key") else None
    if private is None:
        try:
            import keyring
            from core.paths import keyring_service
            service = "SAINT" if keyring_service() == "SAINT" else keyring_service()
            raw = keyring.get_password(service, "link-private-key")
            private = bytes.fromhex(raw) if raw else None
        except Exception as e:                       # no keyring module / locked wallet
            raise KeyUnavailable("Can't read SAINT's device key from the wallet (%s)." % e)
    if private is None or len(private) != 32 or crypto.public_key(private) != public:
        raise KeyUnavailable("SAINT's device key doesn't match its identity file.")
    return private, public, data.get("name") or "Halo PC"


# ---------------------------------------------------------------------- #
# Pairing through the relay (browsers, and phones away from home)
# ---------------------------------------------------------------------- #
class RelayPairing:
    """One-time codes made by ``halo-remote pair``, kept in a 0600 file the running service reads."""

    TTL = 300.0
    MAX_FAILURES = 5

    def __init__(self, path: Optional[str] = None):
        self.path = path or _data_path("link", "remote_pairing.json")

    def create(self, now: Optional[float] = None) -> str:
        from modules.link.identity import SHORT_TOKEN_BYTES, encode_code
        token = os.urandom(SHORT_TOKEN_BYTES)
        self._write({"token": token.hex(), "expires": (now or time.time()) + self.TTL, "failures": 0})
        return encode_code(token)

    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(self.path + ".tmp", self.path)

    def cancel(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def check(self, msg: dict, now: Optional[float] = None) -> bool:
        """True (and the code is used up) when ``msg`` is a pair.request made with the open code."""
        from modules.link.identity import psk_for
        now = time.time() if now is None else now
        offer = self._read()
        if not offer.get("token") or now > offer.get("expires", 0) or offer.get("failures", 0) >= self.MAX_FAILURES:
            return False
        try:
            P.check_pair_mac(msg, psk_for(bytes.fromhex(offer["token"])), now)
        except P.ProtocolError:
            offer["failures"] = offer.get("failures", 0) + 1
            self._write(offer)
            return False
        self.cancel()
        return True

    def psk(self) -> Optional[bytes]:
        from modules.link.identity import psk_for
        offer = self._read()
        return psk_for(bytes.fromhex(offer["token"])) if offer.get("token") else None


def pairing_link(relay_url: str, code: str, host_id: str, account: str) -> str:
    """The page to open on the new device; the code travels in the #fragment, never to a server."""
    parsed = urllib.parse.urlparse(relay_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    frag = urllib.parse.urlencode({"pair": code, "host": host_id, "account": account})
    return "%s://%s/#%s" % (scheme, parsed.netloc, frag)


def accept_pairing(peers, request: dict, platform_default: str = "web"):
    """Record the device from a verified pair.request as one of the user's own devices."""
    from modules.link.identity import ASK, OWN, Peer
    pub = P.unb64(request["pubkey"], 32)
    peer = Peer(id=P.device_id_for(pub), name=" ".join(request["name"].split())[:40] or "Browser",
                public_key=pub.hex(), role=OWN, platform=request.get("platform") or platform_default,
                perms={P.PERMISSION: ASK}, auto_connect=False)
    peers.save(peer)
    return peer


# ---------------------------------------------------------------------- #
# Session state machine
# ---------------------------------------------------------------------- #
IDLE, ASKING, NEGOTIATING, CONNECTED, ENDED = "idle", "asking", "negotiating", "connected", "ended"


@dataclass
class Session:
    session_id: str
    peer_id: str
    peer_public: bytes
    name: str
    want: dict
    state: str = ASKING
    since: float = field(default_factory=time.time)
    last_input: float = field(default_factory=time.time)
    end_reason: str = ""


@dataclass
class Action:
    """What the glue code must do next: send a message, ask the user, start/stop media."""
    kind: str                            # send | ask | start_media | stop_media
    message: Optional[dict] = None
    session: Optional[Session] = None


class SessionController:
    """Decides what happens to each signed message, one session at a time.

    Pure: the caller feeds it verified messages and clock ticks and carries out the returned Actions."""

    def __init__(self, private: bytes, peers, ice_servers: Callable[[], list] = lambda: [],
                 clock: Callable[[], float] = time.time):
        self.private = private
        self.public = crypto.public_key(private)
        self.device_id = P.device_id_for(self.public)
        self.peers = peers
        self.ice_servers = ice_servers
        self.clock = clock
        self.replay = P.ReplayGuard()
        self.session: Optional[Session] = None
        self.keys: Dict[str, bytes] = {}             # device_id -> public key, for every allowed device

    @property
    def state(self) -> str:
        return self.session.state if self.session else IDLE

    def _signed(self, kind: str, session: Session, **fields) -> Action:
        msg = P.routed(kind, self.device_id, session.peer_id, session_id=session.session_id, **fields)
        msg["ts"] = int(self.clock())
        return Action("send", P.sign(msg, self.private, session.peer_public), session)

    def _deny(self, msg: dict, public: bytes, reason: str) -> List[Action]:
        out = P.routed("session.deny", self.device_id, msg["from"], session_id=msg["session_id"], reason=reason)
        out["ts"] = int(self.clock())
        return [Action("send", P.sign(out, self.private, public))]

    def refresh_keys(self):
        self.keys = {d["device_id"]: P.unb64(d["pubkey"], 32) for d in P.allowed_devices(self.peers)}

    def peer_key(self, device_id: str) -> Optional[bytes]:
        if device_id not in self.keys:
            self.refresh_keys()
        return self.keys.get(device_id)

    def on_message(self, msg: dict) -> List[Action]:
        """A routed message from the relay. Unauthenticated or unexpected messages are dropped (logged)."""
        kind = msg.get("type")
        public = self.peer_key(msg.get("from", ""))
        if public is None:
            log.info("remote.refused %s from unknown/revoked %s", kind, msg.get("from"))
            return []
        if kind in P.SIGNED_TYPES:
            try:
                P.verify(msg, self.private, public, now=self.clock())
                self.replay.check(msg, now=self.clock())
            except P.ProtocolError as e:
                log.warning("remote.bad_message %s from %s: %s", kind, msg.get("from"), e.reason)
                return []
        s = self.session
        if kind == "session.request":
            return self._on_request(msg, public)
        if s is None or msg.get("session_id") != s.session_id or msg["from"] != s.peer_id:
            return []
        if kind == "sdp.answer" and s.state == NEGOTIATING:
            return [Action("remote_sdp", msg, s)]
        if kind == "ice" and s.state in (NEGOTIATING, CONNECTED):
            return [Action("remote_ice", msg, s)]
        if kind == "session.end":
            return self.end("peer_ended")
        return []

    def _on_request(self, msg: dict, public: bytes) -> List[Action]:
        decision = P.authorize(self.peers, public)
        if decision.refused:
            return self._deny(msg, public, decision.reason)
        if self.session is not None and self.session.state != ENDED:
            if self.session.peer_id == msg["from"]:           # the same device reconnecting: replace it
                ended = self.end("replaced")
            else:
                return self._deny(msg, public, "busy")
        else:
            ended = []
        want = msg["want"] if isinstance(msg.get("want"), dict) else {}
        name = decision.peer.name if decision.peer is not None else msg.get("client", "")
        self.session = Session(msg["session_id"], msg["from"], public, name, want, since=self.clock(),
                               last_input=self.clock())
        if decision.outcome == P.ALLOW:
            return ended + self._accept()
        return ended + [Action("ask", None, self.session)]

    def _accept(self) -> List[Action]:
        s = self.session
        s.state, s.since = NEGOTIATING, self.clock()
        return [self._signed("session.accept", s, ice_servers=self.ice_servers()), Action("start_media", None, s)]

    def on_user_answer(self, session_id: str, allowed: bool) -> List[Action]:
        s = self.session
        if s is None or s.session_id != session_id or s.state != ASKING:
            return []
        if not allowed:
            self.session = None
            return [self._signed("session.deny", s, reason="declined")]
        return self._accept()

    def local_sdp(self, sdp: str) -> List[Action]:
        s = self.session
        if s is None or s.state != NEGOTIATING:
            return []
        return [self._signed("sdp.offer", s, sdp=sdp)]

    def local_ice(self, candidate: dict) -> List[Action]:
        s = self.session
        if s is None or s.state not in (NEGOTIATING, CONNECTED):
            return []
        return [Action("send", P.routed("ice", self.device_id, s.peer_id, session_id=s.session_id,
                                        candidate=candidate), s)]

    def on_connected(self):
        if self.session is not None and self.session.state == NEGOTIATING:
            self.session.state = CONNECTED
            self.session.last_input = self.clock()

    def on_input(self):
        if self.session is not None:
            self.session.last_input = self.clock()

    def end(self, reason: str) -> List[Action]:
        s = self.session
        if s is None or s.state == ENDED:
            return []
        s.state, s.end_reason = ENDED, reason
        self.session = None
        out = [Action("stop_media", None, s)]
        if reason not in ("peer_ended",):
            out.insert(0, self._signed("session.end", s, reason=reason))
        return out

    def tick(self) -> List[Action]:
        """Called about once a second: timeouts, and devices revoked while connected."""
        s = self.session
        if s is None:
            return []
        now = self.clock()
        if s.state == ASKING and now - s.since > ASK_TIMEOUT:
            self.session = None
            return [self._signed("session.deny", s, reason="no_answer")]
        if s.state == NEGOTIATING and now - s.since > NEGOTIATE_TIMEOUT:
            return self.end("connect_timeout")
        if s.state == CONNECTED and now - s.last_input > IDLE_TIMEOUT:
            return self.end("idle")
        if P.authorize(self.peers, s.peer_public).refused:
            return self.end("revoked")
        return []


def new_session_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------- #
# Control channel -> xdg-desktop-portal RemoteDesktop
# ---------------------------------------------------------------------- #
BTN = {1: 0x110, 2: 0x112, 3: 0x111}             # BTN_LEFT, BTN_MIDDLE, BTN_RIGHT (linux/input-event-codes.h)
KEYSYM = {"\n": 0xFF0D, "\r": 0xFF0D, "\t": 0xFF09, "\b": 0xFF08}


def keysym_for(ch: str) -> int:
    """X11 keysym for one character: Latin-1 maps directly, everything else is 0x01000000 + code point."""
    if ch in KEYSYM:
        return KEYSYM[ch]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 + cp


@dataclass
class PortalCall:
    """One org.freedesktop.portal.RemoteDesktop method call (session handle/options added by the glue)."""
    method: str
    args: tuple


class InputMapper:
    """Turns validated ``control`` messages into portal calls, and remembers what is held down so a
    dropped connection never leaves a key or button stuck."""

    def __init__(self, stream_node: int, width: int, height: int):
        self.stream = stream_node
        self.width, self.height = width, height
        self.keys_down: set = set()
        self.buttons_down: set = set()

    def resize(self, width: int, height: int):
        self.width, self.height = width, height

    def map(self, msg: dict) -> List[PortalCall]:
        kind = msg["type"]
        if kind == "pointer.move":
            x = min(max(float(msg["x"]), 0.0), 1.0) * (self.width - 1)
            y = min(max(float(msg["y"]), 0.0), 1.0) * (self.height - 1)
            return [PortalCall("NotifyPointerMotionAbsolute", (self.stream, x, y))]
        if kind == "pointer.button":
            code = BTN[msg["button"]]
            if msg["down"]:
                self.buttons_down.add(code)
            elif code not in self.buttons_down:
                return []
            else:
                self.buttons_down.discard(code)
            return [PortalCall("NotifyPointerButton", (code, 1 if msg["down"] else 0))]
        if kind == "pointer.scroll":
            dx = min(max(float(msg["dx"]), -2000.0), 2000.0)
            dy = min(max(float(msg["dy"]), -2000.0), 2000.0)
            if dx == 0 and dy == 0:
                return []
            return [PortalCall("NotifyPointerAxis", (dx, dy))]
        if kind == "key":
            code = msg["code"]
            if msg["down"]:
                self.keys_down.add(code)
            elif code not in self.keys_down:
                return []
            else:
                self.keys_down.discard(code)
            return [PortalCall("NotifyKeyboardKeycode", (code, 1 if msg["down"] else 0))]
        if kind == "text":
            calls = []
            for ch in msg["utf8"][:1000]:
                sym = keysym_for(ch)
                calls += [PortalCall("NotifyKeyboardKeysym", (sym, 1)), PortalCall("NotifyKeyboardKeysym", (sym, 0))]
            return calls
        return []

    def release_all(self) -> List[PortalCall]:
        calls = [PortalCall("NotifyKeyboardKeycode", (k, 0)) for k in sorted(self.keys_down)]
        calls += [PortalCall("NotifyPointerButton", (b, 0)) for b in sorted(self.buttons_down)]
        self.keys_down.clear()
        self.buttons_down.clear()
        return calls


# ---------------------------------------------------------------------- #
# Files channel
# ---------------------------------------------------------------------- #
def safe_filename(name: str) -> str:
    name = posixpath.basename(name.replace("\\", "/")).strip().strip(".")
    name = re.sub(r"[\x00-\x1f/:*?\"<>|]", "_", name)[:200]
    return name or "file"


class FileReceiver:
    """file.offer -> file.accept -> 64 KiB binary chunks -> file.done, into ~/Downloads/Halo Remote/."""

    def __init__(self, folder: Optional[str] = None):
        self.folder = folder or os.path.join(os.path.expanduser("~"), "Downloads", "Halo Remote")
        self.current: Optional[dict] = None

    def offer(self, msg: dict) -> dict:
        if self.current is not None:
            return {"type": "file.done", "id": msg["id"], "ok": False}
        if not 0 <= msg["size"] <= MAX_FILE or not re.fullmatch(r"[0-9a-f]{64}", msg["sha256"]):
            return {"type": "file.done", "id": msg["id"], "ok": False}
        os.makedirs(self.folder, exist_ok=True)
        part = os.path.join(self.folder, ".%s.part" % hashlib.sha256(msg["id"].encode()).hexdigest()[:16])
        self.current = {"id": msg["id"], "name": safe_filename(msg["name"]), "size": msg["size"],
                        "sha256": msg["sha256"], "part": part, "got": 0, "hash": hashlib.sha256(),
                        "file": open(part, "wb")}
        return {"type": "file.accept", "id": msg["id"]}

    def chunk(self, data: bytes) -> Optional[dict]:
        """A binary message. Returns file.done once the declared size has arrived."""
        c = self.current
        if c is None:
            return None
        if c["got"] + len(data) > c["size"]:
            return self._finish(False)
        c["file"].write(data)
        c["hash"].update(data)
        c["got"] += len(data)
        if c["got"] == c["size"]:
            return self._finish(c["hash"].hexdigest() == c["sha256"])
        return None

    def _finish(self, ok: bool) -> dict:
        c, self.current = self.current, None
        c["file"].close()
        if ok:
            base, ext = os.path.splitext(c["name"])
            dest, n = os.path.join(self.folder, c["name"]), 1
            while os.path.exists(dest):
                n += 1
                dest = os.path.join(self.folder, "%s (%d)%s" % (base, n, ext))
            os.replace(c["part"], dest)
            c["dest"] = dest
            log.info("remote.file_received %s (%d bytes)", dest, c["size"])
        else:
            os.remove(c["part"])
        self.last = c
        return {"type": "file.done", "id": c["id"], "ok": ok}

    def abort(self):
        if self.current is not None:
            self._finish(False)


# ---------------------------------------------------------------------- #
# GStreamer
# ---------------------------------------------------------------------- #
ENCODERS = [  # (codec, element, settings) — first one the system has wins, H.264 before VP8
    ("H264", "vah264lpenc", "vah264lpenc bitrate={kbps} key-int-max=60 rate-control=cbr"),
    ("H264", "vah264enc", "vah264enc bitrate={kbps} key-int-max=60 rate-control=cbr"),
    ("H264", "nvh264enc", "nvh264enc bitrate={kbps} gop-size=60 preset=low-latency-hq zerolatency=true"),
    ("H264", "x264enc", "x264enc bitrate={kbps} key-int-max=60 tune=zerolatency speed-preset=ultrafast threads=4"),
    # for clients without an H.264 decoder (SAINT's desktop app: Qt WebEngine ships without proprietary codecs)
    ("VP8", "vp8enc", "vp8enc deadline=1 cpu-used=8 target-bitrate={bps} keyframe-max-dist=60 threads=4 "
                      "end-usage=cbr error-resilient=partitions"),
]
CODECS = ("H264", "VP8")


def client_codecs(want: dict) -> List[str]:
    """What the client said it decodes (``want.codecs``); clients from before that field all decode H.264."""
    raw = want.get("codecs") if isinstance(want, dict) else None
    if not isinstance(raw, list):
        return ["H264"]
    return [c for c in CODECS if c in [str(x).upper() for x in raw]]


def pick_encoder(available: Callable[[str], bool], codecs=("H264",)) -> str:
    for codec, name, _ in ENCODERS:
        if codec in codecs and available(name):
            return name
    raise RuntimeError("No encoder for %s: install gst-plugins-bad (VA-API), gst-plugins-ugly (x264) or "
                       "gst-plugins-good (vp8enc)." % "/".join(codecs or ["-"]))


def pipeline_description(fd: int, node_id: int, encoder: str, want: dict) -> str:
    """pipewiresrc (portal stream) -> scale/rate to what the client asked for -> H.264 (constrained baseline:
    Safari/iOS decode it in hardware) or VP8 -> RTP -> webrtcbin."""
    max_w = min(max(int(want.get("max_w") or 1920), 320), 3840)
    max_h = min(max(int(want.get("max_h") or 1080), 240), 2160)
    fps = min(max(int(want.get("fps") or 30), 5), 60)
    kbps = min(max(int(max_w * max_h * fps / 10000), 1500), 20000)
    codec, _, settings = next(e for e in ENCODERS if e[1] == encoder)
    enc = settings.format(kbps=kbps, bps=kbps * 1000)
    if codec == "H264":
        fmt, tail = "NV12", (
            "{enc} ! video/x-h264,profile=constrained-baseline ! h264parse config-interval=-1 ! "
            "rtph264pay config-interval=-1 aggregate-mode=zero-latency pt=96 ! "
            "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000").format(enc=enc)
    else:
        fmt, tail = "I420", (
            "{enc} ! rtpvp8pay pt=97 picture-id-mode=15-bit ! "
            "application/x-rtp,media=video,encoding-name=VP8,payload=97,clock-rate=90000").format(enc=enc)
    return (
        "pipewiresrc fd={fd} path={node} do-timestamp=true keepalive-time=1000 ! "
        "videoconvert ! videoscale add-borders=true ! videorate drop-only=true ! "
        "video/x-raw,format={fmt},framerate={fps}/1,width=[16,{w}],height=[16,{h}],pixel-aspect-ratio=1/1 ! "
        "queue max-size-buffers=1 leaky=downstream ! {tail} ! "
        "webrtcbin name=webrtc bundle-policy=max-bundle latency=0"
    ).format(fd=fd, node=node_id, fps=fps, w=max_w, h=max_h, fmt=fmt, tail=tail)


def webrtcbin_ice(ice_servers: list) -> Tuple[Optional[str], List[str]]:
    """RTCIceServer list -> (stun-server, [turn-server URIs]) in webrtcbin's URI form
    ``stun://host:port`` / ``turn(s)://user:pass@host:port`` (user and pass percent-encoded)."""
    stun, turns = None, []
    for server in ice_servers:
        urls = server.get("urls") or []
        for url in [urls] if isinstance(urls, str) else urls:
            m = re.match(r"^(stun|turns?):([^?]+)(\?transport=(udp|tcp))?$", url)
            if not m:
                continue
            scheme, hostport, transport = m.group(1), m.group(2), m.group(4)
            if scheme == "stun":
                stun = stun or "stun://" + hostport
            elif server.get("username") and server.get("credential"):
                auth = "%s:%s@" % (urllib.parse.quote(server["username"], safe=""),
                                   urllib.parse.quote(server["credential"], safe=""))
                uri = "%s://%s%s" % (scheme, auth, hostport)
                if transport:
                    uri += "?transport=" + transport
                turns.append(uri)
    return stun, turns


# ---------------------------------------------------------------------- #
# Enable / disable / CLI
# ---------------------------------------------------------------------- #
UNIT = """# Halo Remote host: lets your paired devices see and control this PC (docs/REMOTE_DESKTOP.md).
#   systemctl --user status halo-remote ; journalctl --user -u halo-remote -f
[Unit]
Description=Halo Remote (remote desktop host)
PartOf=graphical-session.target
After=graphical-session.target pipewire.service xdg-desktop-portal.service
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={root}
Environment=PYTHONPATH={root}
ExecStart=/usr/bin/python3 -m modules.remote_desktop.host run
Restart=on-failure
RestartSec=10
NoNewPrivileges=true

[Install]
WantedBy=graphical-session.target
"""


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def unit_path() -> str:
    """The packaged unit if Halo installed one, else a per-user copy written on enable."""
    packaged = "/usr/lib/systemd/user/" + SERVICE
    if os.path.exists(packaged):
        return packaged
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "systemd", "user", SERVICE)


def _systemctl(*args) -> int:
    if not shutil.which("systemctl"):
        print("systemctl isn't available here: Halo Remote's service runs on Halo OS / Linux only.")
        return 1
    return subprocess.call(["systemctl", "--user"] + list(args))


def cmd_enable(_args) -> int:
    path = unit_path()
    if not path.startswith("/usr/"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(UNIT.format(root=project_root()))
        _systemctl("daemon-reload")
    if not HostConfig.load().relay_url:
        print("Note: no relay set yet (halo-remote set-relay wss://…/ws); it will wait for one.")
    return _systemctl("enable", "--now", SERVICE)


def cmd_disable(_args) -> int:
    return _systemctl("disable", "--now", SERVICE)


def cmd_status(_args) -> int:
    cfg = HostConfig.load()
    print("relay:", cfg.relay_url or "(not set)")
    try:
        _, public, name = load_device_key()
        print("this PC:", name, P.device_id_for(public), "account", P.account_id_for(public))
    except KeyUnavailable as e:
        print("key:", e)
    return _systemctl("status", "--no-pager", SERVICE) if shutil.which("systemctl") else 0


def cmd_set_relay(args) -> int:
    cfg = HostConfig.load()
    cfg.relay_url = check_relay_url(args.url)
    cfg.save()
    print("relay set to", cfg.relay_url, "- restart the service: systemctl --user restart", SERVICE)
    return 0


def cmd_pair(_args) -> int:
    cfg = HostConfig.load()
    _, public, name = load_device_key()
    code = RelayPairing().create()
    print("Pairing code (5 minutes, one use):", code)
    if cfg.relay_url:
        print("Open on the new device:", pairing_link(cfg.relay_url, code, P.device_id_for(public),
                                                      P.account_id_for(public)))
    return 0


def cmd_run(_args) -> int:
    from modules.remote_desktop import media              # GLib/GStreamer: Linux desktop only
    return media.main()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="halo-remote", description="Halo Remote host")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("enable", cmd_enable), ("disable", cmd_disable), ("status", cmd_status),
                     ("pair", cmd_pair), ("run", cmd_run)]:
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("set-relay")
    p.add_argument("url")
    p.set_defaults(fn=cmd_set_relay)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (KeyUnavailable, ValueError) as e:
        print(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
