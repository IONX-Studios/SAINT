"""SAINT Task Manager's data layer (core/sysinfo.py): real readers, injected in tests."""

from collections import namedtuple

import pytest

from core import sysinfo

T = namedtuple("T", "label current high critical")


def _p(pid, name, user="me", cpu=0.0, mem=0, cmd="", read=None, write=None):
    return {"pid": pid, "name": name, "user": user, "status": "running", "cpu": cpu, "memory": mem, "threads": 1,
            "ppid": 1, "started": 0, "command": cmd, "disk_read": read, "disk_write": write,
            "critical": name in sysinfo.CRITICAL}


def test_filter_and_sort_processes():
    procs = [_p(10, "firefox", cpu=12.0, mem=900), _p(11, "kwin_wayland", cpu=3.0, mem=200),
             _p(12, "sshd", user="root", cpu=50.0), _p(13, "python", cmd="python app.py", read=5, write=5)]
    assert [p["pid"] for p in sysinfo.filter_processes(procs)] == [12, 10, 11, 13]
    assert [p["pid"] for p in sysinfo.filter_processes(procs, user="me")] == [10, 11, 13]
    assert [p["pid"] for p in sysinfo.filter_processes(procs, "app.py")] == [13]
    assert [p["pid"] for p in sysinfo.filter_processes(procs, sort="memory")][:2] == [10, 11]
    assert [p["pid"] for p in sysinfo.filter_processes(procs, sort="disk")][0] == 13
    assert procs[1]["critical"] and not procs[0]["critical"]


def test_cpu_temp_prefers_the_package_sensor():
    temps = {"nvme": [T("Composite", 40, 0, 0)], "k10temp": [T("Tccd1", 70, 0, 0), T("Tctl", 61.25, 0, 0)]}
    assert sysinfo.cpu_temp(lambda: temps) == 61.2 or sysinfo.cpu_temp(lambda: temps) == 61.3
    assert sysinfo.cpu_temp(lambda: {}) is None


def test_gpus_from_nvidia_smi_and_amdgpu_sysfs():
    files = {"/sys/class/drm/card1/device/vendor": "0x1002", "/sys/class/drm/card1/device/gpu_busy_percent": "37",
             "/sys/class/drm/card1/device/mem_info_vram_used": str(2 * 2 ** 30),
             "/sys/class/drm/card1/device/mem_info_vram_total": str(8 * 2 ** 30),
             "/sys/class/drm/card1/device/hwmon/hwmon3/temp1_input": "55000",
             "/sys/class/drm/card1/device/uevent": "PCI_SLOT_NAME=0000:03:00.0"}

    def run(argv):
        if argv[0] == "nvidia-smi":
            return "NVIDIA RTX A1000, 5, 62, 6144, 42, 596.58\n"
        if argv[0] == "lspci":
            return "03:00.0 VGA compatible controller: AMD Radeon RX 7600\n"
        return ""

    def glob(pattern):
        if pattern.endswith("card[0-9]"):
            return ["/sys/class/drm/card1"]
        return [k for k in files if k.startswith(pattern.split("*")[0]) and k.endswith("temp1_input")]
    out = sysinfo.gpus(run=run, read=files.get, globber=glob)
    assert out[0]["vendor"] == "NVIDIA" and out[0]["temp_c"] == 42 and out[0]["memory_total"] == 6144 * 2 ** 20
    amd = out[1]
    assert amd["name"] == "AMD Radeon RX 7600" and amd["percent"] == 37 and amd["temp_c"] == 55.0
    assert amd["memory_used"] == 2 * 2 ** 30


def test_gpu_values_a_driver_doesnt_expose_stay_none():
    files = {"/sys/class/drm/card0/device/vendor": "0x8086", "/sys/class/drm/card0/device/uevent": ""}
    out = sysinfo.gpus(run=lambda a: "", read=files.get, globber=lambda p: ["/sys/class/drm/card0"]
                       if p.endswith("card[0-9]") else [])
    assert out == [{"name": "Intel GPU", "vendor": "Intel", "percent": None, "memory_used": None,
                    "memory_total": None, "temp_c": None, "driver": ""}]


def test_battery_health_from_sysfs():
    files = {"/sys/class/power_supply/BAT0/energy_full": "45000000",
             "/sys/class/power_supply/BAT0/energy_full_design": "60000000",
             "/sys/class/power_supply/BAT0/capacity": "81", "/sys/class/power_supply/BAT0/status": "Discharging",
             "/sys/class/power_supply/BAT0/cycle_count": "212"}
    b = sysinfo.battery(read=files.get, globber=lambda p: ["/sys/class/power_supply/BAT0"])
    assert b == {"percent": 81, "status": "Discharging", "health_percent": 75.0, "cycles": "212"}
    assert sysinfo.battery(read=files.get, globber=lambda p: []) is None


def test_autostart_user_file_overrides_system(tmp_path):
    sysd, user = tmp_path / "xdg", tmp_path / "user"
    sysd.mkdir()
    user.mkdir()
    (sysd / "a.desktop").write_text("[Desktop Entry]\nName=Alpha\nExec=alpha\n")
    (sysd / "g.desktop").write_text("[Desktop Entry]\nName=Gnome only\nExec=g\nOnlyShowIn=GNOME;\n")
    (sysd / "b.desktop").write_text("[Desktop Entry]\nName=Beta\nExec=beta\n")
    (user / "b.desktop").write_text("[Desktop Entry]\nName=Beta\nExec=beta\nHidden=true\n")
    entries = sysinfo.autostart_entries(str(user), (str(sysd),))
    assert [(e["name"], e["enabled"]) for e in entries] == [("Alpha", True), ("Beta", False)]
    msg = sysinfo.set_autostart(entries[0], False, str(user))
    assert "no longer start" in msg and "Hidden=true" in (user / "a.desktop").read_text()
    assert [(e["name"], e["enabled"]) for e in sysinfo.autostart_entries(str(user), (str(sysd),))][0] == ("Alpha", False)
    sysinfo.set_autostart(sysinfo.autostart_entries(str(user), (str(sysd),))[0], True, str(user))
    assert sysinfo.autostart_entries(str(user), (str(sysd),))[0]["enabled"]


def test_services_parse_systemctl():
    units = ("ollama.service loaded active running Ollama Service\n"
             "bluetooth.service loaded inactive dead Bluetooth service\n")
    files = "ollama.service enabled enabled\nbluetooth.service disabled enabled\n"
    out = sysinfo.services(run=lambda a: units if "list-units" in a else files)
    assert out[0] == {"name": "ollama.service", "load": "loaded", "active": "active", "sub": "running",
                      "description": "Ollama Service", "enabled": "enabled"}
    assert out[1]["enabled"] == "disabled"


def test_service_actions_are_checked():
    assert sysinfo.service_argv("ollama.service", "restart", user=False) == ["pkexec", "systemctl", "restart",
                                                                            "ollama.service"]
    assert sysinfo.service_argv("saint.service", "stop", user=True)[:2] == ["systemctl", "--user"]
    with pytest.raises(ValueError):
        sysinfo.service_argv("x.service; rm -rf /", "stop", user=False)
    with pytest.raises(ValueError):
        sysinfo.service_argv("ollama.service", "mask", user=False)


def test_human_units():
    assert sysinfo.human_bytes(None) == "—"
    assert sysinfo.human_bytes(1536) == "1.5 KB"
    assert sysinfo.human_bytes(2 * 2 ** 20, rate=True) == "2.0 MB/s"
    assert sysinfo.human_duration(90061) == "1d 1h 1m"
