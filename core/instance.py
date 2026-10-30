"""
core/instance.py

One SAINT per user. A second `saint` (the taskbar pin, the overlay hotkey, the start menu,
`saint --page Store`) hands its request to the running SAINT instead of starting another or showing
"SAINT is already running".

    send("toggle-overlay")      -> True when a running SAINT took the command
    Server()                    -> in the running SAINT; .command(str) fires on the GUI thread

Commands: show, toggle-overlay, quit, page:<Name>, ask:<request> (the SAINT widget's box).
Transport: QLocalServer (a Unix socket on Linux, a named pipe on Windows), readable by this user
only. Anything else is refused, so nothing a caller sends reaches more than these actions (a request
is handled like one typed into SAINT).
"""

import getpass
import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

COMMANDS = re.compile(r"^(show|toggle-overlay|quit|page:[A-Za-z][A-Za-z &_-]{0,31}|ask:[^\r\n]{1,500})$")


def server_name() -> str:
    from core.paths import profile_name
    user = re.sub(r"[^A-Za-z0-9_-]", "", getpass.getuser()) or "user"
    profile = profile_name()
    return f"saint-{user}" + (f"-{re.sub(r'[^A-Za-z0-9_-]', '', profile)}" if profile else "")


def send(command: str, timeout_ms: int = 1500) -> bool:
    """Give ``command`` to the running SAINT. False when none answers (then start SAINT normally)."""
    if not COMMANDS.match(command):
        raise ValueError(f"not a SAINT command: {command!r}")
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((command + "\n").encode())
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    ok = sock.waitForReadyRead(timeout_ms) and bytes(sock.readAll()).strip() == b"ok"
    sock.disconnectFromServer()
    return ok


class Server(QObject):
    command = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._srv = QLocalServer(self)
        self._srv.setSocketOptions(QLocalServer.UserAccessOption)
        # Only the SAINT holding the lock gets here, so a leftover socket is from a crashed one.
        QLocalServer.removeServer(server_name())
        self.listening = self._srv.listen(server_name())
        self._srv.newConnection.connect(self._accept)

    def _accept(self):
        while self._srv.hasPendingConnections():
            conn = self._srv.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._read(c))
            conn.disconnected.connect(conn.deleteLater)

    def _read(self, conn):
        while conn.canReadLine():
            cmd = bytes(conn.readLine()).decode("utf-8", "replace").strip()
            ok = bool(COMMANDS.match(cmd))
            conn.write(b"ok\n" if ok else b"no\n")
            conn.flush()
            if ok:
                self.command.emit(cmd)

    def close(self):
        self._srv.close()
