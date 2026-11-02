"""
Halo Relay — the signaling service for Halo Remote (docs/REMOTE_DESKTOP.md).

Halo PCs and their paired phones/browsers dial *out* to this service over WSS. It

  * logs each device in with a challenge it must answer with its SAINT Link key (no passwords),
  * keeps presence: which PCs are online, and who may reach each one (the PC itself says so),
  * passes routed messages (session.*, sdp.*, ice, pair.*) between a PC and a device that PC allowed —
    nothing else; it can't read media, keystrokes or files (those go peer-to-peer over WebRTC, or through
    TURN, encrypted end to end) and can't forge the signed messages it carries,
  * hands out short-lived TURN credentials for the coturn next to it,
  * serves the browser client (web/remote) as static files.

State is in memory: after a restart PCs log in again within seconds and re-send their device lists.

    python -m server.relay.relay --port 8080          (from the repo root; see server/relay/README.md)
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from aiohttp import WSMsgType, web

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.remote_desktop import protocol as P  # noqa: E402

log = logging.getLogger("halo.relay")

LOGIN_TIMEOUT = 10.0
MAX_MSGS_PER_SECOND = 200           # ICE bursts are the busiest moment; far above that is abuse
PAIR_ATTEMPTS_PER_MINUTE = 5        # per address: the code is short, the PC also counts failures
TURN_TTL = 12 * 3600


@dataclass
class Config:
    stun_urls: List[str] = field(default_factory=list)
    turn_urls: List[str] = field(default_factory=list)
    turn_secret: str = ""
    web_root: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        split = lambda v: [u.strip() for u in (v or "").split(",") if u.strip()]  # noqa: E731
        return cls(stun_urls=split(os.environ.get("HALO_STUN_URLS")),
                   turn_urls=split(os.environ.get("HALO_TURN_URLS")),
                   turn_secret=os.environ.get("HALO_TURN_SECRET", ""),
                   web_root=os.environ.get("HALO_WEB_ROOT", os.path.join(ROOT, "web", "remote")))

    def ice_servers(self, device_id: str, now: Optional[float] = None) -> list:
        """STUN, plus TURN with coturn's time-limited REST credentials (use-auth-secret)."""
        out = [{"urls": self.stun_urls}] if self.stun_urls else []
        if self.turn_urls and self.turn_secret:
            username = "%d:%s" % (int((now or time.time()) + TURN_TTL), device_id)
            cred = base64.b64encode(hmac.new(self.turn_secret.encode(), username.encode(), hashlib.sha1).digest())
            out.append({"urls": self.turn_urls, "username": username, "credential": cred.decode()})
        return out


@dataclass
class Conn:
    ws: web.WebSocketResponse
    device_id: str
    role: str
    pubkey: str
    name: str
    ip: str
    accounts: List[str] = field(default_factory=list)      # client: the PCs it wants to see
    pair_account: str = ""                                 # client: the PC it is pairing with
    pair_hosts: Set[str] = field(default_factory=set)      # host ids it sent pair.request to


@dataclass
class Account:
    """One Halo PC and the devices it lets reach it."""
    host_id: str
    host_name: str
    allowed: Dict[str, str] = field(default_factory=dict)  # device_id -> base64 pubkey
    online: bool = False
    last_seen: float = 0.0


class Relay:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.conns: Dict[str, Conn] = {}
        self.accounts: Dict[str, Account] = {}
        self.host_account: Dict[str, str] = {}             # host device_id -> account id
        self.pair_attempts: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------ #
    # Who may talk to whom
    # ------------------------------------------------------------------ #
    def may_route(self, sender: Conn, target: Conn, kind: str) -> bool:
        if sender.role == target.role:
            return False                                   # no client <-> client, no PC <-> PC
        host, other = (sender, target) if sender.role == P.HOST else (target, sender)
        account = self.accounts.get(self.host_account.get(host.device_id, ""))
        if account is None:
            return False
        if account.allowed.get(other.device_id) == other.pubkey:
            return True
        # pairing: a not-yet-allowed device may only send pair.request to the PC it named in hello,
        # and that PC may only answer it with pair.accept / pair.deny
        if kind == "pair.request" and sender is other:
            return other.pair_account == self.host_account.get(host.device_id)
        if kind in ("pair.accept", "pair.deny") and sender is host:
            return host.device_id in other.pair_hosts
        return False

    def presence_for(self, client: Conn) -> dict:
        hosts = []
        for acct_id in client.accounts:
            acct = self.accounts.get(acct_id)
            if acct is not None and acct.allowed.get(client.device_id) == client.pubkey:
                hosts.append({"account": acct_id, "device_id": acct.host_id, "name": acct.host_name,
                              "online": acct.online, "last_seen": int(acct.last_seen)})
        return {"type": "presence", "hosts": hosts}

    async def send(self, conn: Conn, msg: dict):
        try:
            await conn.ws.send_str(json.dumps(msg, ensure_ascii=False))
        except (ConnectionError, RuntimeError):
            log.debug("relay.send_failed %s", conn.device_id)

    async def broadcast_presence(self, acct_id: str):
        for c in list(self.conns.values()):
            if c.role == P.CLIENT and acct_id in c.accounts:
                await self.send(c, self.presence_for(c))

    def pair_allowed(self, ip: str) -> bool:
        now = time.time()
        recent = [t for t in self.pair_attempts.get(ip, []) if now - t < 60]
        if len(recent) >= PAIR_ATTEMPTS_PER_MINUTE:
            self.pair_attempts[ip] = recent
            return False
        self.pair_attempts[ip] = recent + [now]
        return True

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    async def _receive(self, ws, timeout: float) -> dict:
        msg = await ws.receive(timeout=timeout)
        if msg.type != WSMsgType.TEXT:
            raise P.ProtocolError("expected_text")
        return P.parse(msg.data)

    async def login(self, ws: web.WebSocketResponse, ip: str) -> Conn:
        hello = await self._receive(ws, LOGIN_TIMEOUT)
        if hello["type"] != "hello":
            raise P.ProtocolError("expected_hello")
        accounts = hello.get("accounts", [])
        pair_account = hello.get("pair", "")
        if not isinstance(accounts, list) or not all(isinstance(a, str) and len(a) == 64 for a in accounts) \
                or len(accounts) > 32 or not isinstance(pair_account, str):
            raise P.ProtocolError("bad:accounts")
        challenge, eph = P.new_challenge()
        await ws.send_str(json.dumps(challenge))
        proof = await self._receive(ws, LOGIN_TIMEOUT)
        if proof["type"] != "relay.proof" or not P.check_relay_proof(eph, challenge, hello, proof):
            raise P.ProtocolError("login_failed")
        return Conn(ws, hello["device_id"], hello["role"], hello["pubkey"], hello["name"][:64], ip,
                    accounts=list(dict.fromkeys(accounts)), pair_account=pair_account)

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=P.MAX_MESSAGE)
        await ws.prepare(request)
        ip = request.headers.get("X-Forwarded-For", request.remote or "?").split(",")[0].strip()
        try:
            conn = await self.login(ws, ip)
        except (P.ProtocolError, asyncio.TimeoutError) as e:
            reason = e.reason if isinstance(e, P.ProtocolError) else "timeout"
            await ws.send_str(json.dumps({"type": "error", "reason": reason}))
            await ws.close()
            return ws
        await self.attach(conn)
        try:
            await self.serve(conn)
        finally:
            await self.detach(conn)
        return ws

    async def attach(self, conn: Conn):
        old = self.conns.get(conn.device_id)
        self.conns[conn.device_id] = conn
        if old is not None and old.ws is not conn.ws:
            await old.ws.close(message=b"replaced")
        acct_id = None
        if conn.role == P.HOST:
            acct_id = P.account_id_for(P.unb64(conn.pubkey, 32))
            acct = self.accounts.setdefault(acct_id, Account(conn.device_id, conn.name))
            acct.host_name, acct.online, acct.last_seen = conn.name, True, time.time()
            self.host_account[conn.device_id] = acct_id
        welcome = {"type": "welcome", "device_id": conn.device_id,
                   "ice_servers": self.config.ice_servers(conn.device_id)}
        if acct_id:
            welcome["account"] = acct_id
        await self.send(conn, welcome)
        if conn.role == P.CLIENT:
            await self.send(conn, self.presence_for(conn))
        else:
            await self.broadcast_presence(acct_id)
        log.info("relay.login %s %s", conn.role, conn.device_id)

    async def detach(self, conn: Conn):
        if self.conns.get(conn.device_id) is conn:
            del self.conns[conn.device_id]
            if conn.role == P.HOST:
                acct_id = self.host_account.get(conn.device_id)
                acct = self.accounts.get(acct_id)
                if acct is not None:
                    acct.online, acct.last_seen = False, time.time()
                    await self.broadcast_presence(acct_id)
        log.info("relay.logout %s", conn.device_id)

    async def serve(self, conn: Conn):
        window, count = time.monotonic(), 0
        async for raw in conn.ws:
            if raw.type != WSMsgType.TEXT:
                break
            now = time.monotonic()
            if now - window >= 1.0:
                window, count = now, 0
            count += 1
            if count > MAX_MSGS_PER_SECOND:
                await self.send(conn, {"type": "error", "reason": "rate_limited"})
                break
            try:
                msg = P.parse(raw.data)
                await self.dispatch(conn, msg, raw.data)
            except P.ProtocolError as e:
                await self.send(conn, {"type": "error", "reason": e.reason})

    async def dispatch(self, conn: Conn, msg: dict, raw: str):
        kind = msg["type"]
        if kind == "devices":
            await self.set_devices(conn, msg)
            return
        if kind not in P.ROUTED_TYPES:
            raise P.ProtocolError("not_routable")
        if msg["from"] != conn.device_id:
            raise P.ProtocolError("wrong_sender")
        target = self.conns.get(msg["to"])
        if kind == "pair.request":
            if not self.pair_allowed(conn.ip):
                raise P.ProtocolError("rate_limited")
            if target is not None and self.may_route(conn, target, kind):
                conn.pair_hosts.add(target.device_id)
        if target is None:
            raise P.ProtocolError("offline")
        if not self.may_route(conn, target, kind):
            raise P.ProtocolError("not_allowed")
        await target.ws.send_str(raw)

    async def set_devices(self, conn: Conn, msg: dict):
        if conn.role != P.HOST:
            raise P.ProtocolError("not_allowed")
        allowed = {}
        for d in msg["keys"][:256]:
            if not isinstance(d, dict):
                continue
            try:
                pub = P.unb64(d.get("pubkey"), 32)
            except P.ProtocolError:
                continue
            if P.device_id_for(pub) == d.get("device_id"):
                allowed[d["device_id"]] = d["pubkey"]
        acct_id = self.host_account[conn.device_id]
        acct = self.accounts[acct_id]
        dropped = set(acct.allowed) - set(allowed)
        acct.allowed = allowed
        await self.broadcast_presence(acct_id)
        for dev in dropped:                                # revoked: no more routes, and say so
            c = self.conns.get(dev)
            if c is not None and acct_id in c.accounts:
                await self.send(c, {"type": "error", "reason": "revoked"})


RELAY_KEY = web.AppKey("relay", Relay)


def make_app(relay: Optional[Relay] = None) -> web.Application:
    relay = relay or Relay(Config.from_env())
    app = web.Application()
    app[RELAY_KEY] = relay
    app.router.add_get("/ws", relay.handle)
    app.router.add_get("/healthz", lambda r: web.json_response(
        {"ok": True, "online": len(relay.conns), "accounts": len(relay.accounts)}))
    root = relay.config.web_root
    if root and os.path.isdir(root):
        app.router.add_get("/", lambda r: web.FileResponse(os.path.join(root, "index.html")))
        app.router.add_static("/", root, show_index=False)
    return app


def main(argv=None):
    ap = argparse.ArgumentParser(description="Halo Relay (Halo Remote signaling)")
    ap.add_argument("--host", default=os.environ.get("HALO_RELAY_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("HALO_RELAY_PORT", "8080")))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.run_app(make_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
