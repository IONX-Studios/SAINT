"""
modules/phone/kdeconnect.py

Your phone on Halo, through KDE Connect (installed with Halo; pair from the phone's KDE Connect app):

    Android   notifications on the PC (Plasma shows them), texts read and sent, find my phone, files,
              clipboard, media control
    iPhone    the KDE Connect iOS app: files, clipboard, find my phone. Apple doesn't let other software
              read or send iMessages/texts or mirror notifications from a PC, so texting is Android only;
              SAINT Mobile (mobile/ios) brings SAINT itself to the iPhone.

    devices()                           paired phones that are reachable now
    contacts(device)                    name -> numbers, from the contacts the phone synced (vCards)
    find_contact(name, device)          best matches for a spoken name ("mom", "Gian")
    send_text_argv(device, number, msg) the kdeconnect-cli command (checked: a phone number, one line)
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

VCARDS = Path.home() / ".local" / "share" / "kpeoplevcard"
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_]{6,64}$")
_NUMBER = re.compile(r"^\+?[0-9][0-9 ().-]{2,24}$")


@dataclass
class Device:
    id: str
    name: str


def devices(run: Callable = subprocess.run) -> List[Device]:
    try:
        out = run(["kdeconnect-cli", "--list-available", "--id-name-only"], capture_output=True, text=True,
                  timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.splitlines():
        dev_id, _, name = line.strip().partition(" ")
        if _DEVICE_ID.match(dev_id):
            found.append(Device(dev_id, name.strip() or dev_id))
    return found


def contacts(device_id: str, root: Path = VCARDS) -> Dict[str, List[str]]:
    """The contacts KDE Connect synced from the phone: display name -> phone numbers."""
    book: Dict[str, List[str]] = {}
    folder = root / f"kdeconnect-{device_id}"
    for card in sorted(folder.glob("*.vcf")) if folder.is_dir() else []:
        name, numbers = "", []
        for line in card.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition(":")
            key = key.split(";")[0].upper()
            if key == "FN":
                name = value.strip()
            elif key == "TEL" and value.strip():
                numbers.append(value.strip())
        if name and numbers:
            book.setdefault(name, []).extend(n for n in numbers if n not in book.get(name, []))
    return book


def find_contact(spoken: str, book: Dict[str, List[str]]) -> List[str]:
    """Contact names matching what was said: exact, then whole words, then a prefix."""
    s = spoken.strip().lower()
    if not s:
        return []
    exact = [n for n in book if n.lower() == s]
    if exact:
        return exact
    words = [n for n in book if s in n.lower().split()]
    return words or [n for n in book if n.lower().startswith(s)]


def send_text_argv(device_id: str, number: str, message: str) -> List[str]:
    if not _DEVICE_ID.match(device_id):
        raise ValueError("unknown phone")
    if not _NUMBER.match(number):
        raise ValueError(f"{number!r} isn't a phone number")
    message = " ".join(message.split())
    if not message or len(message) > 1000:
        raise ValueError("the message is empty or too long")
    return ["kdeconnect-cli", "--device", device_id, "--send-sms", message, "--destination", number]


def ring_argv(device_id: str) -> List[str]:
    if not _DEVICE_ID.match(device_id):
        raise ValueError("unknown phone")
    return ["kdeconnect-cli", "--device", device_id, "--ring"]


def pick_device(devs: List[Device], name: str = "") -> Optional[Device]:
    if name:
        for d in devs:
            if name.lower() in d.name.lower():
                return d
        return None
    return devs[0] if devs else None
