"""
ui/remote_desktop.py — Halo Remote client in SAINT's desktop app (docs/REMOTE_DESKTOP.md).

Devices → a paired PC → "Remote desktop" opens a window with the same viewer the browser client uses
(web/remote/desktop.html), in Qt WebEngine (Chromium: WebRTC built in). The identity is this computer's SAINT
Link key: it stays in Python, and the page asks for X25519 shared secrets through Qt WebChannel. The relay
address comes from the PC itself over Link (``remote.info``), so nothing has to be typed in.

Qt WebEngine is built without proprietary codecs, so the window decodes VP8; the PC's host sends VP8 to clients
that say they can't decode H.264 (modules/remote_desktop/host.py ``client_codecs``).

The page is served from a loopback-only HTTP server (WebCrypto and ES modules need a secure origin, which
``http://127.0.0.1`` is and ``file://`` isn't). It only serves the static files of web/remote.
"""

import functools
import json
import logging
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from core.paths import resource_path
from modules.link import crypto
from modules.remote_desktop import protocol as P

log = logging.getLogger("saint.remote")

WEB_ROOT = resource_path("web", "remote")
CACHE_KEY = "remote_desktop.hosts"          # config: peer id -> {relay_url, account, device_id}


# ---------------------------------------------------------------------- #
# What the window needs, worked out without Qt (tested)
# ---------------------------------------------------------------------- #
def host_record(peer, info: dict) -> dict:
    """The PC as the page needs it, from the Link peer and its ``remote.info`` answer."""
    pub = bytes.fromhex(peer.public_key)
    account = P.account_id_for(pub)
    if info.get("account") and info["account"] != account:
        raise ValueError("That PC answered with someone else's account.")
    return {"device_id": peer.id, "pubkey": P.b64(pub), "name": peer.name, "account": account}


def resolve_info(link, peer, cache: dict) -> dict:
    """Ask the PC where its Halo Remote is (and remember it); fall back to what it said last time."""
    try:
        info = link.remote_info(peer)
    except Exception as e:                          # offline / old SAINT without remote.info
        log.info("remote.info_unavailable %s: %s", peer.id, e)
        info = cache.get(peer.id) or {}
    else:
        cache[peer.id] = {k: info.get(k, "") for k in ("relay_url", "account", "device_id")}
    if not info.get("relay_url"):
        raise LookupError(f"Remote desktop isn't set up on {peer.name}. On that PC run "
                          "`halo-remote set-relay wss://…/ws` and `halo-remote enable` (Halo OS).")
    return info


class _Handler(SimpleHTTPRequestHandler):
    # Windows' registry can map .js to text/plain, and module scripts refuse that.
    extensions_map = dict(SimpleHTTPRequestHandler.extensions_map, **{".js": "text/javascript", ".mjs": "text/javascript",
                                                                      ".css": "text/css", ".html": "text/html"})

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, *args):
        pass


class StaticServer:
    """web/remote on http://127.0.0.1:<random port>, started once, on a daemon thread."""
    _instance: Optional["StaticServer"] = None
    _lock = threading.Lock()

    def __init__(self, root: str):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=root))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="halo-remote-web", daemon=True).start()

    @classmethod
    def get(cls, root: str = str(WEB_ROOT)) -> "StaticServer":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(root)
            return cls._instance

    def url(self, page: str = "desktop.html") -> str:
        return "http://127.0.0.1:%d/%s" % (self.port, page)


# ---------------------------------------------------------------------- #
# The window (Qt WebEngine)
# ---------------------------------------------------------------------- #
def webengine_available() -> bool:
    try:
        import PySide6.QtWebChannel  # noqa: F401
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except Exception:
        return False


_open_windows = set()


def open_window(private_key: bytes, name: str, host: dict, relay_url: str):
    """Open (and keep alive) a remote-desktop window for ``host`` (see host_record)."""
    from PySide6.QtCore import QFile, QIODevice, QObject, QUrl, Slot
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    public = crypto.public_key(private_key)

    class Bridge(QObject):
        """What the page may ask the app. ``sharedSecret`` lends the Link key without handing it over."""

        def __init__(self, window):
            super().__init__(window)
            self.window = window

        @Slot(result=str)
        def config(self) -> str:
            return json.dumps({"me": {"device_id": P.device_id_for(public), "pubkey": P.b64(public), "name": name},
                               "relay_url": relay_url, "host": host})

        @Slot(str, result=str)
        def sharedSecret(self, peer_b64: str) -> str:
            try:
                return crypto.shared_secret(private_key, P.unb64(peer_b64, 32)).hex()
            except Exception:
                return ""

        @Slot(result=str)
        def clipboardText(self) -> str:
            return QGuiApplication.clipboard().text()

        @Slot(str)
        def setClipboardText(self, text: str):
            QGuiApplication.clipboard().setText(text)

        @Slot()
        def close(self):
            self.window.close()

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"Halo Remote — {host['name']}")
            self.resize(1280, 800)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            # off the record: nothing the page stores outlives the window
            self.profile = QWebEngineProfile(self)
            script = QWebEngineScript()
            f = QFile(":/qtwebchannel/qwebchannel.js")
            if f.open(QIODevice.ReadOnly):
                script.setSourceCode(bytes(f.readAll()).decode("utf-8"))
                f.close()
            script.setName("qwebchannel")
            script.setInjectionPoint(QWebEngineScript.DocumentCreation)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setRunsOnSubFrames(False)
            self.profile.scripts().insert(script)
            self.page = QWebEnginePage(self.profile, self)
            self.channel = QWebChannel(self.page)
            self.bridge = Bridge(self)
            self.channel.registerObject("saint", self.bridge)
            self.page.setWebChannel(self.channel)
            self.page.fullScreenRequested.connect(self._fullscreen)
            self.view = QWebEngineView(self)
            self.view.setPage(self.page)
            lay.addWidget(self.view)
            self.view.load(QUrl(StaticServer.get().url("desktop.html")))

        def _fullscreen(self, request):
            request.accept()
            self.showFullScreen() if request.toggleOn() else self.showNormal()

        def closeEvent(self, e):
            _open_windows.discard(self)
            self.view.setPage(None)          # tear the page down first: it holds the profile
            self.page.deleteLater()
            super().closeEvent(e)

    w = Window()
    _open_windows.add(w)
    w.show()
    w.raise_()
    w.activateWindow()
    return w
