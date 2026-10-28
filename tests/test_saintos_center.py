"""SAINTOS Control Center (ui/saintos_center.py): its logic, and that every page builds (offscreen)."""

import os

import pytest

from core.config import config
from ui import saintos_center as sc


def _fake_run(responses):
    calls = []

    def run(args, timeout=15):
        calls.append(args)
        key = " ".join(args[2:4]) if args[0] == "systemctl" else args[0]
        return responses.get(key, (0, ""))
    return run, calls


def test_service_status_parsing():
    run, calls = _fake_run({"is-active saint.service": (3, "inactive\n"),
                            "is-enabled saint.service": (1, "disabled\n")})
    st = sc.service_status(run=run)
    assert st == {"active": "inactive", "enabled": "disabled", "available": "yes"}
    assert all(c[:2] == ["systemctl", "--user"] for c in calls)      # never the system manager


def test_service_actions_are_user_level_and_bounded():
    run, calls = _fake_run({})
    ok, _ = sc.service_action("enable", run=run)
    assert ok and calls[-1] == ["systemctl", "--user", "enable", "--now", "saint.service"]
    with pytest.raises(ValueError):
        sc.service_action("mask; rm -rf ~", run=run)


def test_permission_mode_uses_the_same_key_as_saint():
    old = config.get("automation.permission_mode")
    try:
        sc.set_permission_mode("safe")
        from core.permissions import permission_manager
        assert permission_manager.default_policy("medium") == "deny"
        with pytest.raises(ValueError):
            sc.set_permission_mode("root")
    finally:
        config.set("automation.permission_mode", old)


def test_save_ai_validates():
    old = {k: config.get(k) for k in ("ai.provider", "ai.model", "ai.base_url")}
    try:
        with pytest.raises(ValueError):
            sc.save_ai("mock", "x", "")
        with pytest.raises(ValueError):
            sc.save_ai("ollama", "  ", "")
        sc.save_ai("ollama", "llama3.2:3b", "")
        assert config.get("ai.model") == "llama3.2:3b" and config.get("ai.base_url") == "http://localhost:11434"
    finally:
        for k, v in old.items():
            config.set(k, v)


def test_detect_ollama_when_nothing_listens():
    res = sc.detect_ollama("http://127.0.0.1:9")        # discard port: refused immediately
    assert res["running"] is False and res["models"] == []


def test_data_locations_are_the_test_data_dir():
    locs = sc.data_locations()
    assert locs["Settings, memory, history"] == os.environ["SAINT_DATA_DIR"]


def test_every_page_builds_offscreen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    win = sc.build_window("Capabilities")
    assert win._stack.count() == len(sc.PAGES)
    assert sc.PAGES[win._stack.currentIndex()] == "Capabilities"
    for i in range(len(sc.PAGES)):
        win._nav.setCurrentRow(i)
        app.processEvents()
    win.close()
