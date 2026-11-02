"""
ui/pages/halo_os.py

The Halo page: the operating-system side of SAINT in SAINT's own window — what was the separate SAINTOS
Control Center (ui/saintos_center.py: Welcome, SAINT's service, AI, voice, permissions, data,
diagnostics, Desktop & devices, Recovery, updates). Built the first time it's opened, so it costs
nothing at startup.

One level of navigation at a time: while Halo is open its own list is the left-hand navigation (SAINT's
sidebar folds to icons, giving the page the width), and Back at the top returns to where you were. "Apps"
stays on the Easy Install page, which links to the full Store.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from ui.widgets import Page


class HaloPage(Page):
    def __init__(self, window=None):
        super().__init__("Halo", "Your system: SAINT's service and AI, voice, devices, recovery and updates.")
        self.window = window
        self._cc = None
        self._nav = None
        self._pending = ""
        self._sidebar_was_collapsed = None
        back = QPushButton("←  Back")
        back.setObjectName("Ghost")
        back.setToolTip("Back to the page you came from")
        back.clicked.connect(self._back)
        self.root.insertWidget(0, back, 0, Qt.AlignLeft)

    def _build(self):
        if self._cc is not None:
            return
        from ui import saintos_center as sc
        self._cc = sc.build_window()                  # kept: it owns the page callbacks
        panel = self._cc.takeCentralWidget()
        self.root.addWidget(panel, 1)
        self._nav = self._cc._nav
        if self.window is not None:
            sc.open_store = lambda: self.window.navigate("Store")

    def pages(self):
        from ui import saintos_center as sc
        return sc.PAGES

    def open(self, name: str):
        """Show one of the Halo pages ("Recovery", "Updates", ...)."""
        self._pending = name
        if self.isVisible():
            self._select()

    def _select(self):
        self._build()
        from ui import saintos_center as sc
        if self._pending in sc.PAGES:
            self._nav.setCurrentRow(sc.PAGES.index(self._pending))
        self._pending = ""

    def _back(self):
        if self.window is not None:
            prev = getattr(self.window, "previous_page", "") or "Overview"
            self.window.navigate(prev if prev != "Halo" else "Overview")

    def showEvent(self, e):
        super().showEvent(e)
        self._build()
        if self._pending:
            self._select()
        sidebar = getattr(self.window, "sidebar", None)
        if sidebar is not None and self._sidebar_was_collapsed is None:
            self._sidebar_was_collapsed = bool(getattr(sidebar, "_collapsed", False))
            sidebar.set_collapsed(True)

    def hideEvent(self, e):
        super().hideEvent(e)
        sidebar = getattr(self.window, "sidebar", None)
        if sidebar is not None and self._sidebar_was_collapsed is not None:
            sidebar.set_collapsed(self._sidebar_was_collapsed)
            self._sidebar_was_collapsed = None
