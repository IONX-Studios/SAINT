"""
core/recovery.py

Halo's Recovery page (deterministic; every command is a fixed argument list):

    firmware_setup_supported()        can this PC restart straight into its UEFI settings?
    REBOOT_TO_FIRMWARE                systemctl reboot --firmware-setup
    boot_entries()                    the PC's UEFI boot entries (efibootmgr): Windows Boot Manager, Halo, ...
    boot_once(entry)                  next restart only: start that entry (pkexec efibootmgr --bootnext), then
                                      the PC goes back to its normal order (dual boot: "Restart into Windows")
    snapshots() / snapshot_now()      Btrfs snapshots of the system (snapper; one is taken around every
                                      package install by snap-pac); booting an older one is in the GRUB menu
                                      ("Halo snapshots", grub-btrfs)
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

EFIVARS = Path("/sys/firmware/efi/efivars")
OS_INDICATIONS_SUPPORTED = "OsIndicationsSupported-8be4df61-93ca-11d2-aa0d-00e098032b8c"
REBOOT_TO_FIRMWARE = ["systemctl", "reboot", "--firmware-setup"]
REBOOT = ["systemctl", "reboot"]
_ENTRY = re.compile(r"^Boot([0-9A-Fa-f]{4})(\*?)\s+(.+?)(?:\t|\s{2,}|$)")


def is_uefi(efivars: Path = EFIVARS) -> bool:
    return efivars.is_dir()


def firmware_setup_supported(efivars: Path = EFIVARS) -> bool:
    """UEFI firmware that accepts "boot to setup next time" (bit 0 of OsIndicationsSupported)."""
    try:
        data = (efivars / OS_INDICATIONS_SUPPORTED).read_bytes()
    except OSError:
        return False
    return len(data) >= 5 and bool(data[4] & 1)        # 4 bytes of attributes, then the 64-bit value


@dataclass
class BootEntry:
    num: str
    label: str
    active: bool
    current: bool = False

    @property
    def is_windows(self) -> bool:
        return "windows" in self.label.lower()


def boot_entries(run: Callable = subprocess.run) -> List[BootEntry]:
    try:
        out = run(["efibootmgr"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    current = re.search(r"^BootCurrent:\s*([0-9A-Fa-f]{4})", out, re.M)
    entries = []
    for line in out.splitlines():
        m = _ENTRY.match(line)
        if m:
            entries.append(BootEntry(m[1].upper(), m[3].strip(), m[2] == "*",
                                     bool(current) and current[1].upper() == m[1].upper()))
    return entries


def boot_once(entry: BootEntry, then_restart: bool = True) -> List[List[str]]:
    """Commands: start ``entry`` on the next restart only (the normal order comes back after that)."""
    if not re.fullmatch(r"[0-9A-F]{4}", entry.num):
        raise ValueError(f"not a boot entry number: {entry.num!r}")
    cmds = [["pkexec", "efibootmgr", "--bootnext", entry.num]]
    if then_restart:
        cmds.append(REBOOT)
    return cmds


@dataclass
class Snapshot:
    number: int
    date: str
    description: str


def snapshots(run: Callable = subprocess.run) -> Optional[List[Snapshot]]:
    """The system's snapshots, newest first; None when snapper isn't set up (not Btrfs, or not installed)."""
    try:
        res = run(["snapper", "--csvout", "-c", "root", "list", "--columns", "number,date,description"],
                  capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    rows = [l.split(",", 2) for l in res.stdout.splitlines()[1:] if l.strip()]
    snaps = [Snapshot(int(n), d, desc) for n, d, desc in rows if n.isdigit() and n != "0"]
    return sorted(snaps, key=lambda s: -s.number)


def snapshot_now(description: str) -> List[str]:
    desc = re.sub(r"[^\w .,:()'-]", "", description)[:80] or "SAINT snapshot"
    return ["pkexec", "snapper", "-c", "root", "create", "--cleanup-algorithm", "number", "--description", desc]
