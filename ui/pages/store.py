"""
ui/pages/store.py

The Store page (Halo): one search over Arch, the AUR, Flathub and Windows apps (core/store.py), SAINT's
picks by category when nothing is searched, and one install queue with live progress. Every install
ends in a notification naming what was installed (core/saintos_apps.finish).

AUR packages are made by Arch users, not Arch, and Windows apps run under Wine: both say so, with
votes / the PKGBUILD link / what to expect, and wait for a yes before anything runs.
"""

from collections import deque

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ui.widgets import Card, Page, Segmented, chip, clear_layout, run_async

FILTERS = [("All", None), ("Arch", "repo"), ("Flathub", "flathub"), ("AUR", "aur"), ("Windows", "windows")]


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
        self.query.setPlaceholderText("Search apps — Blender, Roblox Studio, VS Code, Notepad++…")
        self.query.returnPressed.connect(self.search)
        bar.addWidget(self.query, 1)
        go = QPushButton("Search")
        go.clicked.connect(self.search)
        bar.addWidget(go)
        self.root.addLayout(bar)
        self.filter = Segmented([f[0] for f in FILTERS])
        self.filter.changed.connect(lambda _i: self._show_results())
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
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 8, 0)
        self.list.setSpacing(8)
        self.list.addStretch()
        scroll.setWidget(inner)
        self.root.addWidget(scroll, 1)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        self.root.addWidget(self.status)
        self._show_picks()

    # ------------------------------------------------------------------ results
    def _row(self, r, installed=False):
        card = Card()
        top = QHBoxLayout()
        name = QLabel(f"<b>{r.name}</b>" + (f"  <span style='opacity:.6'>{r.version}</span>" if r.version else ""))
        top.addWidget(name)
        top.addWidget(chip(r.label, "accent" if r.source == "featured" else ""))
        if r.source == "aur":
            e = r.extra
            top.addWidget(chip(f"{e.get('votes', 0)} votes" + (" · out of date" if e.get("out_of_date") else ""),
                               "warn" if e.get("out_of_date") else ""))
        top.addStretch()
        blocked = r.extra.get("blocked", "")
        if installed or r.installed:
            top.addWidget(chip("Installed", "ok"))
        elif blocked:
            alt = r.extra.get("instead")
            if alt:
                btn = QPushButton("Get the Linux version")
                btn.clicked.connect(lambda _=False, a=alt: self._search_pick(a))
                top.addWidget(btn)
        else:
            btn = QPushButton("Install")
            btn.clicked.connect(lambda _=False, res=r: self.install(res))
            top.addWidget(btn)
        card.body.addLayout(top)
        about = QLabel(blocked or r.summary)
        about.setObjectName("WarnText" if blocked else "Muted")
        about.setWordWrap(True)
        card.body.addWidget(about)
        return card

    def _set_rows(self, rows):
        clear_layout(self.list)
        for w in rows:
            self.list.addWidget(w)
        self.list.addStretch()

    def _show_picks(self):
        from core import saintos_apps as sa
        from core.store import Result
        try:
            catalog = sa.load_catalog()
        except Exception as e:
            self.status.setText(f"SAINT's app list isn't available: {e}")
            return
        rows = []
        for cat in dict.fromkeys(a.category for a in catalog.values()):
            head = QLabel(cat.upper())
            head.setObjectName("CardTitle")
            rows.append(head)
            for a in (a for a in catalog.values() if a.category == cat):
                rows.append(self._row(Result("featured", a.id, a.name, a.about, extra={"method": a.method, "ref": a.ref})))
        self._set_rows(rows)

    def _search_pick(self, catalog_id):
        self.query.setText(catalog_id)
        self.search()

    def search(self):
        q = self.query.text().strip()
        if not q:
            self._show_picks()
            return
        from core import store
        self.status.setText(f"Searching Arch, Flathub, the AUR and Windows apps for “{q}”…")
        run_async(lambda: store.search(q), self._got, lambda e: self.status.setText(f"Search failed: {e}"))

    def _got(self, results):
        self._results = results
        self._show_results()

    def _show_results(self):
        want = FILTERS[max(0, self.filter.index())][1]
        shown = [r for r in self._results if want is None or r.source == want]
        self._set_rows([self._row(r) for r in shown])
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
            return sa.run_plan(steps, on_line=self._bridge.line.emit,
                               on_step=lambda i, n, s, st, p: self._bridge.step.emit(i, n, s.label, st, p))
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
        title, body = sa.finish(result or {r.id: False}, catalog, names={r.id: r.name})
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
