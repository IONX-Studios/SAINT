"""
ui/taskmanager.py

SAINT Task Manager: processes, performance, app history, startup apps, users, services and system
information, from core/sysinfo.py. Its own small process (`saint-taskmgr`, Ctrl+Shift+Esc on Halo) so it
opens fast and keeps working when SAINT itself is busy or stopped: it loads no voice, AI or SAINT modules.

Only the page you're looking at refreshes (every 2 s); nothing is polled while the window is hidden.
"""

import os
import sys
import time
from collections import deque

if __package__ in (None, ""):                         # run as a script: /opt/saint/app/ui/taskmanager.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QStyledItemDelegate, QTableView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core import sysinfo
from core.sysinfo import human_bytes, human_duration

PAGES = ["Processes", "Performance", "App history", "Startup apps", "Users", "Services", "System information"]
PAGE_ICONS = {"Processes": "list", "Performance": "activity", "App history": "history", "Startup apps": "power",
              "Users": "user", "Services": "settings", "System information": "cpu"}
REFRESH_MS = 2000
HISTORY = 60                                          # points per graph (2 minutes at 2 s)


def _palette():
    try:
        from ui.theme import current_palette
        return current_palette()
    except Exception:
        from ui.theme import palette_for
        return palette_for("Dark")


_MONO = None


def _mono() -> QFont:
    """Tabular numbers, so columns of values don't jitter as they change."""
    global _MONO
    if _MONO is None:
        _MONO = QFont("Noto Sans Mono")
        _MONO.setStyleHint(QFont.Monospace)
        _MONO.setFamilies(["Noto Sans Mono", "Cascadia Mono", "Consolas", "monospace"])
        _MONO.setPointSizeF(QApplication.font().pointSizeF() * 0.95)
    return _MONO


def _tint(base: str, t: float) -> QColor:
    """SAINT orange at strength t (0..1): the heat of a busy cell."""
    c = QColor(base)
    c.setAlpha(int(18 + 150 * max(0.0, min(1.0, t))))
    return c


# --------------------------------------------------------------------------- widgets
class Graph(QWidget):
    """A line graph over a shared history deque: gridlines, a fill that fades to nothing, and a live end-point.
    ``maximum`` 0 scales to the largest value shown (network throughput)."""

    def __init__(self, values=None, maximum=100.0, compact=False):
        super().__init__()
        self.values = values if values is not None else deque(maxlen=HISTORY)
        self.maximum = maximum
        self.compact = compact
        self.setMinimumHeight(36 if compact else 220)

    def paintEvent(self, _):
        p = _palette()
        accent = QColor(p.accent)
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if not self.compact:
            g.setPen(QPen(QColor(p.border), 1))
            g.drawRoundedRect(r, 8, 8)
            grid = QPen(QColor(p.border), 1, Qt.DashLine)
            g.setPen(grid)
            for f in (0.25, 0.5, 0.75):
                y = r.top() + r.height() * f
                g.drawLine(QPointF(r.left() + 1, y), QPointF(r.right() - 1, y))
            r = r.adjusted(2, 6, -2, -2)
        if len(self.values) < 2:
            return
        top = self.maximum or (max(self.values) * 1.15) or 1.0
        step = r.width() / (HISTORY - 1)
        x0 = r.right() - step * (len(self.values) - 1)
        pts = [QPointF(x0 + i * step, r.bottom() - min(1.0, v / top) * r.height()) for i, v in enumerate(self.values)]
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), r.bottom())
        fill.lineTo(pts[0].x(), r.bottom())
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        hi, lo = QColor(accent), QColor(accent)
        hi.setAlpha(90 if not self.compact else 70)
        lo.setAlpha(0)
        grad.setColorAt(0, hi)
        grad.setColorAt(1, lo)
        g.fillPath(fill, grad)
        g.setPen(QPen(accent, 1.4 if self.compact else 1.8))
        g.drawPath(path)
        if not self.compact:                           # the live end-point
            halo = QColor(accent)
            halo.setAlpha(60)
            g.setPen(Qt.NoPen)
            g.setBrush(halo)
            g.drawEllipse(pts[-1], 6, 6)
            g.setBrush(accent)
            g.drawEllipse(pts[-1], 2.6, 2.6)


class DeviceTile(QFrame):
    """One device in the Performance list: its sparkline, name and current reading. Click to see it in detail."""
    clicked = Signal(str)

    def __init__(self, key, title, values, maximum=100.0):
        super().__init__()
        self.key = key
        self.setObjectName("DeviceTile")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 8, 12, 8)
        h.setSpacing(12)
        self.graph = Graph(values, maximum, compact=True)
        self.graph.setFixedSize(68, 40)
        h.addWidget(self.graph)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 600;")
        self.sub = QLabel("—")
        self.sub.setObjectName("Muted")
        col.addWidget(self.title)
        col.addWidget(self.sub)
        h.addLayout(col, 1)

    def set_active(self, on):
        self.setObjectName("DeviceTileActive" if on else "DeviceTile")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        self.clicked.emit(self.key)
        super().mousePressEvent(e)


class Stat(QWidget):
    """A small-caps label over a monospace value."""

    def __init__(self, label):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        lab = QLabel(label.upper())
        lab.setObjectName("StatLabel")
        f = lab.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        lab.setFont(f)
        self.value = QLabel("—")
        self.value.setObjectName("ReadoutSmall")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(lab)
        v.addWidget(self.value)


class ProcessModel(QAbstractTableModel):
    COLS = [("Name", "name"), ("PID", "pid"), ("User", "user"), ("Status", "status"), ("CPU", "cpu"),
            ("Memory", "memory"), ("Disk", "disk"), ("Threads", "threads")]

    def __init__(self):
        super().__init__()
        self.rows = []
        self.totals = {}                              # column key -> "23 %" shown above the column name
        self.ncpu = max(1, os.cpu_count() or 1)
        self.ram = 0
        try:
            import psutil
            self.ram = psutil.virtual_memory().total
        except Exception:
            pass

    def set_totals(self, totals):
        self.totals = totals
        self.headerDataChanged.emit(Qt.Horizontal, 0, len(self.COLS) - 1)

    def heat(self, p, key) -> float:
        """How hard a process works this resource, 0..1, for the cell's orange tint."""
        if key == "cpu":
            return p["cpu"] / self.ncpu / 25.0         # a quarter of the whole machine is full heat
        if key == "memory" and self.ram:
            return p["memory"] / self.ram / 0.08       # 8 % of RAM is full heat
        if key == "disk" and p["disk_read"] is not None:
            return ((p["disk_read"] or 0) + (p["disk_write"] or 0)) / (20 * 2 ** 20)
        return 0.0

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            name, key = self.COLS[section]
            return f"{self.totals[key]}\n{name}" if key in self.totals else name
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole:
            key = self.COLS[section][1]
            return int((Qt.AlignRight if key in ("pid", "cpu", "memory", "disk", "threads") else Qt.AlignLeft)
                       | Qt.AlignBottom)
        return None

    def data(self, index, role=Qt.DisplayRole):
        p = self.rows[index.row()]
        key = self.COLS[index.column()][1]
        if role == Qt.DisplayRole:
            if key == "cpu":                          # share of the whole machine, like the total above it
                return f"{p['cpu'] / self.ncpu:.1f} %"
            if key == "memory":
                return human_bytes(p["memory"])
            if key == "disk":
                if p["disk_read"] is None:
                    return "—"
                return human_bytes((p["disk_read"] or 0) + (p["disk_write"] or 0), rate=True)
            if key == "user":
                return p["user"].split("\\")[-1]
            return str(p[key])
        if role == Qt.UserRole:                       # sort key
            if key == "disk":
                return (p["disk_read"] or 0) + (p["disk_write"] or 0)
            return p[key] if key in ("pid", "cpu", "memory", "threads") else str(p[key]).lower()
        if role == Qt.TextAlignmentRole and key in ("pid", "cpu", "memory", "disk", "threads"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return p["command"] or p["name"]
        if role == Qt.ForegroundRole and p["critical"] and key == "name":
            return QColor(_palette().warning)
        if role == Qt.BackgroundRole and key in ("cpu", "memory", "disk"):
            t = self.heat(p, key)
            return _tint(_palette().accent, t) if t > 0.02 else None
        if role == Qt.FontRole and key in ("pid", "cpu", "memory", "disk", "threads"):
            return _mono()
        return None


class _HeatDelegate(QStyledItemDelegate):
    """Paints the model's heat tint under the text (the stylesheet's item rule would otherwise hide it)."""

    def paint(self, painter, option, index):
        bg = index.data(Qt.BackgroundRole)
        if isinstance(bg, QColor):
            painter.fillRect(option.rect.adjusted(1, 1, -1, -1), bg)
        super().paint(painter, option, index)


class _Sort(QSortFilterProxyModel):
    def lessThan(self, a, b):
        return self.sourceModel().data(a, Qt.UserRole) < self.sourceModel().data(b, Qt.UserRole)


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setSortingEnabled(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _fill(table, rows):
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for r, cells in enumerate(rows):
        for c, val in enumerate(cells):
            item = QTableWidgetItem("" if val is None else str(val))
            table.setItem(r, c, item)
    table.setSortingEnabled(True)


def _page(title, sub=""):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(24, 20, 24, 20)
    v.setSpacing(12)
    t = QLabel(title)
    t.setObjectName("PageTitle")
    v.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("PageSubtitle")
        s.setWordWrap(True)
        v.addWidget(s)
    return w, v


# --------------------------------------------------------------------------- window
class TaskManager(QMainWindow):
    def __init__(self):
        super().__init__()
        from ui.components.side_rail import SideRail
        self.setWindowTitle("SAINT Task Manager")
        self.resize(1160, 740)
        self.procs = []
        self.sampler = sysinfo.ProcessSampler()
        self.rates = sysinfo.RateSampler()
        self.latest = {}
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.rail = SideRail("SAINT · Halo", "Task Manager", [(p, PAGE_ICONS[p]) for p in PAGES],
                             footer="Ctrl+Shift+Esc opens this anywhere")
        self.nav = self.rail.nav
        self.stack = QStackedWidget()
        h.addWidget(self.rail)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        builders = [self._build_processes, self._build_performance, self._build_history, self._build_startup,
                    self._build_users, self._build_services, self._build_system]
        for b in builders:
            self.stack.addWidget(b())
        self.nav.currentRowChanged.connect(self._switch)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.nav.setCurrentRow(0), self.search.setFocus()))
        QShortcut(QKeySequence(Qt.Key_Delete), self, activated=self._end_selected)
        self.nav.setCurrentRow(0)

    # ---------------------------------------------------------------- lifecycle
    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        self.timer.start(REFRESH_MS)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()

    def _switch(self, i):
        self.stack.setCurrentIndex(i)
        self.refresh(full=True)

    def refresh(self, full=False):
        page = PAGES[self.stack.currentIndex()]
        if page in ("Processes", "Users", "App history"):
            self.procs = self.sampler.sample()
        # The graphs keep their history while other pages are shown.
        self._refresh_performance(visible=page == "Performance")
        {"Processes": self._refresh_processes, "Users": self._refresh_users,
         "App history": self._refresh_history if full else lambda: None,
         "Startup apps": self._refresh_startup if full else lambda: None,
         "Services": self._refresh_services if full else lambda: None,
         "System information": self._refresh_system if full else lambda: None,
         "Performance": lambda: None}[page]()

    # ---------------------------------------------------------------- processes
    def _build_processes(self):
        w, v = _page("Processes", "Busier cells glow brighter. Names in amber keep the desktop or system running.")
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, PID or command   (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._refresh_processes())
        bar.addWidget(self.search, 1)
        self.only_mine = QCheckBox("Only my processes")
        self.only_mine.setChecked(True)
        self.only_mine.toggled.connect(lambda _c: self._refresh_processes())
        bar.addWidget(self.only_mine)
        self.kill_btn = QPushButton("End now")
        self.kill_btn.setObjectName("Ghost")
        self.kill_btn.setToolTip("Force it (SIGKILL): unsaved work in that app is lost")
        self.kill_btn.clicked.connect(lambda: self._end_selected(force=True))
        self.end_btn = QPushButton("End task")
        self.end_btn.setObjectName("Primary")
        self.end_btn.setToolTip("Ask it to quit (Delete)")
        self.end_btn.clicked.connect(self._end_selected)
        bar.addWidget(self.kill_btn)
        bar.addWidget(self.end_btn)
        v.addLayout(bar)
        self.model = ProcessModel()
        self.proxy = _Sort()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setObjectName("ProcTable")
        self.table.setItemDelegate(_HeatDelegate(self.table))
        self.table.setStyleSheet("QHeaderView::section { padding-right: 18px; }")
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(4, Qt.DescendingOrder)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        head = self.table.horizontalHeader()
        head.setMinimumHeight(46)
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 76), (2, 110), (3, 90), (4, 86), (5, 100), (6, 100), (7, 76)):
            self.table.setColumnWidth(col, width)
        v.addWidget(self.table, 1)
        self.proc_status = QLabel("")
        self.proc_status.setObjectName("Faint")
        v.addWidget(self.proc_status)
        return w

    def _me(self):
        try:
            import getpass
            return getpass.getuser()
        except Exception:
            return ""

    def _refresh_processes(self):
        sel = self._selected_pid()
        rows = sysinfo.filter_processes(self.procs, self.search.text(), self._me() if self.only_mine.isChecked() else "")
        lt = self.latest
        if lt:
            self.model.set_totals({"cpu": f"{lt['cpu']:.0f} %", "memory": f"{lt['mem']:.0f} %",
                                   "disk": human_bytes(lt["disk"], True)})
        self.model.set_rows(rows)
        if sel is not None:
            for r in range(self.proxy.rowCount()):
                if self.proxy.data(self.proxy.index(r, 1)) == str(sel):
                    self.table.selectRow(r)
                    break
        self.proc_status.setText(f"{len(rows)} shown of {len(self.procs)} processes")

    def _selected_pid(self):
        idx = self.table.selectionModel().selectedRows() if hasattr(self, "table") else []
        if not idx:
            return None
        return int(self.proxy.data(self.proxy.index(idx[0].row(), 1)))

    def _end_selected(self, force=False):
        pid = self._selected_pid()
        if pid is None:
            self.proc_status.setText("Select a process first.")
            return
        p = next((p for p in self.procs if p["pid"] == pid), None)
        if p is None:
            return
        if p["critical"]:
            ok = QMessageBox.warning(
                self, "End a system process?",
                f"{p['name']} keeps your desktop or system running. Ending it can close your session or stop the "
                "computer from working until you restart.\n\nEnd it anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ok != QMessageBox.Yes:
                return
        elif force:
            if QMessageBox.question(self, "End now?", f"Force {p['name']} to stop? Unsaved work in it is lost.") \
                    != QMessageBox.Yes:
                return
        self.proc_status.setText(sysinfo.end_process(pid, force=force))
        self.procs = self.sampler.sample()
        self._refresh_processes()

    # ---------------------------------------------------------------- performance
    def _build_performance(self):
        """Devices down the left, each with its sparkline; the chosen one large on the right."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.NoFrame)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left.setFixedWidth(250)
        tiles = QWidget()
        self.tile_box = QVBoxLayout(tiles)
        self.tile_box.setContentsMargins(14, 20, 6, 20)
        self.tile_box.setSpacing(4)
        self.tile_box.addStretch()
        left.setWidget(tiles)
        h.addWidget(left)
        rule = QFrame()
        rule.setFixedWidth(1)
        rule.setStyleSheet(f"background: {_palette().border};")
        h.addWidget(rule)

        detail = QWidget()
        v = QVBoxLayout(detail)
        v.setContentsMargins(28, 22, 28, 22)
        v.setSpacing(14)
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(2)
        self.d_title = QLabel("CPU")
        self.d_title.setObjectName("PageTitle")
        self.d_model = QLabel("")
        self.d_model.setObjectName("Muted")
        self.d_model.setTextInteractionFlags(Qt.TextSelectableByMouse)
        names.addWidget(self.d_title)
        names.addWidget(self.d_model)
        top.addLayout(names, 1)
        self.d_value = QLabel("—")
        self.d_value.setObjectName("Readout")
        self.d_value.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        top.addWidget(self.d_value)
        v.addLayout(top)
        self.d_axis = QLabel("")
        self.d_axis.setObjectName("StatLabel")
        v.addWidget(self.d_axis)
        self.d_graph = Graph()
        v.addWidget(self.d_graph, 1)
        self.d_stats = QGridLayout()
        self.d_stats.setHorizontalSpacing(28)
        self.d_stats.setVerticalSpacing(14)
        v.addLayout(self.d_stats)
        h.addWidget(detail, 1)

        self.devices = {}
        self.selected = "cpu"
        self.cpu_static = sysinfo.cpu_info()
        self._gpu_at = 0.0
        self._gpus = []
        self._device("cpu", "CPU")
        self._device("mem", "Memory")
        self._select("cpu")
        return w

    def _device(self, key, title, maximum=100.0):
        if key not in self.devices:
            values = deque(maxlen=HISTORY)
            tile = DeviceTile(key, title, values, maximum)
            tile.clicked.connect(self._select)
            self.tile_box.insertWidget(self.tile_box.count() - 1, tile)
            self.devices[key] = {"title": title, "values": values, "max": maximum, "tile": tile, "model": "",
                                 "value": "—", "stats": [], "axis": ""}
        return self.devices[key]

    def _select(self, key):
        self.selected = key
        for k, d in self.devices.items():
            d["tile"].set_active(k == key)
        d = self.devices[key]
        self.d_graph.values = d["values"]
        self.d_graph.maximum = d["max"]
        self._show_detail()

    def _show_detail(self):
        d = self.devices[self.selected]
        self.d_title.setText(d["title"])
        self.d_model.setText(d["model"])
        self.d_value.setText(d["value"])
        self.d_axis.setText(d["axis"])
        if [s for s, _ in d["stats"]] != getattr(self, "_stat_keys", None):
            while self.d_stats.count():
                item = self.d_stats.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._stat_widgets = []
            for i, (label, _val) in enumerate(d["stats"]):
                s = Stat(label)
                self.d_stats.addWidget(s, i // 4, i % 4)
                self._stat_widgets.append(s)
            self._stat_keys = [s for s, _ in d["stats"]]
        for s, (_label, val) in zip(self._stat_widgets, d["stats"]):
            s.value.setText(val)
        self.d_graph.update()

    def _set(self, key, value, sub, model, stats, axis):
        d = self.devices[key]
        d.update(value=value, model=model, stats=stats, axis=axis)
        d["tile"].sub.setText(sub)
        d["tile"].graph.update()

    def _refresh_performance(self, visible=True):
        import psutil
        cpu = psutil.cpu_percent()
        mem = sysinfo.memory()
        rates = self.rates.sample()
        now = time.monotonic()
        if now - self._gpu_at > 4:                    # nvidia-smi is slow-ish: every 4 s is plenty
            self._gpu_at, self._gpus = now, sysinfo.gpus()
        disk_total = sum((d["read"] or 0) + (d["write"] or 0) for d in rates["disks"].values())
        self.latest = {"cpu": cpu, "mem": mem["percent"], "disk": disk_total}

        c = self.cpu_static
        freq = psutil.cpu_freq()
        temp = sysinfo.cpu_temp()
        speed = f"{freq.current / 1000:.2f} GHz" if freq else "not available"
        self.devices["cpu"]["values"].append(cpu)
        self._set("cpu", f"{cpu:.0f}%", f"{cpu:.0f}%  ·  {speed}", c["model"],
                  [("Utilization", f"{cpu:.0f} %"), ("Speed", speed),
                   ("Temperature", f"{temp} °C" if temp is not None else "not available"),
                   ("Processes", str(len(self.procs) or len(psutil.pids()))),
                   ("Cores", str(c["cores"])), ("Threads", str(c["threads"])),
                   ("Up for", human_duration(time.time() - psutil.boot_time()))],
                  "% utilization over 2 minutes")
        self.devices["mem"]["values"].append(mem["percent"])
        self._set("mem", f"{mem['percent']:.0f}%",
                  f"{human_bytes(mem['used'])} of {human_bytes(mem['total'])}",
                  f"{human_bytes(mem['total'])} installed",
                  [("In use", human_bytes(mem["used"])), ("Available", human_bytes(mem["available"])),
                   ("Swap used", human_bytes(mem["swap_used"])), ("Swap size", human_bytes(mem["swap_total"]))],
                  "% of memory in use over 2 minutes")
        for i, g in enumerate(self._gpus):
            key = f"gpu{i}"
            self._device(key, f"GPU {i}")["values"].append(g["percent"] or 0)
            pct = "—" if g["percent"] is None else f"{g['percent']:.0f}%"
            vram = (f"{human_bytes(g['memory_used'])} / {human_bytes(g['memory_total'])}" if g["memory_total"]
                    else "not reported")
            self._set(key, pct, f"{pct}  ·  {g['temp_c']} °C" if g["temp_c"] is not None else pct,
                      f"{g['name']}  ·  {g['driver'] or 'driver unknown'}",
                      [("Utilization", pct.replace("%", " %")), ("Video memory", vram),
                       ("Temperature", f"{g['temp_c']} °C" if g["temp_c"] is not None else "not available"),
                       ("Vendor", g["vendor"])],
                      "% utilization over 2 minutes")
        for name, d in rates["disks"].items():
            key = f"disk:{name}"
            self._device(key, f"Disk {name}")["values"].append(d["busy"] or 0)
            busy = "—" if d["busy"] is None else f"{d['busy']:.0f}%"
            self._set(key, busy, f"{busy}  ·  R {human_bytes(d['read'], True)}", f"/dev/{name}" if os.name != "nt" else name,
                      [("Active time", busy.replace("%", " %")), ("Read", human_bytes(d["read"], True)),
                       ("Write", human_bytes(d["write"], True))],
                      "% of time the disk was busy over 2 minutes")
        for name, n in rates["networks"].items():
            if not n["up"]:
                continue
            key = f"net:{name}"
            total = (n["recv"] or 0) + (n["sent"] or 0)
            self._device(key, name, maximum=0)["values"].append(total)
            self._set(key, human_bytes(total, True),
                      f"↓ {human_bytes(n['recv'], True)}  ↑ {human_bytes(n['sent'], True)}",
                      f"Link {n['speed_mbps']} Mb/s" if n["speed_mbps"] else "link speed not reported",
                      [("Receive", human_bytes(n["recv"], True)), ("Send", human_bytes(n["sent"], True)),
                       ("Link speed", f"{n['speed_mbps']} Mb/s" if n["speed_mbps"] else "not reported")],
                      "throughput over 2 minutes (scale follows the peak)")
        if visible:
            self._show_detail()

    # ---------------------------------------------------------------- app history
    def _build_history(self):
        w, v = _page("App history", "Processor time each app has used since it started. Linux keeps no history "
                                    "across restarts, so this begins at each app's start.")
        self.hist_table = _table(["App", "CPU time", "Processes", "Running since"])
        v.addWidget(self.hist_table, 1)
        return w

    def _refresh_history(self):
        rows = sysinfo.app_history(self.procs or self.sampler.sample())
        _fill(self.hist_table, [(a["name"], human_duration(a["cpu_seconds"]) if a["cpu_seconds"] >= 60
                                 else f"{a['cpu_seconds']:.1f} s", a["processes"],
                                 time.strftime("%d %b %H:%M", time.localtime(a["since"])) if a["since"] else "")
                                for a in rows[:300]])

    # ---------------------------------------------------------------- startup
    def _build_startup(self):
        w, v = _page("Startup apps", "Apps that start when you sign in. Turning one off only affects you.")
        self.start_table = _table(["App", "Status", "From", "Command"])
        v.addWidget(self.start_table, 1)
        row = QHBoxLayout()
        on, off = QPushButton("Enable"), QPushButton("Disable")
        on.clicked.connect(lambda: self._toggle_startup(True))
        off.clicked.connect(lambda: self._toggle_startup(False))
        self.start_msg = QLabel("")
        self.start_msg.setObjectName("Muted")
        for b in (on, off):
            row.addWidget(b)
        row.addWidget(self.start_msg, 1)
        v.addLayout(row)
        return w

    def _refresh_startup(self):
        self.startup = sysinfo.autostart_entries() if os.name != "nt" else []
        _fill(self.start_table, [(e["name"], "Enabled" if e["enabled"] else "Disabled", e["scope"], e["command"])
                                 for e in self.startup])
        if os.name == "nt":
            self.start_msg.setText("Startup apps are read from the Linux autostart folders.")

    def _toggle_startup(self, enabled):
        r = self.start_table.currentRow()
        if r < 0:
            self.start_msg.setText("Select an app first.")
            return
        name = self.start_table.item(r, 0).text()
        entry = next((e for e in self.startup if e["name"] == name), None)
        if entry:
            try:
                self.start_msg.setText(sysinfo.set_autostart(entry, enabled))
            except OSError as e:
                self.start_msg.setText(f"Couldn't change it: {e}")
            self._refresh_startup()

    # ---------------------------------------------------------------- users
    def _build_users(self):
        w, v = _page("Users", "Who is signed in and what their processes use.")
        self.user_table = _table(["User", "Sessions", "Signed in since", "Processes", "CPU", "Memory"])
        v.addWidget(self.user_table, 1)
        return w

    def _refresh_users(self):
        _fill(self.user_table, [(u["name"], u["sessions"] or "—",
                                 time.strftime("%d %b %H:%M", time.localtime(u["since"])) if u["since"] else "—",
                                 u["processes"], f"{u['cpu']:.1f} %", human_bytes(u["memory"]))
                                for u in sysinfo.users(self.procs)])

    # ---------------------------------------------------------------- services
    def _build_services(self):
        w, v = _page("Services", "Background services (systemd). System services ask for your password to change.")
        top = QHBoxLayout()
        self.svc_scope = QComboBox()
        self.svc_scope.addItems(["System services", "My services"])
        self.svc_scope.currentIndexChanged.connect(lambda _i: self._refresh_services())
        self.svc_filter = QLineEdit()
        self.svc_filter.setPlaceholderText("Filter services")
        self.svc_filter.setClearButtonEnabled(True)
        self.svc_filter.textChanged.connect(lambda _t: self._show_services())
        top.addWidget(self.svc_scope)
        top.addWidget(self.svc_filter, 1)
        v.addLayout(top)
        self.svc_table = _table(["Service", "State", "Detail", "At boot", "Description"])
        v.addWidget(self.svc_table, 1)
        row = QHBoxLayout()
        for action in sysinfo.SERVICE_ACTIONS:
            b = QPushButton(action.capitalize())
            b.clicked.connect(lambda _=False, a=action: self._service_action(a))
            row.addWidget(b)
        self.svc_msg = QLabel("")
        self.svc_msg.setObjectName("Muted")
        self.svc_msg.setWordWrap(True)
        row.addWidget(self.svc_msg, 1)
        v.addLayout(row)
        self.svc_rows = []
        return w

    def _refresh_services(self):
        self.svc_rows = sysinfo.services(user=self.svc_scope.currentIndex() == 1)
        if not self.svc_rows:
            self.svc_msg.setText("systemd isn't available here." if os.name == "nt" or not
                                 __import__("shutil").which("systemctl") else "")
        self._show_services()

    def _show_services(self):
        q = self.svc_filter.text().strip().lower()
        _fill(self.svc_table, [(s["name"], s["active"], s["sub"], s["enabled"], s["description"]) for s in self.svc_rows
                               if not q or q in s["name"].lower() or q in s["description"].lower()])

    def _service_action(self, action):
        r = self.svc_table.currentRow()
        if r < 0:
            self.svc_msg.setText("Select a service first.")
            return
        name = self.svc_table.item(r, 0).text()
        user = self.svc_scope.currentIndex() == 1
        if action in ("stop", "disable") and QMessageBox.question(
                self, f"{action.capitalize()} {name}?",
                f"{action.capitalize()} {name}? Something that depends on it may stop working.") != QMessageBox.Yes:
            return
        import subprocess
        try:
            res = subprocess.run(sysinfo.service_argv(name, action, user), capture_output=True, text=True, timeout=60)
            self.svc_msg.setText(f"{name}: {action} done" if res.returncode == 0 else
                                 f"{name}: {action} failed — {(res.stderr or res.stdout).strip()[:200]}")
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            self.svc_msg.setText(f"{name}: {e}")
        self._refresh_services()

    # ---------------------------------------------------------------- system
    def _build_system(self):
        w, v = _page("System information")
        self.sys_table = _table(["", ""])
        self.sys_table.horizontalHeader().hide()
        self.sys_table.setSortingEnabled(False)
        self.sys_table.setColumnWidth(0, 200)
        v.addWidget(self.sys_table, 1)
        return w

    def _refresh_system(self):
        s = sysinfo.system()
        c = self.cpu_static
        mem = sysinfo.memory()
        rows = [("Operating system", s["os"]), ("Kernel", s["kernel"]), ("Computer name", s["hostname"]),
                ("Computer", s["machine"] or "not reported"), ("Motherboard", s["board"] or "not reported"),
                ("Firmware (BIOS/UEFI)", s["bios"] or "not reported"), ("Architecture", s["arch"]),
                ("Started", time.strftime("%d %b %Y %H:%M", time.localtime(s["boot_time"]))),
                ("Up for", human_duration(s["uptime_s"])),
                ("Processor", f"{c['model']} ({c['cores']} cores, {c['threads']} threads)"),
                ("Memory", human_bytes(mem["total"]))]
        for i, g in enumerate(self._gpus or sysinfo.gpus()):
            rows.append((f"Graphics {i}", f"{g['name']} · {g['driver'] or 'driver unknown'}"))
        for name in self.rates.sample()["disks"]:
            rows.append((f"Disk {name} health", sysinfo.disk_health(name) if os.name != "nt" else "not available"))
        b = sysinfo.battery()
        if b:
            rows.append(("Battery", f"{b['percent']} % · {b['status']}" +
                         (f" · health {b['health_percent']} % of design capacity" if b["health_percent"] else "")
                         + (f" · {b['cycles']} cycles" if b["cycles"] else "")))
        self.sys_table.setRowCount(len(rows))
        for r, (k, val) in enumerate(rows):
            self.sys_table.setItem(r, 0, QTableWidgetItem(k))
            self.sys_table.setItem(r, 1, QTableWidgetItem(str(val)))


def apply_style(app):
    """SAINT's own palette and stylesheet, so the Task Manager looks like the rest of SAINT."""
    try:
        from core.config import config
        from ui.theme import build_stylesheet, current_palette, qt_palette
        a = config.get("appearance", {}) or {}
        app.setPalette(qt_palette(current_palette()))
        app.setStyleSheet(build_stylesheet(a.get("theme", "Dark"), a.get("accent", "#feaa34"),
                                           a.get("font_family", "Noto Sans" if os.name != "nt" else "Segoe UI"),
                                           a.get("font_size", 13)))
    except Exception:
        pass


def main(argv=None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("SAINT Task Manager")
    app.setDesktopFileName("saint-taskmgr")
    apply_style(app)
    win = TaskManager()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
