"""
ui/saintos_center.py

SAINTOS Control Center: one window for the parts of SAINT that belong to the operating system.

    Welcome       what SAINTOS is and the three things to do first
    Apps          the SAINTOS App Installer: tick your usual apps, install them in one go
    Service       SAINT's user service: status, start / stop / restart, start at login, recent log
    AI            provider, model and address; detects a local Ollama and lists its models
    Voice         microphone, wake word, speech recognition / voice backends
    Permissions   Safe / Confirm / Autonomous (core/permissions.py — the same gate SAINT uses)
    Capabilities  what this desktop lets SAINT do and how to fix what's missing (core/platform_caps.py)
    Data          where SAINT keeps settings, memory and models; open the folder
    Diagnostics   SAINT's self-test and a full read-only diagnostics folder
    Updates       Fedora updates through Discover; the SAINTOS build this system came from

    python -m ui.saintos_center [--welcome] [--page NAME]

Every button does what it says through SAINT's own modules (core.config, core.permissions,
modules.ai.providers, core.platform_caps) or systemctl --user; nothing here needs root.
Settings it writes are the same keys SAINT's own Settings page edits.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SERVICE = "saint.service"
BUILD_INFO = Path("/usr/share/saintos/build-info.json")
HALO_BUILD_INFO = Path("/usr/share/halo/build-info.json")
OS_RELEASE = Path("/etc/os-release")


# --------------------------------------------------------------------------- #
# Logic (no Qt) — unit-tested in tests/test_saintos_center.py
# --------------------------------------------------------------------------- #
def _run(args: List[str], timeout: float = 15) -> Tuple[int, str]:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(args)} timed out"
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def systemd_user_available() -> bool:
    return os.name != "nt" and shutil.which("systemctl") is not None


def service_status(run: Callable = _run) -> Dict[str, str]:
    """{"active": "active|inactive|failed|...", "enabled": "enabled|disabled|...", "available": "yes|no"}"""
    if not systemd_user_available() and run is _run:
        return {"active": "unavailable", "enabled": "unavailable", "available": "no"}
    _, active = run(["systemctl", "--user", "is-active", SERVICE])
    _, enabled = run(["systemctl", "--user", "is-enabled", SERVICE])
    return {"active": active.strip().splitlines()[0] if active.strip() else "unknown",
            "enabled": enabled.strip().splitlines()[0] if enabled.strip() else "unknown", "available": "yes"}


_SERVICE_ACTIONS = {
    "start": ["start"], "stop": ["stop"], "restart": ["restart"],
    "enable": ["enable", "--now"], "disable": ["disable", "--now"],
}


def service_action(action: str, run: Callable = _run) -> Tuple[bool, str]:
    if action not in _SERVICE_ACTIONS:
        raise ValueError(f"unknown service action {action!r}")
    code, out = run(["systemctl", "--user", *_SERVICE_ACTIONS[action], SERVICE], timeout=30)
    return code == 0, out.strip() or f"systemctl --user {action} {SERVICE}: done"


def service_log(lines: int = 200, run: Callable = _run) -> str:
    _, out = run(["journalctl", "--user", "-u", SERVICE, "-n", str(int(lines)), "--no-pager", "-o", "short-iso"])
    return out


PERMISSION_MODES = [
    ("safe", "Safe", "Only low-risk actions (open apps, read, play music). Anything else is refused."),
    ("confirm", "Confirm", "Low-risk actions run. One “go ahead?” for a plan with medium steps; "
                           "high-risk steps always ask. (Default)"),
    ("autonomous", "Autonomous", "Low and medium actions run without asking. High-risk steps still ask "
                                 "unless you turned that off in SAINT's settings."),
]


def set_permission_mode(mode: str):
    from core.config import config
    if mode not in {m for m, _, _ in PERMISSION_MODES}:
        raise ValueError(f"unknown permission mode {mode!r}")
    config.set("automation.permission_mode", mode, persist=True)


def detect_ollama(base_url: str) -> Dict:
    """{"running": bool, "models": [names], "error": str} from Ollama's own API — nothing is downloaded."""
    from modules.ai.providers import OllamaProvider
    p = OllamaProvider()
    try:
        status = p.test_connection(base_url, timeout=3)
    except Exception as e:
        return {"running": False, "models": [], "error": str(e), "installed": bool(shutil.which("ollama"))}
    if not status.get("connected"):
        return {"running": False, "models": [], "error": status.get("error", "not reachable"),
                "installed": bool(shutil.which("ollama"))}
    try:
        models = [m.get("name", "") for m in p.list_models(base_url, timeout=5)]
    except Exception as e:
        return {"running": True, "models": [], "error": f"listing models failed: {e}", "installed": True}
    return {"running": True, "models": [m for m in models if m], "error": "", "installed": True}


def save_ai(provider: str, model: str, base_url: str):
    from core.config import config
    if provider not in ("ollama", "openai"):
        raise ValueError("provider must be ollama or openai")
    if not model.strip():
        raise ValueError("choose a model")
    config.set("ai.provider", provider, persist=False)
    config.set("ai.model", model.strip(), persist=False)
    config.set("ai.base_url", base_url.strip() or "http://localhost:11434", persist=True)


def microphones() -> List[str]:
    try:
        import sounddevice as sd
        return sorted({d["name"] for d in sd.query_devices() if d.get("max_input_channels", 0) > 0})
    except Exception:
        return []


def data_locations() -> Dict[str, str]:
    from core import paths
    return {"Settings, memory, history": str(paths.data_dir()), "Models": str(paths.models_dir()),
            "Logs": str(paths.data_dir() / "logs"), "SAINT program": str(paths.PROJECT_ROOT)}


def build_info() -> Dict:
    for path in (HALO_BUILD_INFO, BUILD_INFO):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def is_halo() -> bool:
    """Halo (Arch Linux based) rather than SAINTOS (Fedora based)?"""
    try:
        return any(line.strip() == "ID=halo" for line in OS_RELEASE.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def product() -> str:
    return "Halo" if is_halo() else "SAINTOS"


def update_command() -> List[str]:
    """What "Update now" runs, in a terminal so prompts and progress are visible."""
    if is_halo():
        upd = "paru -Syu" if shutil.which("paru") else "sudo pacman -Syu"
    else:
        upd = "sudo dnf upgrade --refresh"
    return ["konsole", "-e", "bash", "-c", f"{upd}; echo; read -rp 'Done. Press Enter to close.'"]


def os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    import platform
    return f"{platform.system()} {platform.release()}"


def open_path(path: str) -> bool:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    if shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", path], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return False


def launch_saint() -> bool:
    exe = "/usr/bin/saint"
    if os.path.exists(exe):
        subprocess.Popen([exe], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    root = Path(__file__).resolve().parent.parent
    subprocess.Popen([sys.executable, str(root / "app.py")], cwd=str(root), start_new_session=True)
    return True


def run_selftest() -> Dict:
    """SAINT's offline self-test (no voice models, no network) in a throwaway data folder."""
    import tempfile
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="saintos-selftest-") as tmp:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SAINT_DATA_DIR=tmp, SAINT_SELFTEST_NO_VOICE="1")
        report = Path(tmp) / "report.json"
        try:
            subprocess.run([sys.executable, str(root / "app.py"), "--selftest", str(report)], cwd=str(root),
                           env=env, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            return {"passed": False, "checks": [{"name": "selftest", "ok": False, "detail": "timed out"}]}
        try:
            return json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return {"passed": False, "checks": [{"name": "selftest", "ok": False, "detail": f"no report: {e}"}]}


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #
PAGES = ["Welcome", "Apps", "Service", "AI", "Voice", "Permissions", "Capabilities", "Data", "Diagnostics", "Updates"]


def build_window(start_page: str = "Welcome"):
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                                   QLineEdit, QListWidget, QMainWindow, QPlainTextEdit, QPushButton, QRadioButton,
                                   QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
    from core.config import config

    class _Bridge(QObject):
        done = Signal(object, object)            # (callback, result) back on the GUI thread
        line = Signal(object, str)               # (callback, text) for streamed command output

    bridge = _Bridge()
    bridge.done.connect(lambda cb, result: cb(result))
    bridge.line.connect(lambda cb, text: cb(text))

    def in_background(fn, then):
        """Run ``fn`` off the GUI thread (systemctl, Ollama, the self-test) and hand the result to ``then``."""
        def work():
            try:
                result = fn()
            except Exception as e:
                result = e
            bridge.done.emit(then, result)
        threading.Thread(target=work, daemon=True).start()

    def heading(text, sub=""):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 8)
        t = QLabel(text)
        t.setObjectName("title")
        t.setStyleSheet("font-size: 20px; font-weight: 600;")
        v.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setStyleSheet("color: palette(mid);")
            v.addWidget(s)
        return box

    def page(title, sub=""):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(10)
        v.addWidget(heading(title, sub))
        return w, v

    win = QMainWindow()
    win.setWindowTitle(f"{product()} Control Center")
    icon = Path("/usr/share/icons/hicolor/256x256/apps/saintos.png")
    win.setWindowIcon(QIcon(str(icon)) if icon.exists() else QIcon(str(Path(__file__).parent.parent / "SAINT.png")))
    win.resize(980, 640)
    root = QWidget()
    h = QHBoxLayout(root)
    h.setContentsMargins(0, 0, 0, 0)
    nav = QListWidget()
    nav.setFixedWidth(190)
    nav.addItems(PAGES)
    stack = QStackedWidget()
    h.addWidget(nav)
    h.addWidget(stack, 1)
    win.setCentralWidget(root)

    # ---------------- Welcome
    halo = is_halo()
    w, v = page(f"Welcome to {product()}", f"{os_name()} — KDE Plasma with SAINT built in.")
    info = QLabel(
        f"{product()} is a normal {'Arch Linux' if halo else 'Fedora'} KDE desktop: everything works without "
        "SAINT, without a network and without an AI model.\n\nTo get SAINT talking:\n"
        "  1. AI — install Ollama and pull one model you choose (SAINT never downloads models on its own), "
        "or point SAINT at a provider you already use.\n"
        "  2. Voice — pick your microphone. SAINT's speech models download the first time you start it.\n"
        "  3. Service — start SAINT now, and turn on “start at login” if you want it always there.\n\n"
        + ("Halo is built from Arch Linux packages; it isn't an official Arch Linux product." if halo else
           "SAINTOS is a Fedora Remix: built from Fedora Linux packages, not an official Fedora product."))
    info.setWordWrap(True)
    v.addWidget(info)
    wrow = QHBoxLayout()
    open_btn = QPushButton("Open SAINT")
    open_btn.clicked.connect(launch_saint)
    apps_btn = QPushButton("Install my apps…")
    apps_btn.clicked.connect(lambda: nav.setCurrentRow(PAGES.index("Apps")))
    wrow.addWidget(open_btn)
    wrow.addWidget(apps_btn)
    wrow.addStretch(1)
    v.addLayout(wrow)
    v.addStretch(1)
    stack.addWidget(w)

    # ---------------- Apps (the SAINTOS App Installer)
    from PySide6.QtWidgets import QGridLayout, QGroupBox, QScrollArea
    from core import saintos_apps as sa
    w, v = page("Install your apps", "Tick the apps you use and install them all at once. Desktop apps come from "
                                     f"Flathub for your user (no password). {'Arch' if is_halo() else 'Fedora'} packages ask for your "
                                     "password once.")
    try:
        catalog = sa.load_catalog()
    except (OSError, ValueError) as e:
        catalog = {}
        v.addWidget(QLabel(f"The app catalog couldn't be loaded: {e}"))
    boxes: Dict[str, QCheckBox] = {}
    holder = QWidget()
    hv = QVBoxLayout(holder)
    for cat in dict.fromkeys(a.category for a in catalog.values()):
        gb = QGroupBox(cat)
        grid = QGridLayout(gb)
        for n, app in enumerate(a for a in catalog.values() if a.category == cat):
            cb = QCheckBox(app.name)
            cb.setToolTip(f"{app.about}\n{app.method}: {app.ref}")
            boxes[app.id] = cb
            grid.addWidget(cb, n // 3, n % 3)
        hv.addWidget(gb)
    hv.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(holder)
    v.addWidget(scroll, 3)
    arow = QHBoxLayout()
    common_btn = QPushButton("Common picks")
    clear_btn = QPushButton("Clear")
    install_btn = QPushButton("Install selected")
    astatus = QLabel("")
    for b in (common_btn, clear_btn, install_btn):
        arow.addWidget(b)
    arow.addWidget(astatus, 1)
    v.addLayout(arow)
    alog = QPlainTextEdit()
    alog.setReadOnly(True)
    alog.setMaximumBlockCount(5000)
    v.addWidget(alog, 2)
    COMMON = ["steam", "claude-code", "discord", "spotify", "vscode", "chrome", "vlc", "obs", "dev-tools"]
    common_btn.clicked.connect(lambda: [boxes[i].setChecked(True) for i in COMMON if i in boxes])
    clear_btn.clicked.connect(lambda: [b.setChecked(False) for b in boxes.values()])

    def mark_installed():
        def check():
            return {i: sa.installed(catalog[i]) for i in catalog}
        def show(res):
            if isinstance(res, dict):
                for i, ok in res.items():
                    if ok:
                        boxes[i].setText(f"{catalog[i].name} ✓")
                        boxes[i].setToolTip(boxes[i].toolTip() + "\n(already installed)")
        in_background(check, show)

    def do_install():
        chosen = [i for i, b in boxes.items() if b.isChecked()]
        if not chosen:
            astatus.setText("Tick at least one app.")
            return
        steps = sa.plan(chosen, catalog)
        missing = sa.missing_tools(steps)
        if missing:
            astatus.setText("Missing: " + ", ".join(missing))
            return
        errors, warnings = sa.blockers()
        if errors:
            astatus.setText(" ".join(errors))
            return
        install_btn.setEnabled(False)
        alog.clear()
        note = (" ".join(warnings) + "\n") if warnings else ""
        astatus.setText(f"{note}Installing {len(chosen)} app(s)…")

        def progress(i, total, step, status, pct):
            what = {"started": "starting", "progress": f"{pct} %", "done": "done", "failed": "failed"}[status]
            astatus.setText(f"{note}Step {i + 1} of {total}: {step.label} — {what}")

        def finished(res):
            install_btn.setEnabled(True)
            if isinstance(res, Exception):
                astatus.setText(f"Stopped: {res}")
                return
            title, body = sa.finish(res, catalog)          # menu refreshed + notification listing the apps
            astatus.setText(f"{title}. " + body.replace("\n", "  "))
            mark_installed()
        in_background(lambda: sa.run_plan(steps, on_line=lambda t: bridge.line.emit(alog.appendPlainText, t),
                                          on_step=lambda *a: bridge.line.emit(lambda _t: progress(*a), "")),
                      finished)
    install_btn.clicked.connect(do_install)
    if catalog and os.name != "nt":
        mark_installed()
    stack.addWidget(w)

    # ---------------- Service
    w, v = page("SAINT service", "SAINT runs as your user (systemd --user): no root, logs in the journal.")
    status_lbl = QLabel("Checking…")
    v.addWidget(status_lbl)
    row = QHBoxLayout()
    log_view = QPlainTextEdit()
    log_view.setReadOnly(True)

    def refresh_service():
        def show(st):
            if isinstance(st, Exception):
                status_lbl.setText(f"Couldn't read the service: {st}")
                return
            if st["available"] != "yes":
                status_lbl.setText("systemd user services aren't available here (this page works on SAINTOS).")
                return
            status_lbl.setText(f"Running: <b>{st['active']}</b> &nbsp;·&nbsp; Start at login: <b>{st['enabled']}</b>")
        in_background(service_status, show)
        in_background(service_log, lambda out: log_view.setPlainText(out if isinstance(out, str) else str(out)))

    for label, action in (("Start", "start"), ("Stop", "stop"), ("Restart", "restart"),
                          ("Start at login", "enable"), ("Don't start at login", "disable")):
        b = QPushButton(label)
        b.clicked.connect(lambda _=False, a=action: in_background(
            lambda: service_action(a), lambda res: (status_lbl.setText(
                res[1] if not isinstance(res, Exception) else str(res)), refresh_service())))
        row.addWidget(b)
    row.addStretch(1)
    v.addLayout(row)
    v.addWidget(QLabel("Recent log"))
    v.addWidget(log_view, 1)
    stack.addWidget(w)

    # ---------------- AI
    w, v = page("AI", "Local first: Ollama on this machine. Hosted providers are optional.")
    form = QFormLayout()
    provider = QComboBox()
    provider.addItems(["ollama", "openai"])
    provider.setCurrentText(config.get("ai.provider", "ollama") if config.get("ai.provider") in ("ollama", "openai")
                            else "ollama")
    model = QComboBox()
    model.setEditable(True)
    model.setCurrentText(config.get("ai.model", ""))
    base = QLineEdit(config.get("ai.base_url", "http://localhost:11434"))
    form.addRow("Provider", provider)
    form.addRow("Model", model)
    form.addRow("Address", base)
    v.addLayout(form)
    ai_status = QLabel("")
    ai_status.setWordWrap(True)
    v.addWidget(ai_status)
    note = QLabel("Hosted providers (OpenAI-compatible): the API key is still entered in SAINT's own Settings and "
                  "kept in KWallet (Secret Service), never in the config file. Spending limits are planned "
                  "(docs/SAINTOS_RELEASE_STATUS.md).")
    note.setWordWrap(True)
    note.setStyleSheet("color: palette(mid);")
    v.addWidget(note)
    btns = QHBoxLayout()
    detect = QPushButton("Detect local models")
    save = QPushButton("Save")

    def show_ollama(res):
        if isinstance(res, Exception):
            ai_status.setText(f"Detection failed: {res}")
            return
        if res["running"]:
            current = model.currentText()
            model.clear()
            model.addItems(res["models"])
            model.setCurrentText(current or (res["models"][0] if res["models"] else ""))
            ai_status.setText(f"Ollama is running with {len(res['models'])} model(s)." if res["models"] else
                              "Ollama is running but has no models. Pull one you choose, e.g. "
                              "`ollama pull llama3.2:3b` (about 2 GB).")
        elif res["installed"]:
            ai_status.setText(f"Ollama is installed but not answering ({res['error']}). Start it: "
                              "`systemctl start ollama` or `ollama serve`.")
        else:
            ai_status.setText("Ollama isn't installed. SAINT still runs its commands without a model; chat and "
                              "planning need one. See https://ollama.com/download/linux.")
    detect.clicked.connect(lambda: in_background(lambda: detect_ollama(base.text()), show_ollama))

    def do_save():
        try:
            save_ai(provider.currentText(), model.currentText(), base.text())
            ai_status.setText("Saved. SAINT picks this up the next time it starts (Service › Restart).")
        except ValueError as e:
            ai_status.setText(str(e))
    save.clicked.connect(do_save)
    btns.addWidget(detect)
    btns.addWidget(save)
    btns.addStretch(1)
    v.addLayout(btns)
    v.addStretch(1)
    stack.addWidget(w)

    # ---------------- Voice
    w, v = page("Voice", "Wake word, speech recognition and the voice all run on this machine.")
    form = QFormLayout()
    mic = QComboBox()
    mic.addItem("System default")
    mic.addItems(microphones())
    if config.get("voice.mic_device"):
        mic.setCurrentText(str(config.get("voice.mic_device")))
    wake = QCheckBox("Listen for “Hey SAINT”")
    wake.setChecked(bool(config.get("voice.wake_word_enabled", True)))
    auto = QCheckBox("Start the microphone when SAINT starts")
    auto.setChecked(bool(config.get("voice.auto_start", True)))
    form.addRow("Microphone", mic)
    form.addRow("", wake)
    form.addRow("", auto)
    v.addLayout(form)
    vstat = QLabel(f"Speech recognition: {config.get('voice.stt_backend', 'faster_whisper')} "
                   f"({config.get('voice.stt_model', 'base.en')}) · Voice: {config.get('voice.tts_backend', 'kokoro')}")
    v.addWidget(vstat)
    vsave = QPushButton("Save")
    vmsg = QLabel("")

    def save_voice():
        config.set("voice.mic_device", None if mic.currentIndex() == 0 else mic.currentText(), persist=False)
        config.set("voice.wake_word_enabled", wake.isChecked(), persist=False)
        config.set("voice.auto_start", auto.isChecked(), persist=True)
        vmsg.setText("Saved. Restart SAINT to use the new microphone.")
    vsave.clicked.connect(save_voice)
    v.addWidget(vsave, 0, Qt.AlignLeft)
    v.addWidget(vmsg)
    if not microphones():
        v.addWidget(QLabel("No microphone found (PipeWire reports no input devices)."))
    v.addStretch(1)
    stack.addWidget(w)

    # ---------------- Permissions
    w, v = page("Permissions", "Every action SAINT takes has a risk level; this decides which ones ask first.")
    group = QButtonGroup(w)
    current = config.get("automation.permission_mode", "confirm")
    pmsg = QLabel("")
    for key, name, desc in PERMISSION_MODES:
        rb = QRadioButton(f"{name} — {desc}")
        rb.setChecked(key == current)
        rb.toggled.connect(lambda on, k=key: on and (set_permission_mode(k), pmsg.setText(f"Mode: {k}. Saved.")))
        group.addButton(rb)
        v.addWidget(rb)
    v.addWidget(pmsg)
    v.addWidget(QLabel("Moving files to the trash, uninstalling, power and force-quit always ask, in every mode."))
    v.addStretch(1)
    stack.addWidget(w)

    # ---------------- Capabilities
    w, v = page("Capabilities", "What this desktop lets SAINT do. Missing pieces say which package adds them.")
    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels(["Capability", "Status", "Provider / how to enable"])
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setVisible(False)

    def fill_caps():
        from core.platform_caps import capabilities
        caps = capabilities()
        table.setRowCount(len(caps))
        for i, c in enumerate(caps):
            table.setItem(i, 0, QTableWidgetItem(c.name.replace("_", " ")))
            table.setItem(i, 1, QTableWidgetItem("available" if c.available else "missing"))
            table.setItem(i, 2, QTableWidgetItem(c.provider if c.available else c.hint))
        table.resizeColumnsToContents()
    fill_caps()
    v.addWidget(table, 1)
    re_btn = QPushButton("Check again")
    re_btn.clicked.connect(fill_caps)
    v.addWidget(re_btn, 0, Qt.AlignLeft)
    stack.addWidget(w)

    # ---------------- Data
    w, v = page("Data", "Everything SAINT knows about you stays in these folders on this machine.")
    form = QFormLayout()
    for label, path in data_locations().items():
        rowb = QHBoxLayout()
        rowb.addWidget(QLabel(path), 1)
        b = QPushButton("Open")
        b.clicked.connect(lambda _=False, p=path: open_path(p) if os.path.exists(p) else None)
        rowb.addWidget(b)
        holder = QWidget()
        holder.setLayout(rowb)
        form.addRow(label, holder)
    v.addLayout(form)
    v.addWidget(QLabel("Secrets (Spotify login, Link key) are kept in KWallet through the Secret Service."))
    v.addStretch(1)
    stack.addWidget(w)

    # ---------------- Diagnostics
    w, v = page("Diagnostics", "Read-only checks. The report folder contains no secrets or memory contents.")
    out = QPlainTextEdit()
    out.setReadOnly(True)
    drow = QHBoxLayout()
    st_btn = QPushButton("Run SAINT self-test")
    dg_btn = QPushButton("Collect diagnostics folder")

    def show_selftest(rep):
        if isinstance(rep, Exception):
            out.setPlainText(f"Self-test failed to run: {rep}")
            return
        lines = [f"Self-test: {'PASSED' if rep.get('passed') else 'FAILED'}"]
        for c in rep.get("checks", []):
            lines.append(f"  {'ok  ' if c.get('ok') else 'FAIL'}  {c.get('name')}: {str(c.get('detail'))[:200]}")
        out.setPlainText("\n".join(lines))
    st_btn.clicked.connect(lambda: (out.setPlainText("Running (up to a minute)…"),
                                    in_background(run_selftest, show_selftest)))

    def collect():
        exe = shutil.which("saintos-diagnose")
        if not exe:
            return "saintos-diagnose isn't installed (it ships with SAINTOS)."
        code, text = _run([exe], timeout=300)
        return text.strip()
    dg_btn.clicked.connect(lambda: (out.setPlainText("Collecting…"),
                                    in_background(collect, lambda r: out.setPlainText(
                                        f"Diagnostics written to:\n{r}" if isinstance(r, str) else str(r)))))
    drow.addWidget(st_btn)
    drow.addWidget(dg_btn)
    drow.addStretch(1)
    v.addLayout(drow)
    v.addWidget(out, 1)
    stack.addWidget(w)

    # ---------------- Updates
    w, v = page("Updates", "Halo updates come from Arch Linux's repositories and the AUR (pacman / paru)."
                if is_halo() else "SAINTOS updates come from Fedora's repositories (dnf / Discover).")
    bi = build_info()
    txt = QLabel("\n".join(f"{k}: {bi[k]}" for k in ("halo_version", "arch_snapshot", "saintos_version",
                                                     "fedora_release", "source_commit", "built_at") if k in bi)
                 or f"No {product()} build information (running from a source checkout).")
    v.addWidget(txt)
    ubtn = QPushButton("Update now (opens a terminal)")
    umsg = QLabel("")

    def run_update():
        cmd = update_command()
        if not shutil.which(cmd[0]):
            umsg.setText(f"Konsole isn't installed; run `{cmd[-1].split(';')[0]}` in a terminal.")
            return
        subprocess.Popen(cmd, start_new_session=True)
        umsg.setText("Updating in the terminal window.")
    ubtn.clicked.connect(run_update)
    v.addWidget(ubtn, 0, Qt.AlignLeft)
    v.addWidget(umsg)
    v.addWidget(QLabel(f"SAINT itself (/opt/saint) is updated by installing a newer {product()} build; an in-place "
                       "SAINT updater is planned."))
    v.addStretch(1)
    stack.addWidget(w)

    nav.currentRowChanged.connect(stack.setCurrentIndex)
    nav.currentRowChanged.connect(lambda i: PAGES[i] == "Service" and refresh_service())
    nav.setCurrentRow(PAGES.index(start_page) if start_page in PAGES else 0)
    win._stack, win._nav = stack, nav            # for tests
    return win


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SAINTOS Control Center")
    ap.add_argument("--welcome", action="store_true", help="open on the Welcome page (first login)")
    ap.add_argument("--page", default="", help=f"open on one page: {', '.join(PAGES)}")
    args = ap.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(f"{product()} Control Center")
    app.setDesktopFileName("saintos-control")
    try:
        from core.config import config
        from ui.theme import build_stylesheet
        a = config.get("appearance", {}) or {}
        font = "Noto Sans" if os.name != "nt" else "Segoe UI"
        app.setStyleSheet(build_stylesheet(a.get("theme", "Dark"), a.get("accent", "#feaa34"), font))
    except Exception as e:                          # the window still works with Plasma's own style
        print(f"saintos-control: SAINT's theme didn't load ({e}); using the system style", file=sys.stderr)
    win = build_window("Welcome" if args.welcome else (args.page.title() if args.page else "Welcome"))
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
