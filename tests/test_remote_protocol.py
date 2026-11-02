"""Halo Remote protocol v1 (modules/remote_desktop/protocol.py) against tests/data/remote_vectors.json.

The same file is checked by the browser client (web/remote/test) and the iPhone app (SaintCore tests).
"""
import json
import os
import sys

import pytest

from modules.link import crypto
from modules.link.identity import OWN, COLLABORATOR, Peer, PeerStore, encode_code, psk_for
from modules.remote_desktop import protocol as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V = json.load(open(os.path.join(ROOT, "tests", "data", "remote_vectors.json"), encoding="utf-8"))
K = {name: bytes.fromhex(k["private"]) for name, k in V["keys"].items()}
PUB = {name: crypto.public_key(k) for name, k in K.items()}
TS = V["ts"]


def test_vectors_are_what_the_generator_writes():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import remote_vectors
    assert json.loads(json.dumps(remote_vectors.build(), ensure_ascii=False)) == V


def test_keys_ids_and_account():
    for name, k in V["keys"].items():
        assert P.b64(PUB[name]) == k["public"]
        assert P.device_id_for(PUB[name]) == k["device_id"]
    assert P.account_id_for(PUB["host"]) == V["account_id"]
    assert P.session_key(K["client"], PUB["host"]).hex() == V["session_key"]
    assert P.session_key(K["host"], PUB["client"]).hex() == V["session_key"]


@pytest.mark.parametrize("case", V["canonical"])
def test_canonical_json(case):
    assert P.canonical(case["object"]).decode("utf-8") == case["canonical"]


def test_canonical_refuses_what_other_languages_encode_differently():
    for bad in [{"x": 1.5}, {"é": 1}, {"s": "\ud800"}, {"x": float("nan")}]:
        with pytest.raises(P.ProtocolError):
            P.canonical(bad)


def test_known_signatures_verify_both_ways():
    req = V["signed"]["session.request"]
    assert P.verify(req, K["host"], PUB["client"], now=TS)
    assert P.sign({k: v for k, v in req.items() if k != "sig"}, K["client"], PUB["host"]) == req
    for kind in ("session.accept", "sdp.offer"):
        assert P.verify(V["signed"][kind], K["client"], PUB["host"], now=TS)


@pytest.mark.parametrize("case", V["rejected"])
def test_rejected_vectors(case):
    with pytest.raises(P.ProtocolError) as e:
        P.verify(case["message"], K[case["verifier"]], PUB[case["peer"]], now=TS)
    assert e.value.reason == case["why"]


def test_reflected_message_is_refused():
    req = V["signed"]["session.request"]
    # the client's own request bounced back to it, as if the host had sent it
    with pytest.raises(P.ProtocolError) as e:
        P.verify(req, K["client"], PUB["host"], now=TS)
    assert e.value.reason == "wrong_sender"


def test_stale_and_replayed_messages_are_refused():
    req = V["signed"]["session.request"]
    with pytest.raises(P.ProtocolError) as e:
        P.verify(req, K["host"], PUB["client"], now=TS + P.MAX_SKEW + 1)
    assert e.value.reason == "stale"
    guard = P.ReplayGuard()
    guard.check(req, now=TS)
    with pytest.raises(P.ProtocolError):
        guard.check(req, now=TS + 5)


def test_relay_login_vector_and_wrong_key():
    rl = V["relay_login"]
    assert P.relay_proof(K["client"], rl["challenge"], rl["role"]) == rl["proof"]
    hello = {"type": "hello", "v": 1, "device_id": rl["device_id"], "role": "client",
             "pubkey": V["keys"]["client"]["public"], "name": "x"}
    eph = bytes.fromhex(rl["eph_private"])
    assert P.check_relay_proof(eph, rl["challenge"], hello, rl["proof"])
    # someone claiming the client's key without holding it
    assert not P.check_relay_proof(eph, rl["challenge"], hello, P.relay_proof(K["stranger"], rl["challenge"], "client"))
    # a proof for another role doesn't count
    assert not P.check_relay_proof(eph, rl["challenge"], dict(hello, role="host"), rl["proof"])


def test_pairing_vector():
    pv = V["pairing"]
    token = bytes.fromhex(pv["token"])
    assert encode_code(token) == pv["code"]
    assert psk_for(token).hex() == pv["psk"]
    assert P.check_pair_mac(pv["request"], psk_for(token), now=TS)
    with pytest.raises(P.ProtocolError) as e:
        P.check_pair_mac(pv["request"], psk_for(bytes.fromhex("0123456788")), now=TS)
    assert e.value.reason == "bad_code"


def test_validate_rejects_malformed_messages():
    good = {"type": "hello", "v": 1, "device_id": V["keys"]["host"]["device_id"], "role": "host",
            "pubkey": V["keys"]["host"]["public"], "name": "PC"}
    assert P.validate(good)
    for bad, reason in [
        ({**good, "v": 2}, "unsupported_version"),
        ({**good, "role": "admin"}, "bad:role"),
        ({**good, "device_id": V["keys"]["client"]["device_id"]}, "device_id_mismatch"),
        ({**good, "pubkey": "AAAA"}, "bad:pubkey"),
        ({k: v for k, v in good.items() if k != "name"}, "missing:name"),
        ({"type": "nope"}, "unknown_type"),
        ([1], "not_an_object"),
    ]:
        with pytest.raises(P.ProtocolError) as e:
            P.validate(bad)
        assert e.value.reason == reason
    with pytest.raises(P.ProtocolError):
        P.parse(b"x" * (P.MAX_MESSAGE + 1))
    with pytest.raises(P.ProtocolError):
        P.validate({"type": "pointer.button", "button": True, "down": True}, P.CONTROL_SCHEMAS)
    with pytest.raises(P.ProtocolError):
        P.validate({"type": "key", "code": 9999, "down": True}, P.CONTROL_SCHEMAS)


# ---------------------------------------------------------------------- #
# Authorization against the real PeerStore
# ---------------------------------------------------------------------- #
def _store(tmp_path, **perms):
    store = PeerStore(str(tmp_path / "peers.json"))
    cid = P.device_id_for(PUB["client"])
    store.save(Peer(id=cid, name="iPad", public_key=PUB["client"].hex(), role=OWN, perms=perms))
    return store, cid


def test_own_device_is_asked_by_default_and_always_allow_works(tmp_path):
    store, cid = _store(tmp_path)
    d = P.authorize(store, PUB["client"])
    assert (d.outcome, d.reason) == ("ask", "ok")
    store.update(cid, perms={"remote_desktop": "allow"})
    assert P.authorize(store, PUB["client"]).outcome == "allow"
    assert P.allowed_devices(store) == [{"device_id": cid, "pubkey": P.b64(PUB["client"]), "name": "iPad"}]


def test_unpaired_key_is_refused(tmp_path):
    store, _ = _store(tmp_path)
    d = P.authorize(store, PUB["stranger"])
    assert (d.outcome, d.reason) == ("deny", "not_paired")


def test_denied_and_collaborator_keys_are_refused(tmp_path):
    store, cid = _store(tmp_path, remote_desktop="deny")
    assert P.authorize(store, PUB["client"]).reason == "not_allowed"
    assert P.allowed_devices(store) == []
    store.update(cid, perms={}, role=COLLABORATOR)
    store.update(cid, perms={"remote_desktop": "deny"})
    friend = PeerStore(str(tmp_path / "friend.json"))
    friend.save(Peer(id=cid, name="Gian", public_key=PUB["client"].hex(), role=COLLABORATOR))
    assert P.authorize(friend, PUB["client"]).reason == "not_allowed"


def test_revoked_key_is_refused_until_paired_again(tmp_path):
    store, cid = _store(tmp_path, remote_desktop="allow")
    assert store.remove(cid)
    d = P.authorize(store, PUB["client"])
    assert (d.outcome, d.reason) == ("deny", "revoked")
    assert P.allowed_devices(store) == []
    store.save(Peer(id=cid, name="iPad", public_key=PUB["client"].hex(), role=OWN))
    assert P.authorize(store, PUB["client"]).outcome == "ask"
