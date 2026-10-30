"""Phone link (modules/phone, KDE Connect) and app installs by voice (modules/apps): devices, contacts from the
synced vCards, the exact kdeconnect-cli commands, and that both register as tools."""

import pytest

from modules.phone import kdeconnect as kc

CLI = "a1b2c3d4e5f6 Pixel 8\n\nbadid! Something\n"


def run_with(out):
    return lambda argv, **kw: type("R", (), {"stdout": out, "returncode": 0})()


def test_devices_and_pick():
    devs = kc.devices(run_with(CLI))
    assert [(d.id, d.name) for d in devs] == [("a1b2c3d4e5f6", "Pixel 8")]
    assert kc.pick_device(devs, "pixel").id == "a1b2c3d4e5f6" and kc.pick_device(devs, "iphone") is None
    assert kc.pick_device([], "") is None


def test_contacts_from_synced_vcards(tmp_path):
    d = tmp_path / "kdeconnect-a1b2c3d4e5f6"
    d.mkdir()
    (d / "1.vcf").write_text("BEGIN:VCARD\nVERSION:2.1\nFN:Mom\nTEL;CELL:+1 555 0100\nEND:VCARD\n", encoding="utf-8")
    (d / "2.vcf").write_text("BEGIN:VCARD\nFN:Gian Rossi\nTEL:+39 333 1234567\nTEL:+39 06 1234\nEND:VCARD\n",
                             encoding="utf-8")
    (d / "3.vcf").write_text("BEGIN:VCARD\nFN:No Number\nEND:VCARD\n", encoding="utf-8")
    book = kc.contacts("a1b2c3d4e5f6", root=tmp_path)
    assert book == {"Mom": ["+1 555 0100"], "Gian Rossi": ["+39 333 1234567", "+39 06 1234"]}
    assert kc.find_contact("mom", book) == ["Mom"]
    assert kc.find_contact("gian", book) == ["Gian Rossi"]
    assert kc.find_contact("dad", book) == []


def test_send_text_command_is_checked():
    assert kc.send_text_argv("a1b2c3d4e5f6", "+1 555 0100", "running   late\n") == [
        "kdeconnect-cli", "--device", "a1b2c3d4e5f6", "--send-sms", "running late", "--destination", "+1 555 0100"]
    for dev, num, msg in (("a1b2c3d4e5f6", "--help", "x"), ("bad id", "+15550100", "x"),
                          ("a1b2c3d4e5f6", "+15550100", " "), ("a1b2c3d4e5f6", "+15550100", "x" * 1001)):
        with pytest.raises(ValueError):
            kc.send_text_argv(dev, num, msg)


def test_phone_and_app_tools_register_and_texting_needs_a_yes():
    from modules.automation.tools import PermissionLevel
    from modules.apps.tools import register_app_tools
    from modules.phone.tools import register_phone_tools

    class Reg:
        def __init__(self):
            self.tools = {}

        def register(self, t):
            self.tools[t.name] = t
    reg = Reg()
    register_phone_tools(reg)
    register_app_tools(reg)
    assert reg.tools["phone.send_text"].permission == PermissionLevel.HIGH
    assert reg.tools["apps.install"].permission == PermissionLevel.HIGH
    assert reg.tools["phone.find"].permission == PermissionLevel.LOW
