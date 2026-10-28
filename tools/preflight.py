"""
tools/preflight.py

Read-only hardware and storage report for planning a SAINTOS (Fedora KDE) install
next to Windows, and for checking what SAINT can use on this machine.

    python tools/preflight.py                  # Markdown report on stdout
    python tools/preflight.py --json out.json  # also write the raw findings

It only *reads*: /sys, /proc, and listing commands (lsblk, lspci, nvidia-smi,
mokutil --sb-state, wpctl status, Get-CimInstance, Get-Partition, ...). Every
command it may run is in ``_READ_ONLY_COMMANDS`` and anything else is refused,
so it can never format, partition, or touch a bootloader. A value it can't
determine is reported as "unknown" with the reason — never guessed.
"""

import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

# The only programs this tool may start, and nothing in their argument lists writes.
_READ_ONLY_COMMANDS = {
    "lsblk", "lspci", "nvidia-smi", "mokutil", "wpctl", "pactl", "loginctl", "uname",
    "powershell", "rocm-smi", "ollama",
}
# Never allowed, whatever the list above says (a guard against future edits).
_FORBIDDEN_WORDS = {"mkfs", "parted", "fdisk", "sfdisk", "gdisk", "wipefs", "dd", "diskpart", "format",
                    "bcdedit", "efibootmgr", "grub2-install", "grub-install", "manage-bde", "Resize-Partition",
                    "Remove-Partition", "New-Partition", "Clear-Disk", "Initialize-Disk", "Set-Partition"}


def unknown(reason: str) -> Dict[str, str]:
    return {"unknown": reason}


def _run(args: List[str], timeout: float = 15.0) -> Optional[str]:
    """stdout of a read-only command, or None when it's missing or fails."""
    if not args or os.path.basename(args[0]).lower().removesuffix(".exe") not in _READ_ONLY_COMMANDS:
        raise PermissionError(f"preflight refuses to run {args[:1]}")
    joined = " ".join(args)
    tokens = {t.lower() for t in joined.replace("|", " ").replace(";", " ").split()}
    if tokens & {w.lower() for w in _FORBIDDEN_WORDS}:
        raise PermissionError(f"preflight refuses a command that could change the disk: {joined}")
    if shutil.which(args[0]) is None:
        return None
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def _ps(script: str) -> Optional[Any]:
    """A read-only PowerShell query, parsed from JSON."""
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"{script} | ConvertTo-Json -Depth 4 -Compress"], timeout=30)
    if not out or not out.strip():
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def _as_list(v) -> list:
    return v if isinstance(v, list) else ([v] if v else [])


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #
def linux_machine() -> Dict[str, Any]:
    vendor, model = _read("/sys/class/dmi/id/sys_vendor"), _read("/sys/class/dmi/id/product_name")
    return {"vendor": vendor or unknown("/sys/class/dmi/id not readable"),
            "model": model or unknown("/sys/class/dmi/id not readable"),
            "kernel": platform.release()}


def linux_os() -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    for line in (_read("/etc/os-release") or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v.strip('"')
    session = os.environ.get("XDG_SESSION_TYPE") or unknown("XDG_SESSION_TYPE not set (not in a desktop session?)")
    return {"name": info.get("PRETTY_NAME") or unknown("/etc/os-release missing"),
            "version_id": info.get("VERSION_ID", ""), "variant": info.get("VARIANT_ID", ""),
            "desktop": os.environ.get("XDG_CURRENT_DESKTOP") or unknown("XDG_CURRENT_DESKTOP not set"),
            "session_type": session}


def linux_cpu_ram() -> Dict[str, Any]:
    cpu = next((l.split(":", 1)[1].strip() for l in (_read("/proc/cpuinfo") or "").splitlines()
                if l.startswith("model name")), None)
    mem_kb = next((int(l.split()[1]) for l in (_read("/proc/meminfo") or "").splitlines()
                   if l.startswith("MemTotal:")), None)
    return {"cpu": cpu or unknown("/proc/cpuinfo has no model name"), "threads": os.cpu_count(),
            "ram_gb": round(mem_kb / 1024 / 1024, 1) if mem_kb else unknown("/proc/meminfo unreadable")}


def linux_gpu() -> Dict[str, Any]:
    out = _run(["lspci", "-mm"])
    gpus = [l for l in (out or "").splitlines() if any(k in l for k in ("VGA", "3D controller", "Display"))]
    return {"adapters": gpus or unknown("lspci missing or found no display adapter"), **vram()}


def linux_storage() -> Dict[str, Any]:
    out = _run(["lsblk", "-J", "-b", "-o",
                "NAME,SIZE,TYPE,FSTYPE,FSAVAIL,LABEL,PARTLABEL,PARTTYPENAME,MOUNTPOINTS,TRAN,RM,MODEL"])
    if not out:
        return {"devices": unknown("lsblk unavailable")}
    try:
        devices = json.loads(out).get("blockdevices", [])
    except ValueError:
        return {"devices": unknown("lsblk output was not JSON")}
    bitlocker = []

    def walk(devs):
        for d in devs:
            if (d.get("fstype") or "").lower() == "bitlocker":
                bitlocker.append(d.get("name"))
            walk(d.get("children") or [])
    walk(devices)
    return {"devices": devices, "bitlocker_partitions": bitlocker}


def linux_boot() -> Dict[str, Any]:
    uefi = os.path.isdir("/sys/firmware/efi")
    sb = _run(["mokutil", "--sb-state"])
    return {"mode": "UEFI" if uefi else "legacy BIOS (or EFI vars not exposed)",
            "secure_boot": sb.strip() if sb else unknown("mokutil not installed (dnf install mokutil)")}


def linux_hardware() -> Dict[str, Any]:
    wifi = [os.path.basename(os.path.dirname(p)) for p in glob.glob("/sys/class/net/*/wireless")]
    devices = _read("/proc/bus/input/devices") or ""
    audio = _run(["wpctl", "status"])
    displays = {}
    for path in glob.glob("/sys/class/drm/card*-*/status"):
        displays[os.path.basename(os.path.dirname(path))] = _read(path)
    return {
        "wifi": wifi or unknown("no wireless interface in /sys/class/net (missing driver/firmware?)"),
        "bluetooth": sorted(os.listdir("/sys/class/bluetooth")) if os.path.isdir("/sys/class/bluetooth")
        else unknown("no /sys/class/bluetooth"),
        "audio": "PipeWire responds (wpctl status)" if audio else unknown("wpctl failed or PipeWire not running"),
        "webcam": sorted(glob.glob("/dev/video*")) or unknown("no /dev/video* device"),
        "touchpad": "present" if "touchpad" in devices.lower() else unknown("no input device named touchpad"),
        "backlight": sorted(os.listdir("/sys/class/backlight")) if os.path.isdir("/sys/class/backlight")
        else unknown("no /sys/class/backlight"),
        "suspend_states": _read("/sys/power/state") or unknown("/sys/power/state unreadable"),
        "suspend_tested": unknown("needs a manual suspend/resume test"),
        "displays": displays or unknown("no DRM connectors found"),
    }


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
def windows_machine() -> Dict[str, Any]:
    cs = _ps("Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory") or {}
    return {"vendor": cs.get("Manufacturer") or unknown("Win32_ComputerSystem query failed"),
            "model": cs.get("Model") or unknown("Win32_ComputerSystem query failed"),
            "kernel": platform.version()}


def windows_os() -> Dict[str, Any]:
    return {"name": f"Windows {platform.release()}", "version_id": platform.version(), "variant": "",
            "desktop": "Windows", "session_type": "windows"}


def windows_cpu_ram() -> Dict[str, Any]:
    cpu = _ps("Get-CimInstance Win32_Processor | Select-Object -First 1 Name") or {}
    cs = _ps("Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory") or {}
    ram = cs.get("TotalPhysicalMemory")
    return {"cpu": cpu.get("Name") or unknown("Win32_Processor query failed"), "threads": os.cpu_count(),
            "ram_gb": round(int(ram) / 1024 ** 3, 1) if ram else unknown("Win32_ComputerSystem query failed")}


def windows_gpu() -> Dict[str, Any]:
    gpus = _as_list(_ps("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion"))
    return {"adapters": [g.get("Name") for g in gpus] or unknown("Win32_VideoController query failed"), **vram()}


def windows_storage() -> Dict[str, Any]:
    disks = _as_list(_ps("Get-Disk | Select-Object Number,FriendlyName,Size,PartitionStyle,BusType"))
    parts = _as_list(_ps("Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,Size,Type,"
                         "GptType,IsSystem,IsBoot,IsHidden"))
    vols = _as_list(_ps("Get-Volume | Select-Object DriveLetter,FileSystemLabel,FileSystem,Size,SizeRemaining"))
    # BitLocker status needs an administrator prompt (manage-bde / Get-BitLockerVolume); only the
    # unprivileged hint from the shell's protection status is read here.
    bl = _ps("(New-Object -ComObject Shell.Application).NameSpace('C:').Self.ExtendedProperty("
             "'System.Volume.BitLockerProtection') | ForEach-Object { @{C=$_} }")
    status = {1: "on", 2: "off", 3: "encrypting", 4: "decrypting", 5: "suspended", 6: "on (locked)"}
    return {"disks": disks or unknown("Get-Disk failed"), "partitions": parts or unknown("Get-Partition failed"),
            "volumes": vols or unknown("Get-Volume failed"),
            "bitlocker_c": status.get((bl or {}).get("C"), unknown("not readable without admin; "
                                                                    "check Settings > Privacy & security > "
                                                                    "Device encryption"))}


def windows_boot() -> Dict[str, Any]:
    fw = os.environ.get("firmware_type")
    return {"mode": fw or unknown("firmware_type not set; check msinfo32 > BIOS Mode"),
            "secure_boot": unknown("Confirm-SecureBootUEFI needs an admin shell; check msinfo32 > "
                                   "Secure Boot State")}


def windows_hardware() -> Dict[str, Any]:
    note = unknown("tested from Fedora: boot it and run this tool there")
    return {k: note for k in ("wifi", "bluetooth", "audio", "webcam", "touchpad", "backlight",
                              "suspend_states", "suspend_tested", "displays")}


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #
def vram() -> Dict[str, Any]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    if out:
        rows = [r.split(", ") for r in out.strip().splitlines()]
        return {"nvidia": [{"name": r[0], "vram_mb": int(r[1]), "driver": r[2]} for r in rows if len(r) >= 3]}
    if _run(["rocm-smi", "--showmeminfo", "vram"]):
        return {"amd_rocm": "rocm-smi responds"}
    return {"vram": unknown("no nvidia-smi / rocm-smi; integrated or unsupported GPU, or driver not installed")}


def saint_runtime() -> Dict[str, Any]:
    """What SAINT itself will find: Python, Ollama, free space for models."""
    home = os.path.expanduser("~")
    free = shutil.disk_usage(home).free
    ollama = _run(["ollama", "--version"])
    return {"python": sys.version.split()[0], "free_space_home_gb": round(free / 1024 ** 3, 1),
            "ollama": ollama.strip() if ollama else unknown("ollama not installed or not on PATH")}


def collect() -> Dict[str, Any]:
    if os.name == "nt":
        probes = [("machine", windows_machine), ("os", windows_os), ("cpu_ram", windows_cpu_ram),
                  ("gpu", windows_gpu), ("storage", windows_storage), ("boot", windows_boot),
                  ("hardware", windows_hardware)]
    else:
        probes = [("machine", linux_machine), ("os", linux_os), ("cpu_ram", linux_cpu_ram),
                  ("gpu", linux_gpu), ("storage", linux_storage), ("boot", linux_boot),
                  ("hardware", linux_hardware)]
    probes.append(("saint", saint_runtime))
    report: Dict[str, Any] = {"platform": sys.platform}
    for name, fn in probes:
        try:
            report[name] = fn()
        except PermissionError:
            raise
        except Exception as e:                       # one broken probe must not hide the rest
            report[name] = unknown(f"{type(e).__name__}: {e}")
    return report


def _fmt(v: Any) -> str:
    if isinstance(v, dict) and set(v) == {"unknown"}:
        return f"**unknown** — {v['unknown']}"
    if isinstance(v, (list, dict)):
        return "`" + json.dumps(v, default=str)[:400] + "`"
    return str(v)


def to_markdown(report: Dict[str, Any]) -> str:
    lines = ["# SAINTOS preflight report", "", "Read-only. Nothing on this machine was changed.", ""]
    for section, values in report.items():
        if not isinstance(values, dict):
            continue
        lines += [f"## {section}", "", "| Item | Value |", "|---|---|"]
        if set(values) == {"unknown"}:
            lines.append(f"| (all) | {_fmt(values)} |")
        else:
            for k, v in values.items():
                if k in ("devices", "disks", "partitions", "volumes"):
                    lines.append(f"| {k} | see JSON (`--json`) |")
                else:
                    lines.append(f"| {k} | {_fmt(v)} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--json", metavar="PATH", help="write the full findings as JSON")
    args = ap.parse_args(argv)
    report = collect()
    try:
        sys.stdout.reconfigure(encoding="utf-8")          # Windows consoles default to cp1252
    except (AttributeError, ValueError):
        pass
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    sys.stdout.write(to_markdown(report) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
