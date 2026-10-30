"""
ui/pages/halo_os.py

The Halo page: the operating-system side of SAINT in SAINT's own window — what was the separate SAINTOS
Control Center (ui/saintos_center.py: Welcome, SAINT's service, AI, voice, permissions, data,
diagnostics, Desktop & devices, Recovery, updates). Built the first time it's opened, so it costs
nothing at startup. Its "Apps" entry goes to the Store page.
"""

from ui.widgets import Page


class HaloPage(Page):
    def __init__(self, window=None):
        super().__init__("Halo", "Your system: SAINT's service and AI, voice, devices, recovery and updates.")
        self.window = window
        self._cc = None
        self._nav = None
        self._pending = ""

    def _build(self):
        if self._cc is not None:
            return
        from ui import saintos_center as sc
        self._cc = sc.build_window()                  # kept: it owns the page callbacks
        panel = self._cc.takeCentralWidget()
        self.root.addWidget(panel, 1)
        self._nav = self._cc._nav
        apps = sc.PAGES.index("Apps")
        self._nav.currentRowChanged.connect(lambda i: i == apps and self.window and self.window.navigate("Store"))

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

    def showEvent(self, e):
        super().showEvent(e)
        self._build()
        if self._pending:
            self._select()
