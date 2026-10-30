"""core/state_socket.py: the reactive wallpaper's feed. A real WebSocket handshake over TCP, then pushed
text frames; non-WebSocket requests are refused; localhost only."""

import base64
import json
import os
import socket
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from core import state_socket as ss


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QCoreApplication.instance() or QApplication([])


def test_accept_key_matches_rfc6455_example():
    assert ss.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_frames():
    assert ss.text_frame("hi") == b"\x81\x02hi"
    assert ss.text_frame("x" * 200)[:4] == b"\x81\x7e\x00\xc8"


def _pump(cond, ms=5000):
    loop = QEventLoop()
    t = QTimer()
    t.timeout.connect(lambda: cond() and loop.quit())
    t.start(10)
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_handshake_then_state_is_pushed(app):
    srv = ss.StateSocket(port=47899)
    assert srv.listening
    got = {}

    def client():
        s = socket.create_connection(("127.0.0.1", 47899), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            data += s.recv(1024)
        head, rest = data.split(b"\r\n\r\n", 1)
        got["head"] = head.decode()
        got["key"] = key
        while len(rest) < 2 or len(rest) < 2 + rest[1]:
            rest += s.recv(1024)
        got["msg"] = json.loads(rest[2:2 + rest[1]])
        s.close()

    srv.publish("listening", 0.42)                  # before the client: it gets the latest state on connect
    t = threading.Thread(target=client)
    t.start()
    _pump(lambda: not t.is_alive())
    t.join(1)
    srv.close()
    assert got["head"].startswith("HTTP/1.1 101")
    assert f"Sec-WebSocket-Accept: {ss.accept_key(got['key'])}" in got["head"]
    assert got["msg"] == {"state": "listening", "level": 0.42}


def test_plain_http_is_refused(app):
    srv = ss.StateSocket(port=47898)
    got = {}

    def client():
        s = socket.create_connection(("127.0.0.1", 47898), timeout=5)
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        got["reply"] = s.recv(1024)
        s.close()
    t = threading.Thread(target=client)
    t.start()
    _pump(lambda: not t.is_alive())
    t.join(1)
    srv.close()
    assert got["reply"].startswith(b"HTTP/1.1 400")
