"""core/recovery.py: firmware setup support, one-time boot into another system, snapshots."""

from core import recovery

EFIBOOTMGR = """BootCurrent: 0002
Timeout: 1 seconds
BootOrder: 0002,0000,0001
Boot0000* Windows Boot Manager	HD(1,GPT,...)/File(\EFI\Microsoft\Boot\bootmgfw.efi)
Boot0001  UEFI: PXE IPv4	PciRoot(0x0)
Boot0002* Halo	HD(1,GPT,...)/File(\EFI\Halo\grubx64.efi)
"""


def run_with(out, code=0):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return type("R", (), {"stdout": out, "returncode": code})()
    run.calls = calls
    return run


def test_boot_entries_and_restart_into_windows_once():
    entries = recovery.boot_entries(run_with(EFIBOOTMGR))
    assert [(e.num, e.label, e.active, e.current) for e in entries] == [
        ("0000", "Windows Boot Manager", True, False), ("0001", "UEFI: PXE IPv4", False, False), ("0002", "Halo", True, True)]
    win = next(e for e in entries if e.is_windows)
    assert recovery.boot_once(win) == [["pkexec", "efibootmgr", "--bootnext", "0000"], ["systemctl", "reboot"]]
    bad = recovery.BootEntry("00; rm", "x", True)
    try:
        recovery.boot_once(bad)
        assert False
    except ValueError:
        pass


def test_firmware_setup_needs_uefi_support(tmp_path):
    assert not recovery.firmware_setup_supported(tmp_path)
    (tmp_path / recovery.OS_INDICATIONS_SUPPORTED).write_bytes(b"\x06\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00")
    assert recovery.firmware_setup_supported(tmp_path)
    (tmp_path / recovery.OS_INDICATIONS_SUPPORTED).write_bytes(b"\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    assert not recovery.firmware_setup_supported(tmp_path)
    assert recovery.REBOOT_TO_FIRMWARE == ["systemctl", "reboot", "--firmware-setup"]


def test_snapshots_newest_first_or_none():
    out = "number,date,description\n0,,current\n1,2026-10-30 10:00:00,pacman -S blender\n2,2026-10-30 11:00:00,Taken in SAINT\n"
    snaps = recovery.snapshots(run_with(out))
    assert [s.number for s in snaps] == [2, 1] and snaps[0].description == "Taken in SAINT"
    assert recovery.snapshots(run_with("", code=1)) is None
    cmd = recovery.snapshot_now("Before `rm -rf` $(x)")
    assert cmd[:4] == ["pkexec", "snapper", "-c", "root"] and "`" not in cmd[-1] and "$" not in cmd[-1]
