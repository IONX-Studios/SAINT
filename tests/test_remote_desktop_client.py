"""Halo Remote in SAINT's desktop app (ui/remote_desktop.py): the parts that don't need a window."""
import urllib.error
import urllib.request

import pytest

from modules.link import crypto
from modules.link.identity import OWN, Peer
from modules.remote_desktop import protocol as P
from ui import remote_desktop as rd


def _peer():
    _, pub = crypto.generate_keypair()
    return Peer(id=P.device_id_for(pub), name="Halo PC", public_key=pub.hex(), role=OWN, platform="linux"), pub


class FakeLink:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, 0

    def remote_info(self, peer):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer


def test_host_record_is_built_from_the_paired_key():
    peer, pub = _peer()
    rec = rd.host_record(peer, {"account": P.account_id_for(pub)})
    assert rec == {"device_id": peer.id, "pubkey": P.b64(pub), "name": "Halo PC", "account": P.account_id_for(pub)}
    with pytest.raises(ValueError):
        rd.host_record(peer, {"account": "0" * 64})          # a PC claiming another account


def test_relay_comes_from_the_pc_and_is_remembered():
    peer, pub = _peer()
    cache = {}
    info = {"relay_url": "wss://r.example/ws", "account": P.account_id_for(pub), "device_id": peer.id}
    assert rd.resolve_info(FakeLink(info), peer, cache)["relay_url"] == "wss://r.example/ws"
    assert cache[peer.id]["relay_url"] == "wss://r.example/ws"
    # PC offline now: what it said last time still works
    assert rd.resolve_info(FakeLink(error=OSError("offline")), peer, cache)["relay_url"] == "wss://r.example/ws"


def test_pc_without_halo_remote_says_how_to_set_it_up():
    peer, _ = _peer()
    with pytest.raises(LookupError, match="halo-remote"):
        rd.resolve_info(FakeLink({"relay_url": ""}), peer, {})
    with pytest.raises(LookupError):
        rd.resolve_info(FakeLink(error=OSError("offline")), peer, {})


def test_static_server_serves_modules_on_loopback_only(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "a.js").write_text("export const x = 1;")
    (tmp_path / "desktop.html").write_text("<p>hi</p>")
    srv = rd.StaticServer(str(tmp_path))
    try:
        assert srv.httpd.server_address[0] == "127.0.0.1"
        with urllib.request.urlopen(srv.url("js/a.js")) as r:
            assert r.headers["Content-Type"].startswith("text/javascript")
            assert r.headers["Cache-Control"] == "no-store"
        with urllib.request.urlopen(srv.url()) as r:
            assert r.read() == b"<p>hi</p>"
        for bad in ("js/", "../" * 6 + "Windows/win.ini", "%2e%2e/%2e%2e/secret"):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(srv.url(bad))
    finally:
        srv.httpd.shutdown()


def test_the_shipped_page_and_modules_exist():
    for f in ("desktop.html", "style.css", "js/desktop.js", "js/viewer.js", "js/client.js", "js/protocol.js",
              "js/input.js"):
        assert (rd.WEB_ROOT / f).is_file(), f
