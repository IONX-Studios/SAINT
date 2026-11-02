"""
modules/remote_desktop/protocol.py

Halo Remote protocol v1 (docs/REMOTE_DESKTOP.md): the messages, how they are checked, and how they are
authenticated with the SAINT Link device keys.

Link keys are X25519 (key agreement), which can't make signatures. So a "signature" (``sig``) here is an
HMAC-SHA256 under a key both paired devices can derive and nobody else can:

    K   = HKDF-SHA256(X25519(my_private, peer_public), salt="halo-remote/v1",
                      info="session-mac" || min(pub_a, pub_b) || max(pub_a, pub_b))
    sig = base64(HMAC-SHA256(K, "halo-remote/v1 sig\\0" || canonical(message without "sig")))

The relay never holds either private key, so it can route a signed message but can't forge or alter one
(the DTLS fingerprints in the SDP are covered, so it can't sit in the middle of the media either). Each
message carries ``from`` and ``to``; the verifier checks ``from`` is the peer whose key it used, so a
message can't be reflected back to its sender.

Logging in to the relay proves possession of a key against a one-time relay key:

    proof = base64(HMAC-SHA256(HKDF(X25519(device_private, relay_ephemeral_public), salt, "relay-login"),
                               nonce || device_id || 0x00 || role))

Pairing through the relay (a browser can't open Link's TCP socket) is authenticated with the pairing
code's pre-shared key (modules/link/identity.py ``psk_for``):

    mac = base64(HMAC-SHA256(HKDF(psk, salt, "pair"), "halo-remote/v1 pair\\0" || canonical(message)))

canonical(): JSON with keys sorted, no whitespace, non-ASCII as UTF-8, only the escapes JSON.stringify
makes. Signed messages may hold strings, integers, booleans, null, lists and objects — no floats, so the
Python, JavaScript and Swift encoders agree byte for byte (tests/data/remote_vectors.json).

This module depends only on modules/link/crypto.py, so the relay container can ship it on its own.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from modules.link import crypto

VERSION = 1
SALT = b"halo-remote/v1"
MAX_SKEW = 300                      # seconds a signed message may be early or late
MAX_MESSAGE = 64 * 1024             # bytes of JSON accepted from the relay or a peer
PERMISSION = "remote_desktop"       # modules/link/identity.py PERMISSIONS
HOST, CLIENT = "host", "client"


class ProtocolError(Exception):
    """A message that isn't protocol v1, or fails authentication. ``reason`` is safe to send back."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------- #
# Encoding helpers
# ---------------------------------------------------------------------- #
def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text, size: Optional[int] = None) -> bytes:
    try:
        raw = base64.b64decode(text, validate=True) if isinstance(text, str) else None
    except (binascii.Error, ValueError):
        raw = None
    if raw is None or (size is not None and len(raw) != size):
        raise ProtocolError("bad_base64")
    return raw


def device_id_for(public_key: bytes) -> str:
    """Same as modules/link/identity.py: first 16 hex characters of SHA-256(public key)."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def account_id_for(host_public_key: bytes) -> str:
    """A relay account is one PC: SHA-256 of its Link public key, in hex."""
    return hashlib.sha256(host_public_key).hexdigest()


def _check_canonical(value, path="$"):
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return
    if isinstance(value, float):
        raise ProtocolError("float_in_signed_message:" + path)
    if isinstance(value, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ProtocolError("surrogate_in_string:" + path)
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            _check_canonical(v, "%s[%d]" % (path, i))
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or not k.isascii():
                raise ProtocolError("non_ascii_key:" + path)
            _check_canonical(v, path + "." + k)
        return
    raise ProtocolError("unsupported_value:" + path)


def canonical(obj) -> bytes:
    _check_canonical(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _mac(key: bytes, label: bytes, obj) -> str:
    return b64(hmac.new(key, b"halo-remote/v1 " + label + b"\x00" + canonical(obj), hashlib.sha256).digest())


# ---------------------------------------------------------------------- #
# Schemas
# ---------------------------------------------------------------------- #
# field kinds: str, int, bool, num (int or float), obj, list, key (base64 32-byte X25519 key), id (16 hex)
_ROUTED = {"from": "id", "to": "id"}
_SIGNED = dict(_ROUTED, session_id="str", ts="int", sig="str")

SCHEMAS: Dict[str, Dict[str, str]] = {
    # device <-> relay
    "hello": {"v": "int", "device_id": "id", "role": "str", "pubkey": "key", "name": "str"},
    "relay.challenge": {"nonce": "str", "eph": "key"},
    "relay.proof": {"sig": "str"},
    "welcome": {"device_id": "id", "ice_servers": "list"},
    "presence": {"hosts": "list"},
    "devices": {"keys": "list"},                    # host -> relay: who may reach this PC
    "error": {"reason": "str"},
    # client <-> host, routed by the relay
    "session.request": dict(_SIGNED, client="str", want="obj"),
    "session.accept": dict(_SIGNED, ice_servers="list"),
    "session.deny": dict(_SIGNED, reason="str"),
    "sdp.offer": dict(_SIGNED, sdp="str"),
    "sdp.answer": dict(_SIGNED, sdp="str"),
    "session.end": dict(_SIGNED, reason="str"),
    "ice": dict(_ROUTED, session_id="str", candidate="obj"),
    "pair.request": dict(_ROUTED, pubkey="key", name="str", platform="str", ts="int", mac="str"),
    "pair.accept": dict(_ROUTED, pubkey="key", name="str", ts="int", mac="str"),
    "pair.deny": dict(_ROUTED, reason="str"),
}
SIGNED_TYPES = frozenset(t for t, s in SCHEMAS.items() if "sig" in s)
ROUTED_TYPES = frozenset(t for t, s in SCHEMAS.items() if "to" in s)

# Data channel "control" (DTLS-protected, not signed; floats allowed)
CONTROL_SCHEMAS: Dict[str, Dict[str, str]] = {
    "pointer.move": {"x": "num", "y": "num"},
    "pointer.button": {"button": "int", "down": "bool"},
    "pointer.scroll": {"dx": "num", "dy": "num"},
    "key": {"code": "int", "down": "bool"},
    "text": {"utf8": "str"},
    "clipboard": {"mime": "str", "data": "str"},
    "display": {"w": "int", "h": "int", "scale": "num"},
    "stats": {"rtt_ms": "num", "fps": "num", "kbps": "num", "path": "str"},
}
# Data channel "files"
FILE_SCHEMAS: Dict[str, Dict[str, str]] = {
    "file.offer": {"id": "str", "name": "str", "size": "int", "sha256": "str"},
    "file.accept": {"id": "str"},
    "file.done": {"id": "str", "ok": "bool"},
}


def _kind_ok(kind: str, value) -> bool:
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
            and abs(value) != float("inf")
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "obj":
        return isinstance(value, dict)
    if kind == "list":
        return isinstance(value, list)
    if kind == "id":
        return isinstance(value, str) and len(value) == 16 and all(c in "0123456789abcdef" for c in value)
    if kind == "key":
        try:
            unb64(value, 32)
            return True
        except ProtocolError:
            return False
    return False


def validate(msg, schemas: Dict[str, Dict[str, str]] = SCHEMAS) -> dict:
    """Return ``msg`` if it is a known message with every field of the right kind; else ProtocolError."""
    if not isinstance(msg, dict):
        raise ProtocolError("not_an_object")
    kind = msg.get("type")
    schema = schemas.get(kind) if isinstance(kind, str) else None
    if schema is None:
        raise ProtocolError("unknown_type")
    for name, field_kind in schema.items():
        if name not in msg:
            raise ProtocolError("missing:" + name)
        if not _kind_ok(field_kind, msg[name]):
            raise ProtocolError("bad:" + name)
    if kind == "hello":
        if msg["v"] != VERSION:
            raise ProtocolError("unsupported_version")
        if msg["role"] not in (HOST, CLIENT):
            raise ProtocolError("bad:role")
        if device_id_for(unb64(msg["pubkey"], 32)) != msg["device_id"]:
            raise ProtocolError("device_id_mismatch")
    if kind == "pair.request" and device_id_for(unb64(msg["pubkey"], 32)) != msg["from"]:
        raise ProtocolError("device_id_mismatch")
    if kind == "pointer.button" and msg["button"] not in (1, 2, 3):
        raise ProtocolError("bad:button")
    if kind == "key" and not 0 < msg["code"] < 768:
        raise ProtocolError("bad:code")
    return msg


def parse(raw, schemas: Dict[str, Dict[str, str]] = SCHEMAS) -> dict:
    """Bytes/str off the wire -> a validated message."""
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_MESSAGE:
            raise ProtocolError("too_large")
        raw = bytes(raw).decode("utf-8", "strict")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MESSAGE:
        raise ProtocolError("too_large")
    try:
        msg = json.loads(raw)
    except ValueError:
        raise ProtocolError("bad_json")
    return validate(msg, schemas)


# ---------------------------------------------------------------------- #
# Device-to-device signatures
# ---------------------------------------------------------------------- #
def session_key(private: bytes, peer_public: bytes) -> bytes:
    own = crypto.public_key(private)
    low, high = sorted([own, peer_public])
    return crypto.hkdf(crypto.shared_secret(private, peer_public), SALT, b"session-mac" + low + high)


def sign(msg: dict, private: bytes, peer_public: bytes) -> dict:
    """A copy of ``msg`` with ``from`` (if missing) and ``sig`` filled in."""
    out = {k: v for k, v in msg.items() if k != "sig"}
    out.setdefault("from", device_id_for(crypto.public_key(private)))
    out["sig"] = _mac(session_key(private, peer_public), b"sig", out)
    return out


def verify(msg: dict, private: bytes, peer_public: bytes, now: Optional[float] = None) -> dict:
    """Check a signed message came from ``peer_public``, for us, recently. Returns it or raises."""
    validate(msg)
    if msg["type"] not in SIGNED_TYPES:
        raise ProtocolError("not_signed_type")
    if msg["from"] != device_id_for(peer_public):
        raise ProtocolError("wrong_sender")
    if msg["to"] != device_id_for(crypto.public_key(private)):
        raise ProtocolError("wrong_recipient")
    body = {k: v for k, v in msg.items() if k != "sig"}
    if not crypto.constant_time_equal(_mac(session_key(private, peer_public), b"sig", body).encode(),
                                      msg["sig"].encode()):
        raise ProtocolError("bad_signature")
    check_fresh(msg, now)
    return msg


def check_fresh(msg: dict, now: Optional[float] = None):
    now = time.time() if now is None else now
    if abs(now - msg["ts"]) > MAX_SKEW:
        raise ProtocolError("stale")


class ReplayGuard:
    """Refuses a signed message seen before (same sender, type, session, timestamp and signature)."""

    def __init__(self, window: float = 2 * MAX_SKEW):
        self.window = window
        self._seen: Dict[str, float] = {}

    def check(self, msg: dict, now: Optional[float] = None):
        now = time.time() if now is None else now
        self._seen = {k: t for k, t in self._seen.items() if now - t < self.window}
        key = "|".join((msg["from"], msg["type"], msg["session_id"], str(msg["ts"]), msg["sig"]))
        if key in self._seen:
            raise ProtocolError("replayed")
        self._seen[key] = now


# ---------------------------------------------------------------------- #
# Relay login
# ---------------------------------------------------------------------- #
def new_challenge() -> tuple:
    """(message to send, ephemeral private key to keep)."""
    eph_private, eph_public = crypto.generate_keypair()
    return {"type": "relay.challenge", "nonce": b64(os.urandom(32)), "eph": b64(eph_public)}, eph_private


def _relay_key(shared: bytes) -> bytes:
    return crypto.hkdf(shared, SALT, b"relay-login")


def _relay_input(nonce: bytes, device_id: str, role: str) -> bytes:
    return nonce + device_id.encode("ascii") + b"\x00" + role.encode("ascii")


def relay_proof(private: bytes, challenge: dict, role: str) -> dict:
    validate(challenge)
    key = _relay_key(crypto.shared_secret(private, unb64(challenge["eph"], 32)))
    device_id = device_id_for(crypto.public_key(private))
    sig = hmac.new(key, _relay_input(unb64(challenge["nonce"], 32), device_id, role), hashlib.sha256).digest()
    return {"type": "relay.proof", "sig": b64(sig)}


def check_relay_proof(eph_private: bytes, challenge: dict, hello: dict, proof: dict) -> bool:
    validate(hello)
    validate(proof)
    device_public = unb64(hello["pubkey"], 32)
    key = _relay_key(crypto.shared_secret(eph_private, device_public))
    want = hmac.new(key, _relay_input(unb64(challenge["nonce"], 32), hello["device_id"], hello["role"]),
                    hashlib.sha256).digest()
    try:
        got = unb64(proof["sig"], 32)
    except ProtocolError:
        return False
    return crypto.constant_time_equal(want, got)


# ---------------------------------------------------------------------- #
# Pairing through the relay
# ---------------------------------------------------------------------- #
def _pair_key(psk: bytes) -> bytes:
    return crypto.hkdf(psk, SALT, b"pair")


def pair_mac(msg: dict, psk: bytes) -> dict:
    out = {k: v for k, v in msg.items() if k != "mac"}
    out["mac"] = _mac(_pair_key(psk), b"pair", out)
    return out


def check_pair_mac(msg: dict, psk: bytes, now: Optional[float] = None) -> dict:
    validate(msg)
    body = {k: v for k, v in msg.items() if k != "mac"}
    if not crypto.constant_time_equal(_mac(_pair_key(psk), b"pair", body).encode(), msg["mac"].encode()):
        raise ProtocolError("bad_code")
    check_fresh(msg, now)
    return msg


# ---------------------------------------------------------------------- #
# Who may connect
# ---------------------------------------------------------------------- #
ALLOW, ASK, DENY = "allow", "ask", "deny"


@dataclass
class Decision:
    outcome: str                    # allow | ask | deny
    reason: str                     # ok | revoked | not_paired | not_allowed
    peer: object = None

    @property
    def refused(self) -> bool:
        return self.outcome == DENY


def authorize(peers, public_key: bytes) -> Decision:
    """May the device holding ``public_key`` use remote desktop on this PC?

    ``peers`` is a modules.link.identity.PeerStore (anything with ``by_key`` and ``revoked``). A revoked
    device is refused even if a stale copy of it is still listed somewhere."""
    device_id = device_id_for(public_key)
    if device_id in peers.revoked():
        return Decision(DENY, "revoked")
    peer = peers.by_key(public_key)
    if peer is None or bytes.fromhex(peer.public_key) != public_key:
        return Decision(DENY, "not_paired")
    policy = peer.permission(PERMISSION)
    if policy == ALLOW:
        return Decision(ALLOW, "ok", peer)
    if policy == ASK:
        return Decision(ASK, "ok", peer)
    return Decision(DENY, "not_allowed", peer)


def allowed_devices(peers) -> list:
    """What the host tells the relay: every paired device that may ask for remote desktop."""
    out = []
    for p in peers.all():
        if p.permission(PERMISSION) in (ALLOW, ASK) and p.id not in peers.revoked():
            out.append({"device_id": p.id, "pubkey": b64(bytes.fromhex(p.public_key)), "name": p.name})
    return out


def routed(kind: str, sender: str, recipient: str, **fields) -> dict:
    """A routed message skeleton (``ts`` added for the types that carry one)."""
    msg = {"type": kind, "from": sender, "to": recipient}
    if "ts" in SCHEMAS[kind]:
        msg["ts"] = int(time.time())
    msg.update(fields)
    return msg


def iter_hosts(presence: dict) -> Iterable[dict]:
    for h in presence.get("hosts", []):
        if isinstance(h, dict) and _kind_ok("id", h.get("device_id")):
            yield h


__all__ = ["VERSION", "ProtocolError", "SCHEMAS", "CONTROL_SCHEMAS", "FILE_SCHEMAS", "canonical", "validate",
           "parse", "sign", "verify", "relay_proof", "check_relay_proof", "new_challenge", "pair_mac",
           "check_pair_mac", "authorize", "allowed_devices", "Decision", "ReplayGuard", "account_id_for",
           "device_id_for", "routed", "iter_hosts", "b64", "unb64"]
