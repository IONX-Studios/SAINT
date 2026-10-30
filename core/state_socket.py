"""
core/state_socket.py

SAINT's live state for Plasma (Halo): the reactive wallpaper and the SAINT widget are QML, and QML has a
WebSocket client built in but can't follow D-Bus signals. So SAINT pushes {"state", "level"} to
ws://127.0.0.1:47811/ — localhost only, read-only (anything a client sends is ignored), about 15
messages a second while SAINT is listening or speaking and one on each change otherwise.

Just enough of RFC 6455 for that: the opening handshake, unmasked server text frames, ping-free.
"""

import base64
import hashlib
import json
import logging

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QHostAddress, QTcpServer

log = logging.getLogger("saint.state_socket")
PORT = 47811
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + _GUID).encode()).digest()).decode()


def text_frame(text: str) -> bytes:
    data = text.encode("utf-8")
    n = len(data)
    if n < 126:
        head = bytes([0x81, n])
    elif n < 65536:
        head = bytes([0x81, 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([0x81, 127]) + n.to_bytes(8, "big")
    return head + data


class StateSocket(QObject):
    def __init__(self, port: int = PORT, parent=None):
        super().__init__(parent)
        self._clients = []                       # sockets that finished the handshake
        self._last = ""
        self._srv = QTcpServer(self)
        self.listening = self._srv.listen(QHostAddress.LocalHost, port)
        if not self.listening:
            log.info("state_socket.unavailable %s", self._srv.errorString())
        self._srv.newConnection.connect(self._accept)

    def _accept(self):
        while self._srv.hasPendingConnections():
            sock = self._srv.nextPendingConnection()
            sock.setProperty("buf", b"")
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))

    def _read(self, sock):
        if sock in self._clients:
            sock.readAll()                       # read-only: whatever a client sends is ignored
            return
        buf = sock.property("buf") + bytes(sock.readAll())
        if len(buf) > 8192:
            sock.abort()
            return
        sock.setProperty("buf", buf)
        if b"\r\n\r\n" not in buf:
            return
        headers = {}
        for line in buf.split(b"\r\n")[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower()] = v.strip()
        key = headers.get(b"sec-websocket-key", b"").decode("ascii", "replace")
        if not key or headers.get(b"upgrade", b"").lower() != b"websocket":
            sock.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            sock.disconnectFromHost()
            return
        sock.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode())
        self._clients.append(sock)
        if self._last:
            sock.write(text_frame(self._last))

    def _drop(self, sock):
        if sock in self._clients:
            self._clients.remove(sock)
        sock.deleteLater()

    def publish(self, state: str, level: float):
        msg = json.dumps({"state": state, "level": round(float(level), 3)})
        self._last = msg
        frame = text_frame(msg)
        for sock in list(self._clients):
            sock.write(frame)

    def close(self):
        for sock in list(self._clients):
            sock.abort()
        self._srv.close()
