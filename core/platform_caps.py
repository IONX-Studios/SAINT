"""
core/platform_caps.py

Which desktop capabilities SAINT can use on this machine, and why not when it can't.

SAINT's tools reach the OS through different native interfaces per platform:

    Windows  pywin32 / UI Automation / Core Audio / WinRT (the existing code)
    Linux    KDE Plasma over D-Bus (KWin, Spectacle, PowerDevil), PipeWire, MPRIS,
             the freedesktop Secret Service and Notifications, and a few small
             tools (ydotool, wl-clipboard, tesseract, ...)

``capabilities()`` probes for those interfaces without changing anything and
returns one ``Capability`` per feature, with the provider it found or a hint
naming the Fedora package to install. The System page, ``python -m
core.platform_caps`` and the Linux diagnostics script all read it, so a missing
piece is explained instead of a tool silently failing.
"""

import importlib.util
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence


@dataclass
class Session:
    os: str                 # "windows" | "linux" | "mac"
    desktop: str = ""       # "KDE", "GNOME", ... ($XDG_CURRENT_DESKTOP)
    display: str = ""       # "wayland" | "x11" | "" (no graphical session)
    distro: str = ""        # "fedora"
    distro_version: str = ""

    @property
    def kde(self) -> bool:
        return "kde" in self.desktop.lower()


@dataclass
class Capability:
    name: str
    available: bool
    provider: str = ""            # what SAINT will use
    hint: str = ""                # how to get it when unavailable
    notes: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _os_release(path: str = "/etc/os-release") -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v.strip('"')
    except OSError:
        pass
    return out


def detect_session(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None,
                   os_release: Optional[Mapping[str, str]] = None) -> Session:
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if platform == "win32":
        return Session("windows", "Windows", "windows")
    if platform == "darwin":
        return Session("mac", "Aqua", "quartz")
    rel = _os_release() if os_release is None else os_release
    display = (env.get("XDG_SESSION_TYPE") or "").lower()
    if display not in ("wayland", "x11"):
        display = "wayland" if env.get("WAYLAND_DISPLAY") else ("x11" if env.get("DISPLAY") else "")
    return Session("linux", env.get("XDG_CURRENT_DESKTOP", ""), display,
                   rel.get("ID", ""), rel.get("VERSION_ID", ""))


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _first(which: Callable[[str], Optional[str]], names: Sequence[str]) -> str:
    return next((n for n in names if which(n)), "")


def linux_capabilities(session: Session, which: Callable[[str], Optional[str]] = shutil.which,
                       has_module: Callable[[str], bool] = _has_module,
                       exists: Callable[[str], bool] = os.path.exists) -> List[Capability]:
    wayland = session.display == "wayland"
    caps: List[Capability] = []

    def cap(name, provider, hint, notes=()):
        caps.append(Capability(name, bool(provider), provider, "" if provider else hint, list(notes)))

    dbus = _first(which, ("qdbus6", "qdbus-qt6", "qdbus", "gdbus", "busctl"))
    # Windows: listing, focusing, moving. Wayland gives clients no global window list;
    # on Plasma KWin's scripting interface does it, over the session bus.
    if wayland:
        kwin = f"KWin scripting over D-Bus ({dbus})" if session.kde and dbus else ""
        cap("windows", kwin, "Plasma (KWin) and qdbus6 (dnf install qt6-qttools) are needed on Wayland; "
            "other Wayland compositors don't expose a window list",
            ["window geometry is in logical pixels; scaling comes from KWin"])
    else:
        cap("windows", _first(which, ("wmctrl", "xdotool")), "dnf install wmctrl xdotool")
    # Keyboard and pointer. ydotool needs its daemon and access to /dev/uinput.
    if wayland:
        ydo = "ydotool" if which("ydotool") else ""
        notes = [] if not ydo or exists("/dev/uinput") else ["/dev/uinput missing"]
        cap("input", ydo, "dnf install ydotool; systemctl --user enable --now ydotool "
            "(SAINT's setup adds a narrowly scoped uinput rule — see docs/LINUX_PORT_PLAN.md)", notes)
    else:
        cap("input", "xdotool" if which("xdotool") else "", "dnf install xdotool")
    cap("screenshot", "spectacle" if session.kde and which("spectacle") else
        _first(which, ("grim",)) if wayland else _first(which, ("import", "scrot")),
        "dnf install spectacle (KDE) — or the screenshot portal")
    cap("clipboard", _first(which, ("wl-copy",)) if wayland else _first(which, ("xclip", "xsel")),
        "dnf install wl-clipboard" if wayland else "dnf install xclip")
    cap("notifications", _first(which, ("notify-send", "gdbus")), "dnf install libnotify")
    cap("audio", _first(which, ("wpctl", "pactl")), "PipeWire's wpctl (dnf install wireplumber)")
    cap("media", "playerctl (MPRIS)" if which("playerctl") else (f"MPRIS over D-Bus ({dbus})" if dbus else ""),
        "dnf install playerctl")
    cap("brightness", "brightnessctl" if which("brightnessctl") else
        ("PowerDevil over D-Bus" if session.kde and dbus else ""), "dnf install brightnessctl")
    cap("power", "systemd-logind (loginctl/systemctl)" if which("systemctl") else "", "systemd is required")
    cap("app_launch", _first(which, ("gio", "kioclient", "gtk-launch")), "dnf install glib2")
    cap("open_uri", "xdg-open" if which("xdg-open") else "", "dnf install xdg-utils")
    cap("ocr", "tesseract" if which("tesseract") else "", "dnf install tesseract tesseract-langpack-eng")
    secret = "Secret Service (keyring + SecretStorage)" if has_module("keyring") and has_module("secretstorage") \
        else ""
    cap("secrets", secret, "pip install keyring SecretStorage; KWallet or GNOME Keyring must be running",
        ["KDE: KWallet provides org.freedesktop.secrets from Plasma 6"])
    cap("ui_tree", "AT-SPI (pyatspi)" if has_module("pyatspi") else "",
        "dnf install python3-pyatspi (reading other apps' UI is limited on Wayland)")
    return caps


def windows_capabilities(has_module: Callable[[str], bool] = _has_module) -> List[Capability]:
    win32 = "pywin32" if has_module("win32gui") else ""
    caps = [
        Capability("windows", bool(win32), win32, "" if win32 else "pip install pywin32"),
        Capability("input", has_module("pyautogui"), "SendInput / pyautogui", "pip install pyautogui"),
        Capability("screenshot", has_module("PIL"), "Pillow ImageGrab", "pip install pillow"),
        Capability("clipboard", bool(win32), "win32clipboard", "pip install pywin32"),
        Capability("notifications", True, "Qt tray balloon"),
        Capability("audio", has_module("pycaw"), "Core Audio (pycaw)", "pip install pycaw"),
        Capability("media", has_module("winrt"), "WinRT media sessions", "pip install winrt-Windows.Media.Control"),
        Capability("brightness", True, "WMI"),
        Capability("power", True, "shutdown.exe / user32"),
        Capability("app_launch", True, "Start menu + os.startfile"),
        Capability("open_uri", True, "os.startfile"),
        Capability("ocr", has_module("winrt"), "Windows.Media.Ocr", "pip install winrt-Windows.Media.Ocr"),
        Capability("secrets", has_module("keyring"), "Windows Credential Manager (keyring)", "pip install keyring"),
        Capability("ui_tree", has_module("uiautomation"), "UI Automation", "pip install uiautomation"),
    ]
    for c in caps:
        if c.available:
            c.hint = ""
    return caps


def capabilities(session: Optional[Session] = None) -> List[Capability]:
    session = session or detect_session()
    if session.os == "windows":
        return windows_capabilities()
    return linux_capabilities(session)


def unavailable_reason(name: str) -> str:
    """'' when the capability is usable, else a sentence for the user ("Screenshots need ...")."""
    c = next((c for c in capabilities() if c.name == name), None)
    if c is None or c.available:
        return ""
    return f"{name.replace('_', ' ').capitalize()} isn't available on this system: {c.hint}."


def main() -> int:
    import json
    s = detect_session()
    print(json.dumps({"session": asdict(s), "capabilities": [c.to_dict() for c in capabilities(s)]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
