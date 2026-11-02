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

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QTableView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from core import sysinfo
from core.sysinfo import human_bytes, human_duration

PAGES = ["Processes", "Performance", "App history", "Startup apps", "Users", "Services", "System information"]
REFRESH_MS = 2000
HISTORY = 60                                          # points per graph (2 minutes at 2 s)


def _palette():
    try:
        from ui.theme import current_palette
        return current_palette()
    except Exception:
        from ui.theme import palette_for
        return palette_for("Dark")


# --------------------------------------------------------------------------- widgets
class Graph(QWidget):
    """A small line graph of the last HISTORY values (0..100 unless ``maximum`` is given)."""

    def __init__(self, maximum=100.0, color=None):
        super().__init__()
        self.values = deque(maxlen=HISTORY)
        self.maximum = maximum
        self.color = QColor(color or _palette().accent)
        self.setMinimumHeight(70)

    def push(self, v):
        self.values.append(max(0.0, float(v or 0.0)))
        self.update()

    def paintEvent(self, _):
        p = _palette()
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g.setPen(QPen(QColor(p.border), 1))
        g.drawRoundedRect(r, 6, 6)
        if len(self.values) < 2:
            return
        top = self.maximum or max(self.values) or 1.0
        step = r.width() / (HISTORY - 1)
        x0 = r.right() - step * (len(self.values) - 1)
        pts = [QPointF(x0 + i * step, r.bottom() - min(1.0, v / top) * (r.height() - 4)) for i, v in
               enumerate(self.values)]
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), r.bottom())
        fill.lineTo(pts[0].x(), r.bottom())
        c = QColor(self.color)
        c.setAlpha(40)
        g.fillPath(fill, c)
        g.setPen(QPen(self.color, 1.6))
        g.drawPath(path)


class Metric(QFrame):
    """Title, big value, detail line and a graph."""

    def __init__(self, title, maximum=100.0):
        super().__init__()
        self.setObjectName("Panel")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("CardTitle")
        head.addWidget(t)
        head.addStretch()
        self.value = QLabel("—")
        self.value.setStyleSheet("font-size: 18px; font-weight: 600;")
        head.addWidget(self.value)
        v.addLayout(head)
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.detail)
        self.graph = Graph(maximum)
        v.addWidget(self.graph)


class ProcessModel(QAbstractTableModel):
    COLS = [("Name", "name"), ("PID", "pid"), ("User", "user"), ("Status", "status"), ("CPU", "cpu"),
            ("Memory", "memory"), ("Disk", "disk"), ("Threads", "threads")]

    def __init__(self):
        super().__init__()
        self.rows = []

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
            return self.COLS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        p = self.rows[index.row()]
        key = self.COLS[index.column()][1]
        if role == Qt.DisplayRole:
            if key == "cpu":
                return f"{p['cpu']:.1f} %"
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
        if role == Qt.ForegroundRole and p["critical"]:
            return QColor(_palette().warning)
        return None


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
        self.setWindowTitle("SAINT Task Manager")
        self.resize(1080, 700)
        self.procs = []
        self.sampler = sysinfo.ProcessSampler()
        self.rates = sysinfo.RateSampler()
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("SideNav")
        self.nav.setFixedWidth(190)
        self.nav.addItems(PAGES)
        self.stack = QStackedWidget()
        h.addWidget(self.nav)
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
        w, v = _page("Processes", "Everything running. Orange names keep the desktop or system alive.")
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, PID or command")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._refresh_processes())
        bar.addWidget(self.search, 1)
        self.only_mine = QCheckBox("Only my processes")
        self.only_mine.setChecked(True)
        self.only_mine.toggled.connect(lambda _c: self._refresh_processes())
        bar.addWidget(self.only_mine)
        self.end_btn = QPushButton("End task")
        self.end_btn.clicked.connect(self._end_selected)
        self.kill_btn = QPushButton("End now")
        self.kill_btn.setToolTip("Force it (SIGKILL): unsaved work in that app is lost")
        self.kill_btn.clicked.connect(lambda: self._end_selected(force=True))
        bar.addWidget(self.end_btn)
        bar.addWidget(self.kill_btn)
        v.addLayout(bar)
        self.model = ProcessModel()
        self.proxy = _Sort()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(4, Qt.DescendingOrder)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
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
        self.model.set_rows(rows)
        if sel is not None:
            for r in range(self.proxy.rowCount()):
                if self.proxy.data(self.proxy.index(r, 1)) == str(sel):
                    self.table.selectRow(r)
                    break
        cpu = sum(p["cpu"] for p in self.procs if p["pid"] != 0) / max(1, os.cpu_count() or 1)   # pid 0: idle
        self.proc_status.setText(f"{len(rows)} shown of {len(self.procs)} processes · CPU {cpu:.0f} %")

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
        w, v = _page("Performance", "Live use of each part of this computer. Values a driver doesn't report "
                                    "show as “not available”.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.perf_grid = QGridLayout(inner)
        self.perf_grid.setSpacing(12)
        self.perf_grid.setColumnStretch(0, 1)
        self.perf_grid.setColumnStretch(1, 1)
        self.m_cpu = Metric("CPU")
        self.m_mem = Metric("Memory")
        self.perf_grid.addWidget(self.m_cpu, 0, 0)
        self.perf_grid.addWidget(self.m_mem, 0, 1)
        self.m_gpu, self.m_disk, self.m_net = {}, {}, {}
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)
        self.cpu_static = sysinfo.cpu_info()
        self._gpu_at = 0.0
        self._gpus = []
        return w

    def _metric(self, store, key, title, maximum=100.0):
        if key not in store:
            m = Metric(title, maximum)
            n = 2 + len(self.m_gpu) + len(self.m_disk) + len(self.m_net)
            self.perf_grid.addWidget(m, n // 2, n % 2)
            store[key] = m
        return store[key]

    def _refresh_performance(self, visible=True):
        import psutil
        cpu = psutil.cpu_percent()
        self.m_cpu.graph.push(cpu)
        mem = sysinfo.memory()
        self.m_mem.graph.push(mem["percent"])
        rates = self.rates.sample()
        for name, d in rates["disks"].items():
            self._metric(self.m_disk, name, f"Disk {name}").graph.push(d["busy"] or 0)
        for name, n in rates["networks"].items():
            if n["up"]:
                self._metric(self.m_net, name, f"Network {name}", maximum=0).graph.push(
                    ((n["recv"] or 0) + (n["sent"] or 0)) / 1024)
        now = time.monotonic()
        if now - self._gpu_at > 4:                    # nvidia-smi is slow-ish: every 4 s is plenty
            self._gpu_at, self._gpus = now, sysinfo.gpus()
        for i, g in enumerate(self._gpus):
            self._metric(self.m_gpu, i, f"GPU {i}").graph.push(g["percent"] or 0)
        if not visible:
            return
        c = self.cpu_static
        freq = psutil.cpu_freq()
        temp = sysinfo.cpu_temp()
        self.m_cpu.value.setText(f"{cpu:.0f} %")
        self.m_cpu.detail.setText(
            f"{c['model']}\n{c['cores']} cores · {c['threads']} threads · "
            f"{f'{freq.current / 1000:.2f} GHz' if freq else 'speed not available'} · "
            f"{f'{temp} °C' if temp is not None else 'temperature not available'}")
        self.m_mem.value.setText(f"{mem['percent']:.0f} %")
        self.m_mem.detail.setText(f"{human_bytes(mem['used'])} of {human_bytes(mem['total'])} in use · "
                                  f"{human_bytes(mem['available'])} available\nSwap {human_bytes(mem['swap_used'])} of "
                                  f"{human_bytes(mem['swap_total'])}")
        for name, d in rates["disks"].items():
            m = self.m_disk[name]
            m.value.setText("—" if d["busy"] is None else f"{d['busy']:.0f} %")
            m.detail.setText(f"Read {human_bytes(d['read'], True)} · Write {human_bytes(d['write'], True)}")
        for name, n in rates["networks"].items():
            if name in self.m_net:
                m = self.m_net[name]
                m.value.setText(human_bytes((n["recv"] or 0) + (n["sent"] or 0), True))
                m.detail.setText(f"Receive {human_bytes(n['recv'], True)} · Send {human_bytes(n['sent'], True)}"
                                 + (f" · link {n['speed_mbps']} Mb/s" if n["speed_mbps"] else ""))
        for i, g in enumerate(self._gpus):
            m = self.m_gpu[i]
            m.value.setText("—" if g["percent"] is None else f"{g['percent']:.0f} %")
            mem_txt = (f"{human_bytes(g['memory_used'])} of {human_bytes(g['memory_total'])} video memory"
                       if g["memory_total"] else "video memory not reported")
            temp_txt = f"{g['temp_c']} °C" if g["temp_c"] is not None else "temperature not available"
            m.detail.setText(f"{g['name']} ({g['driver'] or 'driver unknown'})\n{mem_txt} · {temp_txt}")

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
