"""
core/sysinfo.py

What SAINT Task Manager shows, read from the system: psutil, /proc, /sys, lspci, nvidia-smi, systemctl and
the XDG autostart folders. Nothing is estimated — a value the hardware or driver doesn't expose is None and
the UI says "not available". Pure functions with injectable readers so tests run anywhere.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Processes that keep the desktop or the system alive: ending them asks twice.
CRITICAL = {"systemd", "init", "kwin_wayland", "kwin_x11", "plasmashell", "sddm", "Xorg", "Xwayland", "dbus-daemon",
            "dbus-broker", "pipewire", "wireplumber", "NetworkManager", "polkitd", "udisksd", "systemd-logind",
            "systemd-journald", "systemd-udevd", "kded6", "ksmserver", "csrss.exe", "wininit.exe", "winlogon.exe",
            "lsass.exe", "services.exe", "smss.exe", "explorer.exe", "dwm.exe"}


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _run(argv: List[str], timeout: float = 4) -> str:
    if not shutil.which(argv[0]):
        return ""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# --------------------------------------------------------------------------- processes
class ProcessSampler:
    """CPU % and disk I/O rates need two samples; keep one sampler and call ``sample()`` each refresh."""

    def __init__(self):
        self._io: Dict[int, tuple] = {}
        self._t = 0.0

    def sample(self) -> List[Dict]:
        import psutil
        now = time.monotonic()
        dt = max(1e-3, now - self._t) if self._t else 0.0
        out, io_now = [], {}
        attrs = ["pid", "name", "username", "status", "cpu_percent", "memory_info", "num_threads", "ppid",
                 "create_time", "cmdline"]
        for p in psutil.process_iter(attrs):
            info = p.info
            read = write = None
            try:
                io = p.io_counters()
                io_now[info["pid"]] = (io.read_bytes, io.write_bytes)
                prev = self._io.get(info["pid"])
                if prev and dt:
                    read, write = (io.read_bytes - prev[0]) / dt, (io.write_bytes - prev[1]) / dt
            except (psutil.Error, AttributeError, OSError):
                pass
            mem = info.get("memory_info")
            name = info.get("name") or "?"
            out.append({"pid": info["pid"], "name": name, "user": info.get("username") or "",
                        "status": info.get("status") or "", "cpu": float(info.get("cpu_percent") or 0.0),
                        "memory": int(mem.rss) if mem else 0, "threads": info.get("num_threads") or 0,
                        "ppid": info.get("ppid") or 0, "started": info.get("create_time") or 0,
                        "command": " ".join(info.get("cmdline") or [])[:400],
                        "disk_read": read, "disk_write": write,
                        "critical": name in CRITICAL or info["pid"] in (0, 1)})
        self._io, self._t = io_now, now
        return out


def filter_processes(procs: List[Dict], query: str = "", user: str = "", sort: str = "cpu",
                     descending: bool = True) -> List[Dict]:
    q = query.strip().lower()
    rows = [p for p in procs if (not q or q in p["name"].lower() or q in str(p["pid"]) or q in p["command"].lower())
            and (not user or p["user"].split("\\")[-1] == user)]
    key = {"name": lambda p: p["name"].lower(), "pid": lambda p: p["pid"], "memory": lambda p: p["memory"],
           "cpu": lambda p: p["cpu"], "user": lambda p: p["user"], "disk": lambda p: (p["disk_read"] or 0) +
           (p["disk_write"] or 0)}.get(sort, lambda p: p["cpu"])
    return sorted(rows, key=key, reverse=descending)


def end_process(pid: int, force: bool = False, psutil_mod=None) -> str:
    """Ask a process to quit (SIGTERM), or kill it (SIGKILL). Returns what happened, in words."""
    psutil = psutil_mod or __import__("psutil")
    try:
        p = psutil.Process(pid)
        name = p.name()
        p.kill() if force else p.terminate()
        try:
            p.wait(timeout=3)
            return f"{name} ({pid}) ended"
        except psutil.TimeoutExpired:
            return f"{name} ({pid}) didn't quit yet — use End now to force it"
    except psutil.NoSuchProcess:
        return f"process {pid} had already ended"
    except psutil.AccessDenied:
        return f"not allowed to end process {pid} (it belongs to another user or the system)"


# --------------------------------------------------------------------------- performance
def cpu_info(read=_read) -> Dict:
    import psutil
    model = platform.processor() or ""
    cpuinfo = read("/proc/cpuinfo") or ""
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    freq = None
    try:
        f = psutil.cpu_freq()
        freq = round(f.current) if f else None
    except Exception:
        pass
    return {"model": model or platform.machine(), "cores": psutil.cpu_count(logical=False),
            "threads": psutil.cpu_count(), "freq_mhz": freq, "temp_c": cpu_temp()}


def cpu_temp(sensors: Optional[Callable] = None) -> Optional[float]:
    """Package temperature from the k10temp / coretemp / zenpower / acpitz sensor, if the kernel exposes one."""
    try:
        import psutil
        temps = (sensors or psutil.sensors_temperatures)()
    except Exception:
        return None
    for chip in ("k10temp", "coretemp", "zenpower", "cpu_thermal", "acpitz"):
        for t in temps.get(chip, []) if temps else []:
            if t.current and (not t.label or t.label in ("Tctl", "Tdie", "Package id 0", "") or chip == "acpitz"):
                return round(float(t.current), 1)
    return None


def gpus(run=_run, read=_read, globber=glob.glob) -> List[Dict]:
    """Each GPU with what its driver tells: NVIDIA through nvidia-smi, AMD/Intel through /sys/class/drm."""
    out = []
    smi = run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,driver_version",
               "--format=csv,noheader,nounits"])
    for line in smi.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 6:
            def num(v):
                try:
                    return float(v)
                except ValueError:
                    return None
            out.append({"name": parts[0], "vendor": "NVIDIA", "percent": num(parts[1]),
                        "memory_used": int(num(parts[2]) * 2 ** 20) if num(parts[2]) is not None else None,
                        "memory_total": int(num(parts[3]) * 2 ** 20) if num(parts[3]) is not None else None,
                        "temp_c": num(parts[4]), "driver": f"nvidia {parts[5]}"})
    for card in sorted(globber("/sys/class/drm/card[0-9]")):
        dev = f"{card}/device"
        vendor = (read(f"{dev}/vendor") or "").lower()
        if vendor == "0x10de" and out:
            continue                                     # NVIDIA already reported by nvidia-smi
        if vendor not in ("0x1002", "0x8086", "0x10de"):
            continue
        driver = os.path.basename(os.path.realpath(f"{dev}/driver")) if os.path.exists(f"{dev}/driver") else ""
        busy = read(f"{dev}/gpu_busy_percent")
        used, total = read(f"{dev}/mem_info_vram_used"), read(f"{dev}/mem_info_vram_total")
        temp = None
        for h in globber(f"{dev}/hwmon/hwmon*/temp1_input"):
            t = read(h)
            temp = round(int(t) / 1000, 1) if t and t.isdigit() else None
            break
        out.append({"name": _pci_name(read(f"{dev}/uevent") or "", run) or {"0x1002": "AMD GPU", "0x8086": "Intel GPU",
                                                                            "0x10de": "NVIDIA GPU"}[vendor],
                    "vendor": {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}[vendor],
                    "percent": float(busy) if busy and busy.isdigit() else None,
                    "memory_used": int(used) if used and used.isdigit() else None,
                    "memory_total": int(total) if total and total.isdigit() else None,
                    "temp_c": temp, "driver": driver})
    return out


def _pci_name(uevent: str, run) -> str:
    slot = next((l.split("=", 1)[1] for l in uevent.splitlines() if l.startswith("PCI_SLOT_NAME=")), "")
    if not slot:
        return ""
    line = run(["lspci", "-s", slot]).strip()
    return line.split(": ", 1)[1] if ": " in line else ""


def memory() -> Dict:
    import psutil
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    return {"total": vm.total, "used": vm.total - vm.available, "available": vm.available, "percent": vm.percent,
            "swap_total": sw.total, "swap_used": sw.used, "swap_percent": sw.percent}


class RateSampler:
    """Disk and network throughput between two calls (bytes/s)."""

    def __init__(self):
        self._disk, self._net, self._t = None, None, 0.0

    def sample(self) -> Dict:
        import psutil
        now = time.monotonic()
        dt = now - self._t if self._t else 0.0
        disk = psutil.disk_io_counters(perdisk=True) or {}
        net = psutil.net_io_counters(pernic=True) or {}
        disks, nics = {}, {}
        for name, c in disk.items():
            if name.startswith(("loop", "ram", "zram", "sr", "dm-")):
                continue
            p = (self._disk or {}).get(name)
            disks[name] = {"read": (c.read_bytes - p.read_bytes) / dt if p and dt else None,
                           "write": (c.write_bytes - p.write_bytes) / dt if p and dt else None,
                           "busy": min(100.0, (c.busy_time - p.busy_time) / (dt * 10)) if p and dt and
                           hasattr(c, "busy_time") else None}
        stats = psutil.net_if_stats()
        for name, c in net.items():
            if name == "lo" or name.startswith(("veth", "docker", "br-", "virbr", "Loopback")):
                continue
            p = (self._net or {}).get(name)
            st = stats.get(name)
            nics[name] = {"up": bool(st and st.isup), "speed_mbps": (st.speed or None) if st else None,
                          "recv": (c.bytes_recv - p.bytes_recv) / dt if p and dt else None,
                          "sent": (c.bytes_sent - p.bytes_sent) / dt if p and dt else None}
        self._disk, self._net, self._t = disk, net, now
        return {"disks": disks, "networks": nics}


def disk_health(device: str, run=_run) -> str:
    """SMART overall health when smartctl may read it without a password; otherwise says so."""
    out = run(["smartctl", "-H", "-j", f"/dev/{device}"])
    if not out:
        return "not available (smartctl isn't installed)" if not shutil.which("smartctl") else \
            "needs admin rights to read"
    try:
        import json
        data = json.loads(out)
        passed = data.get("smart_status", {}).get("passed")
        return "good" if passed else "FAILING — back up this disk" if passed is False else "not reported"
    except ValueError:
        return "not reported"


def battery(read=_read, globber=glob.glob) -> Optional[Dict]:
    for bat in sorted(globber("/sys/class/power_supply/BAT*")):
        full = read(f"{bat}/energy_full") or read(f"{bat}/charge_full")
        design = read(f"{bat}/energy_full_design") or read(f"{bat}/charge_full_design")
        cap = read(f"{bat}/capacity")
        health = round(100 * int(full) / int(design), 1) if full and design and design.isdigit() and int(design) else None
        return {"percent": int(cap) if cap and cap.isdigit() else None, "status": read(f"{bat}/status") or "",
                "health_percent": health, "cycles": read(f"{bat}/cycle_count")}
    return None


def system(read=_read) -> Dict:
    import psutil
    osr = {}
    for line in (read("/etc/os-release") or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            osr[k] = v.strip('"')
    boot = psutil.boot_time()
    dmi = {k: read(f"/sys/class/dmi/id/{k}") for k in ("sys_vendor", "product_name", "board_name", "bios_version")}
    return {"os": osr.get("PRETTY_NAME") or f"{platform.system()} {platform.release()}",
            "kernel": platform.release(), "hostname": platform.node(), "boot_time": boot,
            "uptime_s": time.time() - boot, "machine": " ".join(v for v in (dmi["sys_vendor"], dmi["product_name"]) if v),
            "board": dmi["board_name"], "bios": dmi["bios_version"], "arch": platform.machine()}


# --------------------------------------------------------------------------- startup, services, users
def _desktop_entry(path: str, read=_read) -> Dict:
    fields = {}
    in_entry = False
    for line in (read(path) or "").splitlines():
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
        elif in_entry and "=" in line:
            k, v = line.split("=", 1)
            fields.setdefault(k.strip(), v.strip())
    return fields


def autostart_entries(user_dir: Optional[str] = None, system_dirs=("/etc/xdg/autostart",), read=_read,
                      listdir=os.listdir) -> List[Dict]:
    """XDG autostart apps. A user file with the same name overrides the system one (that's how disabling
    a system entry works: Hidden=true in ~/.config/autostart)."""
    user_dir = user_dir or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "autostart")
    entries: Dict[str, Dict] = {}
    for d, scope in [(s, "system") for s in system_dirs] + [(user_dir, "user")]:
        try:
            names = [n for n in listdir(d) if n.endswith(".desktop")]
        except OSError:
            continue
        for n in names:
            f = _desktop_entry(os.path.join(d, n), read)
            if not f:
                continue
            enabled = f.get("Hidden", "false").lower() != "true" and \
                f.get("X-GNOME-Autostart-enabled", "true").lower() != "false"
            only = f.get("OnlyShowIn", "")
            if only and "KDE" not in only.split(";"):
                continue
            prev = entries.get(n, {})
            entries[n] = {"file": n, "name": f.get("Name") or prev.get("name") or n[:-8], "command": f.get("Exec") or
                          prev.get("command", ""), "enabled": enabled, "scope": scope if not prev else "system+user",
                          "path": os.path.join(d, n)}
    return sorted(entries.values(), key=lambda e: e["name"].lower())


def set_autostart(entry: Dict, enabled: bool, user_dir: Optional[str] = None) -> str:
    """Turn an autostart app on/off for this user only: write ~/.config/autostart/<file> with Hidden=."""
    user_dir = user_dir or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "autostart")
    os.makedirs(user_dir, exist_ok=True)
    target = os.path.join(user_dir, entry["file"])
    src = entry["path"]
    lines = (_read(src) or "[Desktop Entry]\nType=Application").splitlines()
    lines = [l for l in lines if not l.startswith(("Hidden=", "X-GNOME-Autostart-enabled="))]
    idx = next((i for i, l in enumerate(lines) if l.strip() == "[Desktop Entry]"), 0)
    lines.insert(idx + 1, f"Hidden={'false' if enabled else 'true'}")
    Path(target).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"{entry['name']} will {'start' if enabled else 'no longer start'} when you sign in"


def services(user: bool = False, run=_run) -> List[Dict]:
    """systemd services: name, description, load/active/sub state, enabled at boot."""
    scope = ["--user"] if user else []
    units = run(["systemctl", *scope, "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"])
    files = run(["systemctl", *scope, "list-unit-files", "--type=service", "--no-legend", "--no-pager"])
    enabled = {}
    for line in files.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            enabled[parts[0]] = parts[1]
    out = []
    for line in units.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4 and parts[0].endswith(".service"):
            out.append({"name": parts[0], "load": parts[1], "active": parts[2], "sub": parts[3],
                        "description": parts[4] if len(parts) > 4 else "",
                        "enabled": enabled.get(parts[0], "")})
    return out


SERVICE_ACTIONS = ("start", "stop", "restart", "enable", "disable")


def service_argv(name: str, action: str, user: bool) -> List[str]:
    """The command for a service action. System services go through pkexec (the password prompt)."""
    if action not in SERVICE_ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if not name.endswith(".service") or not all(c.isalnum() or c in "@._-\\:" for c in name):
        raise ValueError(f"unsafe service name {name!r}")
    return ["systemctl", "--user", action, name] if user else ["pkexec", "systemctl", action, name]


def users(procs: List[Dict]) -> List[Dict]:
    """Signed-in users and what their processes use."""
    import psutil
    sessions: Dict[str, Dict] = {}
    for u in psutil.users():
        s = sessions.setdefault(u.name, {"name": u.name, "sessions": 0, "since": u.started, "cpu": 0.0, "memory": 0,
                                         "processes": 0})
        s["sessions"] += 1
        s["since"] = min(s["since"], u.started)
    for p in procs:
        name = p["user"].split("\\")[-1]
        if not name:
            continue
        s = sessions.setdefault(name, {"name": name, "sessions": 0, "since": None, "cpu": 0.0, "memory": 0,
                                       "processes": 0})
        s["cpu"] += p["cpu"]
        s["memory"] += p["memory"]
        s["processes"] += 1
    return sorted(sessions.values(), key=lambda s: (-s["sessions"], -s["memory"]))


def app_history(procs: List[Dict]) -> List[Dict]:
    """CPU time used by each app since it started (Linux keeps no per-app history across restarts)."""
    import psutil
    totals: Dict[str, Dict] = {}
    for p in procs:
        try:
            t = psutil.Process(p["pid"]).cpu_times()
            cpu_s = t.user + t.system
        except (psutil.Error, OSError):
            continue
        a = totals.setdefault(p["name"], {"name": p["name"], "cpu_seconds": 0.0, "processes": 0, "since": p["started"]})
        a["cpu_seconds"] += cpu_s
        a["processes"] += 1
        a["since"] = min(a["since"] or p["started"], p["started"] or a["since"])
    return sorted(totals.values(), key=lambda a: -a["cpu_seconds"])


def human_bytes(n: Optional[float], rate: bool = False) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            s = f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            return s + ("/s" if rate else "")
        n /= 1024
    return str(n)


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m" if h else f"{m}m"
