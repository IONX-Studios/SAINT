"""Halo Relay (server/relay/relay.py) over real WebSockets on localhost.

Needs aiohttp (server/relay/requirements.txt); skipped without it.
"""
import asyncio
import base64
import hashlib
import hmac
import json

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from modules.link import crypto  # noqa: E402
from modules.link.identity import psk_for  # noqa: E402
from modules.remote_desktop import protocol as P  # noqa: E402
from server.relay.relay import Config, Relay, make_app  # noqa: E402


class Device:
    def __init__(self, role, name="dev"):
        self.private, self.public = crypto.generate_keypair()
        self.role, self.name = role, name
        self.id = P.device_id_for(self.public)
        self.ws = None

    @property
    def account(self):
        return P.account_id_for(self.public)

    def entry(self):
        return {"device_id": self.id, "pubkey": P.b64(self.public), "name": self.name}

    async def login(self, client, accounts=(), pair="", private=None):
        self.ws = await client.ws_connect("/ws")
        hello = {"type": "hello", "v": 1, "device_id": self.id, "role": self.role,
                 "pubkey": P.b64(self.public), "name": self.name, "accounts": list(accounts)}
        if pair:
            hello["pair"] = pair
        await self.ws.send_json(hello)
        challenge = await self.recv()
        assert challenge["type"] == "relay.challenge"
        await self.ws.send_json(P.relay_proof(private or self.private, challenge, self.role))
        return await self.recv()

    async def send(self, msg):
        await self.ws.send_json(msg)

    async def recv(self, timeout=3):
        msg = await self.ws.receive(timeout=timeout)
        return json.loads(msg.data)

    async def recv_type(self, kind, timeout=3):
        while True:
            msg = await self.recv(timeout)
            if msg["type"] == kind:
                return msg


def run(scenario, config=None):
    async def main():
        relay = Relay(config or Config(stun_urls=["stun:relay.test:3478"], turn_urls=["turn:relay.test:3478"],
                                       turn_secret="s3cret"))
        async with TestClient(TestServer(make_app(relay))) as client:
            await scenario(client, relay)
    asyncio.run(main())


def test_login_presence_and_signed_routing():
    async def scenario(client, relay):
        pc, phone = Device("host", "Halo PC"), Device("client", "iPhone")
        welcome = await pc.login(client)
        assert welcome["type"] == "welcome" and welcome["account"] == pc.account
        await pc.send({"type": "devices", "keys": [phone.entry()]})
        welcome = await phone.login(client, accounts=[pc.account])
        assert welcome["type"] == "welcome"
        presence = await phone.recv_type("presence")
        assert presence["hosts"] == [{"account": pc.account, "device_id": pc.id, "name": "Halo PC",
                                      "online": True, "last_seen": presence["hosts"][0]["last_seen"]}]
        req = P.sign(P.routed("session.request", phone.id, pc.id, session_id="s1", client="iPhone",
                              want={"max_w": 1280, "max_h": 720, "fps": 30, "audio": False}),
                     phone.private, pc.public)
        await phone.send(req)
        got = await pc.recv_type("session.request")
        assert P.verify(got, pc.private, phone.public) == req       # untouched by the relay
        await pc.send(P.routed("ice", pc.id, phone.id, session_id="s1", candidate={"candidate": "x"}))
        assert (await phone.recv_type("ice"))["candidate"] == {"candidate": "x"}
        # the PC goes away: the phone is told
        await pc.ws.close()
        presence = await phone.recv_type("presence")
        assert presence["hosts"][0]["online"] is False
    run(scenario)


def test_login_without_the_key_is_refused():
    async def scenario(client, relay):
        thief, real = Device("client"), Device("client")
        thief.public, thief.id = real.public, real.id        # claims someone else's key
        reply = await thief.login(client)
        assert reply == {"type": "error", "reason": "login_failed"}
        assert real.id not in relay.conns
    run(scenario)


def test_unauthorised_device_gets_no_presence_and_no_route():
    async def scenario(client, relay):
        pc, stranger = Device("host"), Device("client")
        await pc.login(client)
        await pc.send({"type": "devices", "keys": []})
        await stranger.login(client, accounts=[pc.account])
        assert (await stranger.recv_type("presence"))["hosts"] == []
        req = P.sign(P.routed("session.request", stranger.id, pc.id, session_id="s", client="x",
                              want={}), stranger.private, pc.public)
        await stranger.send(req)
        assert await stranger.recv_type("error") == {"type": "error", "reason": "not_allowed"}
        # and it can't pretend to be someone else on the wire
        await stranger.send(dict(req, **{"from": pc.id}))
        assert (await stranger.recv_type("error"))["reason"] == "wrong_sender"
    run(scenario)


def test_revoked_device_loses_its_route_immediately():
    async def scenario(client, relay):
        pc, tablet = Device("host"), Device("client")
        await pc.login(client)
        await pc.send({"type": "devices", "keys": [tablet.entry()]})
        await tablet.login(client, accounts=[pc.account])
        assert len((await tablet.recv_type("presence"))["hosts"]) == 1
        await pc.send({"type": "devices", "keys": []})          # removed in SAINT -> Devices
        assert (await tablet.recv_type("presence"))["hosts"] == []
        assert (await tablet.recv_type("error"))["reason"] == "revoked"
        await tablet.send(P.routed("ice", tablet.id, pc.id, session_id="s", candidate={}))
        assert (await tablet.recv_type("error"))["reason"] == "not_allowed"
    run(scenario)


def test_pairing_through_the_relay():
    async def scenario(client, relay):
        pc, browser, other = Device("host"), Device("client", "Chromebook"), Device("client")
        await pc.login(client)
        await pc.send({"type": "devices", "keys": []})
        await browser.login(client, accounts=[pc.account], pair=pc.account)
        psk = psk_for(bytes.fromhex("0102030405"))
        req = P.pair_mac(P.routed("pair.request", browser.id, pc.id, pubkey=P.b64(browser.public),
                                  name="Chromebook", platform="web"), psk)
        await browser.send(req)
        assert P.check_pair_mac(await pc.recv_type("pair.request"), psk)
        # a client that didn't announce pairing with this PC can't send it pair requests
        await other.login(client)
        await other.send(P.pair_mac(P.routed("pair.request", other.id, pc.id, pubkey=P.b64(other.public),
                                             name="x", platform="web"), psk))
        assert (await other.recv_type("error"))["reason"] == "not_allowed"
        # the PC accepts and adds the browser: the browser now sees it
        await pc.send(P.pair_mac(P.routed("pair.accept", pc.id, browser.id, pubkey=P.b64(pc.public),
                                          name="Halo PC"), psk))
        assert (await browser.recv_type("pair.accept"))["pubkey"] == P.b64(pc.public)
        await pc.send({"type": "devices", "keys": [browser.entry()]})
        assert (await browser.recv_type("presence"))["hosts"][0]["device_id"] == pc.id
        # the PC can't push pair messages at a device that never asked
        await pc.send(P.routed("pair.deny", pc.id, other.id, reason="x"))
        assert (await pc.recv_type("error"))["reason"] == "not_allowed"
    run(scenario)


def test_pair_requests_are_rate_limited():
    async def scenario(client, relay):
        pc, browser = Device("host"), Device("client")
        await pc.login(client)
        await browser.login(client, pair=pc.account)
        msg = P.routed("pair.request", browser.id, pc.id, pubkey=P.b64(browser.public), name="b",
                       platform="web", mac="AAAA")
        for _ in range(5):
            await browser.send(msg)
        await browser.send(msg)
        assert (await browser.recv_type("error"))["reason"] == "rate_limited"
    run(scenario)


def test_clients_cannot_talk_to_each_other_or_set_devices():
    async def scenario(client, relay):
        a, b = Device("client"), Device("client")
        await a.login(client)
        await b.login(client)
        await a.send(P.routed("ice", a.id, b.id, session_id="s", candidate={}))
        assert (await a.recv_type("error"))["reason"] == "not_allowed"
        await a.send({"type": "devices", "keys": []})
        assert (await a.recv_type("error"))["reason"] == "not_allowed"
    run(scenario)


def test_turn_credentials_follow_coturn_rest_scheme():
    cfg = Config(stun_urls=["stun:s:3478"], turn_urls=["turn:s:3478"], turn_secret="k")
    servers = cfg.ice_servers("abcd", now=1000)
    user = servers[1]["username"]
    assert user == "%d:abcd" % (1000 + 12 * 3600)
    assert servers[1]["credential"] == base64.b64encode(hmac.new(b"k", user.encode(), hashlib.sha1).digest()).decode()
    assert Config().ice_servers("x") == []
