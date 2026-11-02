"""Halo Remote host logic (modules/remote_desktop/host.py): who gets in, the session state machine, input
mapping to portal calls, file receiving, pairing, the GStreamer/ICE configuration.

The media glue (modules/remote_desktop/media.py: portal, PipeWire, webrtcbin) needs a Linux desktop and is
not exercised here.
"""
import hashlib
import json
import os

import pytest

from modules.link import crypto
from modules.link.identity import OWN, Peer, PeerStore, encode_code, psk_for
from modules.remote_desktop import host as H
from modules.remote_desktop import protocol as P


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def world(tmp_path):
    host_priv, host_pub = crypto.generate_keypair()
    ipad_priv, ipad_pub = crypto.generate_keypair()
    peers = PeerStore(str(tmp_path / "peers.json"))
    ipad_id = P.device_id_for(ipad_pub)
    peers.save(Peer(id=ipad_id, name="iPad", public_key=ipad_pub.hex(), role=OWN))
    clock = Clock()
    ctl = H.SessionController(host_priv, peers, lambda: [{"urls": ["stun:x:3478"]}], clock=clock)
    return {"ctl": ctl, "peers": peers, "clock": clock, "host_pub": host_pub, "ipad": (ipad_priv, ipad_pub, ipad_id),
            "host_id": P.device_id_for(host_pub)}


def request(world, sid="s1", key=None):
    priv, pub, did = key or world["ipad"]
    msg = P.routed("session.request", P.device_id_for(pub), world["host_id"], session_id=sid, client="iPad",
                   want={"max_w": 1280, "max_h": 720, "fps": 30, "audio": False})
    msg["ts"] = int(world["clock"]())
    return P.sign(msg, priv, world["host_pub"])


def sent(actions, kind):
    return [a.message for a in actions if a.kind == "send" and a.message["type"] == kind]


def opened(world, msg):
    priv, _, _ = world["ipad"]
    return P.verify(msg, priv, world["host_pub"], now=world["clock"]())


def test_own_device_is_asked_then_accepted_and_offer_is_signed(world):
    ctl = world["ctl"]
    acts = ctl.on_message(request(world))
    assert [a.kind for a in acts] == ["ask"] and ctl.state == H.ASKING
    acts = ctl.on_user_answer("s1", True)
    accept = opened(world, sent(acts, "session.accept")[0])
    assert accept["ice_servers"] == [{"urls": ["stun:x:3478"]}]
    assert any(a.kind == "start_media" for a in acts) and ctl.state == H.NEGOTIATING
    offer = opened(world, sent(ctl.local_sdp("v=0\r\n"), "sdp.offer")[0])
    assert offer["sdp"] == "v=0\r\n"
    ctl.on_connected()
    assert ctl.state == H.CONNECTED


def test_declined_and_unanswered_prompts_deny(world):
    ctl = world["ctl"]
    ctl.on_message(request(world, "a"))
    deny = opened(world, sent(ctl.on_user_answer("a", False), "session.deny")[0])
    assert deny["reason"] == "declined" and ctl.state == H.IDLE
    ctl.on_message(request(world, "b"))
    world["clock"].t += H.ASK_TIMEOUT + 1
    assert opened(world, sent(ctl.tick(), "session.deny")[0])["reason"] == "no_answer"


def test_always_allow_skips_the_prompt(world):
    world["peers"].update(world["ipad"][2], perms={"remote_desktop": "allow"})
    acts = world["ctl"].on_message(request(world))
    assert sent(acts, "session.accept") and world["ctl"].state == H.NEGOTIATING


def test_unpaired_device_is_ignored_and_bad_signature_dropped(world):
    ctl = world["ctl"]
    stranger = crypto.generate_keypair()
    assert ctl.on_message(request(world, key=(stranger[0], stranger[1], None))) == []
    forged = dict(request(world), want={"max_w": 9999})
    assert ctl.on_message(forged) == [] and ctl.state == H.IDLE


def test_denied_permission_gets_a_signed_refusal(world):
    world["peers"].update(world["ipad"][2], perms={"remote_desktop": "deny"})
    # a denied device isn't in the relay list either, so the controller doesn't know its key at all
    assert world["ctl"].on_message(request(world)) == []
    # if it is known (list cached before the change), authorize() still refuses with a reason
    world["ctl"].keys[world["ipad"][2]] = world["ipad"][1]
    deny = sent(world["ctl"].on_message(request(world, "x")), "session.deny")[0]
    assert opened(world, deny)["reason"] == "not_allowed"


def test_replayed_request_is_dropped(world):
    ctl = world["ctl"]
    req = request(world)
    ctl.on_message(req)
    ctl.on_user_answer("s1", False)
    assert ctl.on_message(req) == []


def test_second_device_gets_busy_same_device_replaces(world, tmp_path):
    ctl = world["ctl"]
    phone_priv, phone_pub = crypto.generate_keypair()
    world["peers"].save(Peer(id=P.device_id_for(phone_pub), name="Phone", public_key=phone_pub.hex(), role=OWN,
                             perms={"remote_desktop": "allow"}))
    world["peers"].update(world["ipad"][2], perms={"remote_desktop": "allow"})
    ctl.on_message(request(world, "one"))
    busy = sent(ctl.on_message(request(world, "two", key=(phone_priv, phone_pub, None))), "session.deny")[0]
    assert P.verify(busy, phone_priv, world["host_pub"], now=world["clock"]())["reason"] == "busy"
    acts = ctl.on_message(request(world, "three"))
    assert opened(world, sent(acts, "session.end")[0])["reason"] == "replaced"
    assert ctl.session.session_id == "three"


def test_revoking_a_connected_device_ends_its_session(world):
    ctl = world["ctl"]
    world["peers"].update(world["ipad"][2], perms={"remote_desktop": "allow"})
    ctl.on_message(request(world))
    ctl.on_connected()
    world["peers"].remove(world["ipad"][2])
    acts = ctl.tick()
    assert [a.kind for a in acts] == ["send", "stop_media"]
    assert opened(world, acts[0].message)["reason"] == "revoked" and ctl.state == H.IDLE


def test_answer_and_ice_only_for_the_current_session(world):
    ctl = world["ctl"]
    world["peers"].update(world["ipad"][2], perms={"remote_desktop": "allow"})
    ctl.on_message(request(world))
    priv, _, did = world["ipad"]
    answer = P.routed("sdp.answer", did, world["host_id"], session_id="s1", sdp="v=0")
    answer["ts"] = int(world["clock"]())
    assert [a.kind for a in ctl.on_message(P.sign(answer, priv, world["host_pub"]))] == ["remote_sdp"]
    other = P.sign(dict(answer, session_id="zzz"), priv, world["host_pub"])
    assert ctl.on_message(other) == []
    ice = P.routed("ice", did, world["host_id"], session_id="s1", candidate={"candidate": "c"})
    assert [a.kind for a in ctl.on_message(ice)] == ["remote_ice"]
    end = P.routed("session.end", did, world["host_id"], session_id="s1", reason="bye")
    end["ts"] = int(world["clock"]())
    assert [a.kind for a in ctl.on_message(P.sign(end, priv, world["host_pub"]))] == ["stop_media"]


def test_negotiation_and_idle_timeouts(world):
    ctl = world["ctl"]
    world["peers"].update(world["ipad"][2], perms={"remote_desktop": "allow"})
    ctl.on_message(request(world, "n"))
    world["clock"].t += H.NEGOTIATE_TIMEOUT + 1
    assert opened(world, sent(ctl.tick(), "session.end")[0])["reason"] == "connect_timeout"
    ctl.on_message(request(world, "m"))
    ctl.on_connected()
    world["clock"].t += H.IDLE_TIMEOUT - 10
    assert ctl.tick() == []
    ctl.on_input()
    world["clock"].t += H.IDLE_TIMEOUT + 1
    assert opened(world, sent(ctl.tick(), "session.end")[0])["reason"] == "idle"


# ---------------------------------------------------------------------- #
# Input
# ---------------------------------------------------------------------- #
def calls(mapper, *msgs):
    out = []
    for m in msgs:
        out += [(c.method, c.args) for c in mapper.map(P.validate(m, P.CONTROL_SCHEMAS))]
    return out


def test_pointer_mapping_clamps_to_the_stream():
    m = H.InputMapper(42, 1920, 1080)
    assert calls(m, {"type": "pointer.move", "x": 0.5, "y": 1.5}) == \
        [("NotifyPointerMotionAbsolute", (42, 959.5, 1079.0))]
    assert calls(m, {"type": "pointer.move", "x": -3, "y": 0}) == [("NotifyPointerMotionAbsolute", (42, 0.0, 0.0))]
    assert calls(m, {"type": "pointer.button", "button": 3, "down": True},
                 {"type": "pointer.button", "button": 3, "down": False}) == \
        [("NotifyPointerButton", (0x111, 1)), ("NotifyPointerButton", (0x111, 0))]
    # a release for a button that was never pressed isn't sent
    assert calls(m, {"type": "pointer.button", "button": 1, "down": False}) == []
    assert calls(m, {"type": "pointer.scroll", "dx": 0, "dy": 1e9}) == [("NotifyPointerAxis", (0.0, 2000.0))]
    assert calls(m, {"type": "pointer.scroll", "dx": 0, "dy": 0}) == []


def test_keys_text_and_release_on_disconnect():
    m = H.InputMapper(1, 100, 100)
    assert calls(m, {"type": "key", "code": 29, "down": True}, {"type": "key", "code": 46, "down": True}) == \
        [("NotifyKeyboardKeycode", (29, 1)), ("NotifyKeyboardKeycode", (46, 1))]
    calls(m, {"type": "pointer.button", "button": 1, "down": True})
    released = [(c.method, c.args) for c in m.release_all()]
    assert released == [("NotifyKeyboardKeycode", (29, 0)), ("NotifyKeyboardKeycode", (46, 0)),
                        ("NotifyPointerButton", (0x110, 0))]
    assert m.release_all() == []
    assert calls(m, {"type": "text", "utf8": "aé€\n"}) == [
        ("NotifyKeyboardKeysym", (0x61, 1)), ("NotifyKeyboardKeysym", (0x61, 0)),
        ("NotifyKeyboardKeysym", (0xE9, 1)), ("NotifyKeyboardKeysym", (0xE9, 0)),
        ("NotifyKeyboardKeysym", (0x10020AC, 1)), ("NotifyKeyboardKeysym", (0x10020AC, 0)),
        ("NotifyKeyboardKeysym", (0xFF0D, 1)), ("NotifyKeyboardKeysym", (0xFF0D, 0))]


# ---------------------------------------------------------------------- #
# Files
# ---------------------------------------------------------------------- #
def test_file_receive_checks_size_and_hash(tmp_path):
    rx = H.FileReceiver(str(tmp_path / "in"))
    data = os.urandom(150_000)
    offer = {"type": "file.offer", "id": "f1", "name": "../../etc/passwd", "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
    assert rx.offer(offer) == {"type": "file.accept", "id": "f1"}
    assert rx.chunk(data[:65536]) is None
    assert rx.chunk(data[65536:]) == {"type": "file.done", "id": "f1", "ok": True}
    dest = tmp_path / "in" / "passwd"
    assert dest.read_bytes() == data
    # same name again: kept side by side; wrong hash: discarded
    rx.offer(dict(offer, id="f2", sha256="0" * 64))
    assert rx.chunk(data) == {"type": "file.done", "id": "f2", "ok": False}
    assert sorted(os.listdir(tmp_path / "in")) == ["passwd"]
    rx.offer(dict(offer, id="f3"))
    rx.chunk(data)
    assert (tmp_path / "in" / "passwd (2)").exists()
    # more bytes than offered
    rx.offer(dict(offer, id="f4", size=10))
    assert rx.chunk(b"x" * 11)["ok"] is False


def test_safe_filename():
    assert H.safe_filename("..\\..\\boot.ini") == "boot.ini"
    assert H.safe_filename("a:b*c?.txt") == "a_b_c_.txt"
    assert H.safe_filename("...") == "file"


# ---------------------------------------------------------------------- #
# Pairing, config, keys
# ---------------------------------------------------------------------- #
def test_pairing_through_relay_with_one_time_code(tmp_path):
    host_priv, host_pub = crypto.generate_keypair()
    _, web_pub = crypto.generate_keypair()
    pairing = H.RelayPairing(str(tmp_path / "pair.json"))
    code = pairing.create()
    token = json.load(open(tmp_path / "pair.json"))["token"]
    assert encode_code(bytes.fromhex(token)) == code
    req = P.routed("pair.request", P.device_id_for(web_pub), P.device_id_for(host_pub), pubkey=P.b64(web_pub),
                   name="Chromebook  ", platform="web")
    wrong = P.pair_mac(req, psk_for(b"\x00" * 5))
    assert not pairing.check(wrong)
    assert pairing.check(P.pair_mac(req, psk_for(bytes.fromhex(token))))
    assert not pairing.check(P.pair_mac(req, psk_for(bytes.fromhex(token))))   # used up
    peers = PeerStore(str(tmp_path / "peers.json"))
    peer = H.accept_pairing(peers, req)
    assert peer.name == "Chromebook" and peer.permission("remote_desktop") == "ask"
    assert P.authorize(peers, web_pub).outcome == "ask"


def test_pairing_code_dies_after_failures_and_expiry(tmp_path):
    pairing = H.RelayPairing(str(tmp_path / "pair.json"))
    pairing.create(now=1000)
    token = bytes.fromhex(json.load(open(tmp_path / "pair.json"))["token"])
    _, pub = crypto.generate_keypair()
    req = P.routed("pair.request", P.device_id_for(pub), "0" * 16, pubkey=P.b64(pub), name="x", platform="web")
    req["ts"] = 1000
    good = P.pair_mac(req, psk_for(token))
    assert not pairing.check(good, now=1000 + H.RelayPairing.TTL + 1)
    for _ in range(H.RelayPairing.MAX_FAILURES):
        pairing.check(P.pair_mac(req, psk_for(b"\x01" * 5)), now=1001)
    assert not pairing.check(good, now=1001)


def test_pairing_link_keeps_the_code_out_of_the_request():
    link = H.pairing_link("wss://relay.example.com/ws", "ABCD-EFGH", "a" * 16, "b" * 64)
    assert link.startswith("https://relay.example.com/#pair=ABCD-EFGH&host=")


def test_relay_url_check():
    assert H.check_relay_url(" wss://r.example/ws ") == "wss://r.example/ws"
    assert H.check_relay_url("ws://localhost:8080/ws")
    for bad in ["ws://r.example/ws", "https://r.example", "nonsense"]:
        with pytest.raises(ValueError):
            H.check_relay_url(bad)


def test_device_key_is_read_never_created(tmp_path):
    path = tmp_path / "identity.json"
    with pytest.raises(H.KeyUnavailable):
        H.load_device_key(str(path))
    assert not path.exists()
    priv, pub = crypto.generate_keypair()
    path.write_text(json.dumps({"public_key": pub.hex(), "private_key": priv.hex(), "name": "Halo"}))
    assert H.load_device_key(str(path)) == (priv, pub, "Halo")
    other, _ = crypto.generate_keypair()
    path.write_text(json.dumps({"public_key": pub.hex(), "private_key": other.hex()}))
    with pytest.raises(H.KeyUnavailable):
        H.load_device_key(str(path))


def test_config_round_trip(tmp_path):
    cfg = H.HostConfig.load(str(tmp_path / "remote.json"))
    assert cfg.relay_url == ""
    cfg.relay_url = "wss://r/ws"
    cfg.save()
    assert H.HostConfig.load(str(tmp_path / "remote.json")).relay_url == "wss://r/ws"


# ---------------------------------------------------------------------- #
# GStreamer / ICE configuration
# ---------------------------------------------------------------------- #
def test_encoder_choice_and_pipeline():
    assert H.pick_encoder(lambda n: n in ("x264enc", "vah264enc")) == "vah264enc"
    assert H.pick_encoder(lambda n: n == "x264enc") == "x264enc"
    with pytest.raises(RuntimeError):
        H.pick_encoder(lambda n: False)
    desc = H.pipeline_description(7, 55, "x264enc", {"max_w": 99999, "max_h": 720, "fps": 30})
    assert desc.startswith("pipewiresrc fd=7 path=55 ")
    assert "width=[16,3840],height=[16,720]" in desc and "framerate=30/1" in desc
    assert "profile=constrained-baseline" in desc and desc.endswith("latency=0")
    assert "x264enc bitrate=" in desc


def test_webrtcbin_ice_uris():
    stun, turns = H.webrtcbin_ice([
        {"urls": ["stun:relay.example:3478"]},
        {"urls": ["turn:relay.example:3478?transport=udp", "turns:relay.example:5349?transport=tcp", "bogus:x"],
         "username": "1793:abcd", "credential": "a+b/c="},
    ])
    assert stun == "stun://relay.example:3478"
    assert turns == ["turn://1793%3Aabcd:a%2Bb%2Fc%3D@relay.example:3478?transport=udp",
                     "turns://1793%3Aabcd:a%2Bb%2Fc%3D@relay.example:5349?transport=tcp"]


def test_unit_file_runs_the_host_from_the_project():
    unit = H.UNIT.format(root="/opt/saint")
    assert "ExecStart=/usr/bin/python3 -m modules.remote_desktop.host run" in unit
    assert "Environment=PYTHONPATH=/opt/saint" in unit and "WantedBy=graphical-session.target" in unit


def test_packaged_unit_matches_the_one_enable_writes():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "packaging", "linux", "systemd", "halo-remote.service.in")
    with open(path, encoding="utf-8") as f:
        assert f.read() == H.UNIT.format(root="@SAINT_ROOT@")


def test_codec_follows_what_the_client_decodes():
    have = lambda n: n in ("x264enc", "vp8enc")   # noqa: E731
    assert H.client_codecs({}) == ["H264"]                                   # older clients
    assert H.client_codecs({"codecs": ["vp8", "AV1"]}) == ["VP8"]
    assert H.pick_encoder(have, H.client_codecs({"codecs": ["VP8", "H264"]})) == "x264enc"
    assert H.pick_encoder(have, ["VP8"]) == "vp8enc"
    with pytest.raises(RuntimeError):
        H.pick_encoder(have, [])
    desc = H.pipeline_description(3, 9, "vp8enc", {"max_w": 1280, "max_h": 720, "fps": 30})
    assert "format=I420" in desc and "target-bitrate=2764000" in desc
    assert "rtpvp8pay pt=97" in desc and "encoding-name=VP8" in desc and "h264" not in desc


def test_link_tells_paired_apps_where_remote_desktop_is(tmp_path, monkeypatch):
    from modules.link.identity import Identity, Peer
    from modules.link.service import LinkService
    from modules.link.wire import LinkError
    monkeypatch.setattr(H, "_data_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    cfg = H.HostConfig.load()
    cfg.relay_url = "wss://relay.example.com/ws"
    cfg.save()
    svc = LinkService.__new__(LinkService)
    priv, pub = crypto.generate_keypair()
    ident = Identity(str(tmp_path / "identity.json"))
    ident._data, ident._private = {"public_key": pub.hex(), "name": "Halo"}, priv
    svc.identity = ident

    class Ctx:
        def __init__(self, **kw):
            self.peer = Peer(id="p", name="P", public_key="", **kw)
    out = svc._h_remote_info(Ctx(role=OWN), {})
    assert out == {"relay_url": "wss://relay.example.com/ws", "device_id": P.device_id_for(pub),
                   "account": P.account_id_for(pub)}
    with pytest.raises(LinkError):
        svc._h_remote_info(Ctx(role="collaborator"), {})
    with pytest.raises(LinkError):
        svc._h_remote_info(Ctx(role=OWN, perms={"remote_desktop": "deny"}), {})
