"""core/instance.py: a second `saint` (taskbar pin, overlay hotkey, menu) hands its request to the running
SAINT. Round trip over the real local socket / named pipe; only the closed command set gets through."""

import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from core import instance

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def app():
    # A QApplication (not a bare QCoreApplication): later tests in the same process build widgets.
    from PySide6.QtWidgets import QApplication
    return QCoreApplication.instance() or QApplication([])


def test_command_set_is_closed():
    for ok in ("show", "toggle-overlay", "quit", "page:Settings", "page:Store"):
        assert instance.COMMANDS.match(ok), ok
    for bad in ("", "rm -rf ~", "page:", "page:../x", "show; quit", "page:" + "x" * 40, "TOGGLE-OVERLAY"):
        assert not instance.COMMANDS.match(bad), bad
    with pytest.raises(ValueError):
        instance.send("open /etc/passwd")


def test_round_trip_to_the_running_saint(app, monkeypatch):
    """The second `saint` is another process (as from the taskbar pin or the hotkey)."""
    name = "saint-test-instance"
    monkeypatch.setattr(instance, "server_name", lambda: name)
    srv = instance.Server()
    assert srv.listening
    got = []
    srv.command.connect(got.append)
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); from PySide6.QtCore import QCoreApplication; "
            "a = QCoreApplication([]); from core import instance; "
            f"instance.server_name = lambda: {name!r}; "
            "print([instance.send(c) for c in ('toggle-overlay', 'page:Settings', 'show')])")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: proc.poll() is not None and loop.quit())
    timer.start(20)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    out = proc.communicate(timeout=5)[0].strip()
    srv.close()
    assert out == "[True, True, True]"
    assert got == ["toggle-overlay", "page:Settings", "show"]


def test_nobody_running_means_start_normally(app, monkeypatch):
    monkeypatch.setattr(instance, "server_name", lambda: "saint-test-nobody-here")
    assert instance.send("show", timeout_ms=300) is False


def test_ask_carries_one_line_of_text():
    assert instance.COMMANDS.match("ask:what's the weather tomorrow?")
    for bad in ("ask:", "ask:two\nlines", "ask:" + "x" * 501):
        assert not instance.COMMANDS.match(bad), bad
