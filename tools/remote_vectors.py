"""Write tests/data/remote_vectors.json: known answers for Halo Remote protocol v1.

The Python (tests/test_remote_protocol.py), JavaScript (web/remote/test) and Swift (SaintCore tests) implementations
all check themselves against this file, so the three can't drift apart.

    python tools/remote_vectors.py          # rewrite the file (only when the protocol changes on purpose)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.link import crypto  # noqa: E402
from modules.link.identity import encode_code, psk_for  # noqa: E402
from modules.remote_desktop import protocol as P  # noqa: E402

OUT = os.path.join(ROOT, "tests", "data", "remote_vectors.json")

HOST = bytes.fromhex("a8abababababababababababababababababababababababababababababab6b")
CLIENT = bytes.fromhex("5871c6f9a0d1d2f1e8c4a3b2c1d0e0f1a2b3c4d5e6f708192a3b4c5d6e7f8051")
STRANGER = bytes.fromhex("e0" + "17" * 30 + "4f")
RELAY_EPH = bytes.fromhex("c8" + "42" * 30 + "41")
TS = 1793600000                                     # early November 2026, fixed so the signatures are stable


def key(private):
    pub = crypto.public_key(private)
    return {"private": private.hex(), "public": P.b64(pub), "device_id": P.device_id_for(pub)}


def build():
    host, client, stranger = key(HOST), key(CLIENT), key(STRANGER)
    hp, cp = crypto.public_key(HOST), crypto.public_key(CLIENT)

    canon = []
    for obj in [
        {"b": 1, "a": [True, False, None], "c": {"z": "", "y": -7}},
        {"name": "Sebastian’s iPad", "emoji": "\U0001F5A5 ok", "esc": "q\"b\\s/\n\t\r\b\f\x01\x1f\x7f", "u": " "},
        {"type": "x", "nested": [{"k": [1, [2, [3]]]}], "big": 9007199254740991, "neg": -1},
    ]:
        canon.append({"object": obj, "canonical": P.canonical(obj).decode("utf-8")})

    request = P.sign({"type": "session.request", "from": client["device_id"], "to": host["device_id"],
                      "session_id": "s-0001", "ts": TS, "client": "Sebastian’s iPad",
                      "want": {"max_w": 1920, "max_h": 1080, "fps": 30, "audio": False}}, CLIENT, hp)
    offer = P.sign({"type": "sdp.offer", "from": host["device_id"], "to": client["device_id"],
                    "session_id": "s-0001", "ts": TS + 1,
                    "sdp": "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\na=fingerprint:sha-256 AB:CD\r\n"}, HOST, cp)
    accept = P.sign({"type": "session.accept", "from": host["device_id"], "to": client["device_id"],
                     "session_id": "s-0001", "ts": TS,
                     "ice_servers": [{"urls": ["stun:relay.example:3478"]}]}, HOST, cp)
    tampered = dict(request, want=dict(request["want"], fps=60))
    forged = P.sign({k: v for k, v in request.items() if k not in ("sig", "from")}, STRANGER, hp)

    challenge = {"type": "relay.challenge", "nonce": P.b64(bytes(range(32))),
                 "eph": P.b64(crypto.public_key(RELAY_EPH))}
    proof = P.relay_proof(CLIENT, challenge, "client")

    token = bytes.fromhex("0123456789")
    psk = psk_for(token)
    pair_req = P.pair_mac({"type": "pair.request", "from": client["device_id"], "to": host["device_id"],
                           "pubkey": client["public"], "name": "Chromebook", "platform": "web", "ts": TS}, psk)

    return {
        "about": "Halo Remote protocol v1 known answers (docs/REMOTE_DESKTOP.md). Written by tools/remote_vectors.py.",
        "ts": TS,
        "keys": {"host": host, "client": client, "stranger": stranger},
        "account_id": P.account_id_for(hp),
        "session_key": P.session_key(CLIENT, hp).hex(),
        "canonical": canon,
        "signed": {"session.request": request, "session.accept": accept, "sdp.offer": offer},
        "rejected": [
            {"why": "bad_signature", "message": tampered, "verifier": "host", "peer": "client"},
            {"why": "wrong_sender", "message": forged, "verifier": "host", "peer": "client"},
            {"why": "bad_signature", "message": dict(forged, **{"from": client["device_id"]}), "verifier": "host",
             "peer": "client"},
        ],
        "relay_login": {"eph_private": RELAY_EPH.hex(), "challenge": challenge, "role": "client",
                        "device_id": client["device_id"], "proof": proof},
        "pairing": {"token": token.hex(), "code": encode_code(token), "psk": psk.hex(), "request": pair_req},
    }


if __name__ == "__main__":
    data = build()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("wrote", os.path.relpath(OUT, ROOT))
