"""
ui/components/store_tiles.py

The Store's pieces: an art loader (icons and screenshots from core/store_art.py, fetched off the GUI thread
and cached on disk), app tiles laid out in a grid that reflows with the window, and the featured banner.
Artwork is the publisher's own (Flathub AppStream); an app without any shows its initial instead.
"""

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from ui.theme import current_palette
from ui.widgets import chip

TILE_W, TILE_H = 250, 172


class ArtLoader(QObject):
    """One per process. ``loaded(key, kind, path, meta)``: kind is "icon" or "shot"; path "" when there's none."""
    loaded = Signal(str, str, str, dict)
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = ArtLoader()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="store-art")
        self._done = {}                               # (key, kind) -> (path, meta)
        self._pending = set()

    def request(self, key: str, kind: str, flathub_id: str = "", url: str = ""):
        from core.config import config
        if not key or not config.get("store.artwork", True):
            return
        if (key, kind) in self._done:
            path, meta = self._done[(key, kind)]
            QTimer.singleShot(0, lambda: self.loaded.emit(key, kind, path, meta))
            return
        if (key, kind) in self._pending:
            return
        self._pending.add((key, kind))
        self._pool.submit(self._work, key, kind, flathub_id, url)

    def _work(self, key, kind, flathub_id, url):
        from core import store_art
        meta = {}
        try:
            if flathub_id:
                meta = store_art.metadata(flathub_id)
                url = url or meta.get("icon" if kind == "icon" else "screenshot") or ""
            path = store_art.image(url) if url else None
        except Exception:
            path = None
        meta = {k: v for k, v in (meta or {}).items() if not k.startswith("_")}
        self._done[(key, kind)] = (path or "", meta)
        self._pending.discard((key, kind))
        from PySide6.QtCore import QCoreApplication
        if QCoreApplication.instance() is not None and not QCoreApplication.closingDown():
            self.loaded.emit(key, kind, path or "", meta)


def rounded(pix: QPixmap, size: int, radius: float) -> QPixmap:
    out = QPixmap(size * 2, size * 2)
    out.setDevicePixelRatio(2.0)
    out.fill(Qt.transparent)
    g = QPainter(out)
    g.setRenderHint(QPainter.Antialiasing)
    g.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    g.setClipPath(path)
    g.drawPixmap(QRectF(0, 0, size, size), pix.scaled(size * 2, size * 2, Qt.KeepAspectRatio,
                                                       Qt.SmoothTransformation), QRectF(0, 0, size * 2, size * 2))
    g.end()
    return out


def monogram(name: str, size: int) -> QPixmap:
    """The app's initial on a tinted square: shown when there's no real icon (never a made-up logo)."""
    p = current_palette()
    out = QPixmap(size * 2, size * 2)
    out.setDevicePixelRatio(2.0)
    out.fill(Qt.transparent)
    g = QPainter(out)
    g.setRenderHint(QPainter.Antialiasing)
    g.setPen(Qt.NoPen)
    g.setBrush(QColor(p.accent_soft))
    g.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
    f = QFont()
    f.setPixelSize(int(size * 0.46))
    f.setWeight(QFont.DemiBold)
    g.setFont(f)
    g.setPen(QColor(p.accent))
    g.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (name.strip()[:1] or "?").upper())
    g.end()
    return out


def art_key(result):
    """(key, flathub id, direct icon url) for a Store result."""
    from core.store_art import flathub_id
    fid = flathub_id(result) or ""
    url = (result.extra or {}).get("icon", "")
    return (fid or url or f"{result.source}:{result.id}"), fid, url


class AppTile(QFrame):
    """One app: icon, name, developer, summary, where it comes from, and its action."""
    ICON = 56

    def __init__(self, result, on_install, on_open=None):
        super().__init__()
        self.result = result
        self.setObjectName("StoreTile")
        self.setFixedSize(TILE_W, TILE_H)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 14)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(self.ICON, self.ICON)
        self.icon.setPixmap(monogram(result.name, self.ICON))
        top.addWidget(self.icon, 0, Qt.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(1)
        self.name = QLabel(result.name)
        self.name.setObjectName("TileName")
        self.name.setWordWrap(True)
        self.dev = QLabel((result.extra or {}).get("developer", "") or "")
        self.dev.setObjectName("Faint")
        names.addWidget(self.name)
        names.addWidget(self.dev)
        names.addStretch()
        top.addLayout(names, 1)
        v.addLayout(top)
        self.about = QLabel(result.summary or "")
        self.about.setObjectName("Muted")
        self.about.setWordWrap(True)
        self.about.setMaximumHeight(36)
        self.about.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        v.addWidget(self.about, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(chip(result.label, "accent" if result.source == "featured" else ""))
        if result.source == "aur":
            e = result.extra
            bottom.addWidget(chip(f"{e.get('votes', 0)} votes", "warn" if e.get("out_of_date") else ""))
        bottom.addStretch()
        self.action = QPushButton("Install")
        self.action.setObjectName("TileAction")
        blocked = (result.extra or {}).get("blocked", "")
        if blocked:
            self.action.setText("Linux version")
            self.action.setToolTip(blocked)
        self.action.clicked.connect(lambda: on_install(self.result))
        bottom.addWidget(self.action)
        v.addLayout(bottom)
        self._on_open = on_open
        self._key, fid, url = art_key(result)
        loader = ArtLoader.get()
        loader.loaded.connect(self._art)
        loader.request(self._key, "icon", fid, url)

    def _art(self, key, kind, path, meta):
        if key != self._key or kind != "icon":
            return
        try:
            if path:
                pix = QPixmap(path)
                if not pix.isNull():
                    self.icon.setPixmap(rounded(pix, self.ICON, self.ICON * 0.22))
            if meta.get("developer") and not self.dev.text():
                self.dev.setText(meta["developer"])
            if meta.get("summary") and not self.about.text():
                self.about.setText(meta["summary"])
        except RuntimeError:                          # the tile was deleted while the art loaded
            pass

    def set_installed(self, installed: bool):
        if not installed:
            return
        self.action.setText("Open" if self._on_open else "Installed")
        self.action.setObjectName("TileOpen")
        self.action.style().unpolish(self.action)
        self.action.style().polish(self.action)
        try:
            self.action.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        if self._on_open:
            self.action.clicked.connect(lambda: self._on_open(self.result))
        else:
            self.action.setEnabled(False)


class TileGrid(QWidget):
    """Tiles in as many columns as fit; reflows when the page is resized."""

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.tiles = []
        self._cols = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def set_tiles(self, tiles):
        self.tiles = tiles
        self._cols = 0
        self._reflow()

    def _reflow(self):
        cols = max(1, (self.width() + 14) // (TILE_W + 14))
        if cols == self._cols:
            return
        self._cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // cols, i % cols, Qt.AlignLeft | Qt.AlignTop)
        self.grid.setColumnStretch(cols, 1)
        rows = (len(self.tiles) + cols - 1) // cols
        self.setMinimumHeight(rows * TILE_H + max(0, rows - 1) * 14)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reflow()


class HeroBanner(QFrame):
    """The featured app: its real screenshot fading into its brand colour, with name, maker and Install.
    Rotates through ``results`` every 9 s; the dots jump to one."""

    def __init__(self, results, on_install):
        super().__init__()
        self.setObjectName("StoreHero")
        self.setFixedHeight(264)
        self.results = results
        self.on_install = on_install
        self.i = 0
        self.shots, self.brand = {}, {}
        h = QHBoxLayout(self)
        h.setContentsMargins(34, 28, 34, 24)
        col = QVBoxLayout()
        col.setSpacing(6)
        eb = QLabel("FEATURED")
        eb.setObjectName("RailEyebrow")
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setStyleSheet("padding: 0px;")
        f = eb.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.6)
        eb.setFont(f)
        col.addWidget(eb)
        self.title = QLabel()
        self.title.setObjectName("HeroTitle")
        self.dev = QLabel()
        self.dev.setObjectName("HeroDev")
        self.about = QLabel()
        self.about.setObjectName("HeroAbout")
        self.about.setWordWrap(True)
        self.about.setMaximumWidth(380)
        col.addWidget(self.title)
        col.addWidget(self.dev)
        col.addSpacing(4)
        col.addWidget(self.about)
        col.addStretch()
        row = QHBoxLayout()
        self.install = QPushButton("Install")
        self.install.setObjectName("Primary")
        self.install.clicked.connect(lambda: self.on_install(self.results[self.i]))
        row.addWidget(self.install)
        self.dots = QHBoxLayout()
        self.dots.setSpacing(6)
        self._dots = []
        for n in range(len(results)):
            d = QPushButton()
            d.setObjectName("HeroDot")
            d.setFixedSize(QSize(8, 8))
            d.setCursor(Qt.PointingHandCursor)
            d.clicked.connect(lambda _=False, k=n: self.show_index(k))
            self.dots.addWidget(d)
            self._dots.append(d)
        row.addSpacing(16)
        row.addLayout(self.dots)
        row.addStretch()
        col.addLayout(row)
        h.addLayout(col)
        h.addStretch(1)
        loader = ArtLoader.get()
        loader.loaded.connect(self._art)
        for r in results:
            key, fid, _url = art_key(r)
            loader.request(key, "shot", fid)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.show_index((self.i + 1) % max(1, len(self.results))))
        self.timer.start(9000)
        self.show_index(0)

    def show_index(self, i):
        if not self.results:
            return
        self.i = i
        r = self.results[i]
        key = art_key(r)[0]
        self.title.setText(r.name)
        meta = self.brand.get(key, {})
        self.dev.setText(meta.get("developer") or r.label)
        self.about.setText(r.summary or meta.get("summary", ""))
        for n, d in enumerate(self._dots):
            d.setProperty("on", n == i)
            d.style().unpolish(d)
            d.style().polish(d)
        # Text on the brand colour: light on dark brands, dark on light ones.
        light = self._base().lightness() < 140
        ink = "#f4f5f7" if light else "#131417"
        soft = "rgba(244,245,247,0.72)" if light else "rgba(19,20,23,0.68)"
        self.title.setStyleSheet(f"color: {ink}; font-size: 30px; font-weight: 700;")
        self.dev.setStyleSheet(f"color: {soft}; font-size: 13px;")
        self.about.setStyleSheet(f"color: {ink}; font-size: 14px;")
        self.timer.start(9000)
        self.update()

    def _base(self) -> QColor:
        """The banner colour: the app's own brand colour for this theme, else SAINT's surface."""
        p = current_palette()
        key = art_key(self.results[self.i])[0] if self.results else ""
        meta = self.brand.get(key, {})
        base = QColor((meta.get("brand_dark") if p.dark else meta.get("brand_light")) or p.surface)
        return base.darker(115) if p.dark and base.lightness() > 90 else base

    def _art(self, key, kind, path, meta):
        if kind != "shot":
            return
        try:
            self.brand[key] = meta
            if path:
                pix = QPixmap(path)
                if not pix.isNull():
                    self.shots[key] = pix
            if self.results and art_key(self.results[self.i])[0] == key:
                self.show_index(self.i)
        except RuntimeError:
            pass

    def paintEvent(self, _):
        p = current_palette()
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        g.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 16, 16)
        g.setClipPath(clip)
        key = art_key(self.results[self.i])[0] if self.results else ""
        base = self._base()
        g.fillRect(r, base)
        shot = self.shots.get(key)
        if shot is not None:
            # The screenshot fills the right 62 %; a fade from the brand colour blends its left edge in.
            area = QRectF(r.width() * 0.38, 18, r.width() * 0.62 - 18, r.height() - 36)
            s = shot.scaled(int(area.width() * 2), int(area.height() * 2), Qt.KeepAspectRatioByExpanding,
                            Qt.SmoothTransformation)
            sx = (s.width() - area.width() * 2) / 2
            shot_path = QPainterPath()
            shot_path.addRoundedRect(area, 10, 10)
            g.save()
            g.setClipPath(shot_path, Qt.IntersectClip)
            g.drawPixmap(area, s, QRectF(sx, 0, area.width() * 2, area.height() * 2))
            g.restore()
        fade = QLinearGradient(QPointF(r.width() * 0.30, 0), QPointF(r.width() * 0.62, 0))
        c0, c1 = QColor(base), QColor(base)
        c0.setAlpha(255)
        c1.setAlpha(0)
        fade.setColorAt(0, c0)
        fade.setColorAt(1, c1)
        g.fillRect(r, fade)
        g.setClipping(False)
        g.setPen(QColor(p.border))
        g.drawRoundedRect(r, 16, 16)
