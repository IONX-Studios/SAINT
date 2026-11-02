"""
ui/pages/store.py

The Store page (Halo): one search over Arch, the AUR, Flathub and Windows apps (core/store.py), and one install
queue with live progress. With nothing searched it opens like a store: a featured banner (the app's real
screenshot and brand colour), then SAINT's picks in shelves of tiles by category. Icons, screenshots and
developers are the publishers' own from Flathub (core/store_art.py, cached); an app without any shows its
initial. Every install ends in a notification naming what was installed (core/saintos_apps.finish).

AUR packages are made by Arch users, not Arch, and Windows apps run under Wine: both say so, with
votes / the PKGBUILD link / what to expect, and wait for a yes before anything runs.
"""

from collections import deque

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ui.components.store_tiles import AppTile, HeroBanner, TileGrid
from ui.widgets import Card, Page, Segmented, clear_layout, run_async

FILTERS = [("All", None), ("Arch", "repo"), ("Flathub", "flathub"), ("AUR", "aur"), ("Windows", "windows")]
FEATURED = ["heroic", "blender", "obs", "steam", "krita", "spotify"]      # the banner's rotation (catalog ids)


class _Bridge(QObject):
    line = Signal(str)
    step = Signal(int, int, str, str, int)


class StorePage(Page):
    def __init__(self, window=None):
        super().__init__("Store", "Every app for Halo in one search: Arch, the AUR, Flathub and Windows apps.")
        self.window = window
        self._queue = deque()
        self._busy = False
        self._results = []
        self._bridge = _Bridge()
        self._bridge.line.connect(self._log_line)
        self._bridge.step.connect(self._on_step)

        bar = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setObjectName("StoreSearch")
        self.query.setPlaceholderText("Search every app — Blender, Roblox Studio, VS Code, Notepad++…")
        self.query.setClearButtonEnabled(True)
        # Live search: results follow the text as it's typed (debounced), and an empty box shows the picks at once.
        self._seq = 0
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.search)
        self.query.textChanged.connect(self._typed)
        self.query.returnPressed.connect(self.search)
        bar.addWidget(self.query, 1)
        self.root.addLayout(bar)
        self.filter = Segmented([f[0] for f in FILTERS])
        self.filter.changed.connect(lambda _i: self._show_results() if self.query.text().strip() else None)
        self.root.addWidget(self.filter, 0, Qt.AlignLeft)

        # Install queue: what's running, how far, and its log.
        self.job = Card("Installing")
        self.job_label = QLabel("")
        self.job_label.setWordWrap(True)
        self.job.body.addWidget(self.job_label)
        self.job_bar = QProgressBar()
        self.job_bar.setRange(0, 100)
        self.job_bar.setTextVisible(False)
        self.job_bar.setFixedHeight(4)
        self.job.body.addWidget(self.job_bar)
        self.job_log = QPlainTextEdit()
        self.job_log.setReadOnly(True)
        self.job_log.setMaximumHeight(140)
        self.job_log.hide()
        log_btn = QPushButton("Details")
        log_btn.setObjectName("Ghost")
        log_btn.clicked.connect(lambda: self.job_log.setVisible(not self.job_log.isVisible()))
        self.job.header.addWidget(log_btn)
        self.job.body.addWidget(self.job_log)
        self.job.hide()
        self.root.addWidget(self.job)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 10, 0)
        self.list.setSpacing(14)
        self.list.addStretch()
        scroll.setWidget(inner)
        self.root.addWidget(scroll, 1)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        self.root.addWidget(self.status)
        self._show_picks()

    # ------------------------------------------------------------------ results
    def _tile(self, r):
        t = AppTile(r, self._act, self._open if self._openable(r) else None)
        if r.installed:
            t.set_installed(True)
        return t

    @staticmethod
    def _openable(r):
        return r.source == "flathub" or (r.source == "featured" and (r.extra or {}).get("method") == "flatpak")

    def _act(self, r):
        alt = (r.extra or {}).get("instead")
        if (r.extra or {}).get("blocked"):
            if alt:
                self._search_pick(alt)
            return
        self.install(r)

    def _open(self, r):
        import subprocess
        ref = r.id if r.source == "flathub" else (r.extra or {}).get("ref", "")
        try:
            subprocess.Popen(["flatpak", "run", ref], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status.setText(f"Opening {r.name}…")
        except OSError as e:
            self.status.setText(f"Couldn't open {r.name}: {e}")

    def _set_rows(self, rows):
        clear_layout(self.list)
        for w in rows:
            self.list.addWidget(w)
        self.list.addStretch()

    def _picks(self):
        from core import saintos_apps as sa
        from core.store import Result
        catalog = sa.load_catalog()
        return catalog, [Result("featured", a.id, a.name, a.about,
                                extra={"method": a.method, "ref": a.ref, "art": a.art, "category": a.category})
                         for a in catalog.values()]

    def _show_picks(self):
        try:
            catalog, picks = self._picks()
        except Exception as e:
            self.status.setText(f"SAINT's app list isn't available: {e}")
            return
        by_id = {r.id: r for r in picks}
        rows = [HeroBanner([by_id[i] for i in FEATURED if i in by_id], self._act)]
        tiles = {}
        for cat in dict.fromkeys(r.extra["category"] for r in picks):
            head = QLabel(cat)
            head.setObjectName("ShelfTitle")
            rows.append(head)
            grid = TileGrid()
            shelf = [self._tile(r) for r in picks if r.extra["category"] == cat]
            tiles.update({t.result.id: t for t in shelf})
            grid.set_tiles(shelf)
            rows.append(grid)
        self._set_rows(rows)
        self.status.setText("")
        self._mark_installed(catalog, tiles)

    def _mark_installed(self, catalog, tiles):
        """Installed apps say so (and Flatpaks open), checked off the GUI thread."""
        import os
        if os.name == "nt":
            return
        from core import saintos_apps as sa

        def check():
            return {i: sa.installed(catalog[i]) for i in tiles if i in catalog}

        def show(res):
            for i, ok in (res or {}).items():
                try:
                    tiles[i].set_installed(ok)
                except RuntimeError:                  # the shelf was rebuilt meanwhile
                    pass
        run_async(check, show, lambda _e: None)

    def _search_pick(self, catalog_id):
        self.query.setText(catalog_id)
        self.search()

    def _typed(self, text):
        if not text.strip():
            self._debounce.stop()
            self.search()
        else:
            self._debounce.start(350)

    def search(self):
        self._debounce.stop()
        self._seq += 1
        seq = self._seq
        q = self.query.text().strip()
        if not q:
            self._results = []
            self.status.setText("")
            self._show_picks()
            return
        if len(q) < 2:
            return
        from core import store
        self.status.setText(f"Searching Arch, Flathub, the AUR and Windows apps for “{q}”…")
        run_async(lambda: store.search(q), lambda res: self._got(seq, res),
                  lambda e: seq == self._seq and self.status.setText(f"Search failed: {e}"))

    def _got(self, seq, results):
        if seq != self._seq:                  # an older query finished after a newer one started
            return
        self._results = results
        self._show_results()

    def _show_results(self):
        want = FILTERS[max(0, self.filter.index())][1]
        # A Windows app that's blocked because a Linux version exists only points at that version: skip it.
        shown = [r for r in self._results if (want is None or r.source == want)
                 and not (r.source == "windows" and (r.extra or {}).get("blocked"))]
        grid = TileGrid()
        grid.set_tiles([self._tile(r) for r in shown])
        self._set_rows([grid] if shown else [])
        self.status.setText(f"{len(shown)} result(s)" if shown else "Nothing found. Try another name.")

    # ------------------------------------------------------------------ installing
    def install(self, r):
        if r.source == "aur":
            e = r.extra
            ok = QMessageBox.question(
                self, "Install from the AUR?",
                f"{r.name} comes from the Arch User Repository: packages made by Arch users, not by Arch.\n\n"
                f"{e.get('votes', 0)} votes · maintainer {e.get('maintainer') or 'none'}"
                + (" · marked out of date" if e.get("out_of_date") else "")
                + f"\n\nYou can read exactly what it builds first: {e.get('url', '')}\n\nInstall it?")
            if ok != QMessageBox.Yes:
                return
        elif r.source == "windows":
            ok = QMessageBox.question(
                self, "Install a Windows app?",
                f"{r.name} is a Windows app. SAINT downloads its installer from the publisher (checked against "
                "winget's SHA-256), installs it with Wine in its own folder and adds it to the start menu.\n\n"
                "Most apps work; some don't (anti-cheat, drivers, some copy protection). Install it?")
            if ok != QMessageBox.Yes:
                return
        self._queue.append(r)
        self.job.show()
        self._next()

    def _next(self):
        if self._busy or not self._queue:
            return
        from core import store
        r = self._queue.popleft()
        try:
            steps = store.plan(r)
        except Exception as e:
            self.job_label.setText(f"{r.name}: {e}")
            return self._next()
        self._busy = True
        self._current = r
        self.job_log.clear()
        self.job_bar.setValue(0)
        left = f" ({len(self._queue)} more in the queue)" if self._queue else ""
        self.job_label.setText(f"{r.name}: getting ready…{left}")

        def work():
            from core import saintos_apps as sa
            errors, warnings = sa.blockers()
            if errors:
                raise RuntimeError(" ".join(errors))
            for w in warnings:
                self._bridge.line.emit(w)
            self._errors = {}
            return sa.run_plan(steps, on_line=self._bridge.line.emit,
                               on_step=lambda i, n, s, st, p: self._bridge.step.emit(i, n, s.label, st, p),
                               errors=self._errors)
        run_async(work, self._done, self._failed)

    def _log_line(self, text):
        self.job_log.appendPlainText(text)

    def _on_step(self, i, n, label, status, pct):
        what = {"started": "starting", "progress": f"{pct} %", "done": "done", "failed": "failed"}[status]
        self.job_label.setText(f"{self._current.name} — step {i + 1} of {n}: {label} — {what}")
        self.job_bar.setValue(int(((i + (pct / 100 if status != "started" else 0)) / max(n, 1)) * 100))

    def _done(self, result):
        from core import saintos_apps as sa
        try:
            catalog = sa.load_catalog()
        except Exception:
            catalog = {}
        r = self._current
        title, body = sa.finish(result or {r.id: False}, catalog, names={r.id: r.name},
                                errors=getattr(self, "_errors", None))
        if result and not all(result.values()):
            self.job_log.show()                           # a failure shows its log without another click
        self.job_label.setText(f"{title}: " + body.replace("\n", "  "))
        self.job_bar.setValue(100)
        self._busy = False
        if self._queue:
            self._next()
        elif self._results:
            self._show_results()

    def _failed(self, e):
        self.job_label.setText(f"{self._current.name}: {e}")
        self._busy = False
        self._next()
