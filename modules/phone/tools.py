"""Phone tools (Halo, KDE Connect): "text Mom I'm running late", "find my phone", "which phones are connected".
Sending a text is high permission: SAINT reads it back and waits for a yes first (core/permissions.py)."""

import shutil
import subprocess

from modules.automation.tools import P, PermissionLevel, Tool, ToolError
from modules.phone import kdeconnect as kc


def _available():
    # Cheap (asked often); whether a phone is reachable is checked when a tool runs.
    if not shutil.which("kdeconnect-cli"):
        return False, "KDE Connect isn't installed"
    return True, ""


def _phone(name: str = ""):
    dev = kc.pick_device(kc.devices(), name)
    if dev is None:
        raise ToolError("I can't reach your phone. Open KDE Connect on it (same Wi-Fi as this PC) and pair it.")
    return dev


def send_text(to: str, message: str, phone: str = ""):
    dev = _phone(phone)
    number = to.strip()
    who = number
    if not kc._NUMBER.match(number):
        book = kc.contacts(dev.id)
        matches = kc.find_contact(to, book)
        if not matches:
            raise ToolError(f"I couldn't find “{to}” in your phone's contacts (turn on contacts sync in KDE Connect).")
        if len(matches) > 1:
            raise ToolError(f"Which one: {', '.join(matches[:5])}?")
        who, number = matches[0], book[matches[0]][0]
    try:
        argv = kc.send_text_argv(dev.id, number, message)
    except ValueError as e:
        raise ToolError(str(e))
    res = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    if res.returncode != 0:
        raise ToolError(f"Your phone didn't send it: {(res.stderr or res.stdout).strip()[:160]}")
    return {"summary": f"Sent to {who} from {dev.name}: “{' '.join(message.split())}”"}


def find_phone(phone: str = ""):
    dev = _phone(phone)
    subprocess.run(kc.ring_argv(dev.id), capture_output=True, timeout=10)
    return {"summary": f"Ringing {dev.name}."}


def list_phones():
    devs = kc.devices()
    if not devs:
        return {"summary": "No phone is connected right now."}
    return {"summary": "Connected: " + ", ".join(d.name for d in devs), "devices": [d.__dict__ for d in devs]}


def register_phone_tools(registry):
    tools = [
        Tool("phone.send_text", "Send a text message from the user's Android phone (KDE Connect) to a contact name "
             "or phone number", {}, PermissionLevel.HIGH, send_text,
             parameters={"to": P("string", "contact name or phone number"),
                         "message": P("string", "the text to send"),
                         "phone": P("string", "which phone, if more than one", required=False, default="")},
             llm_exposed=True, category="phone"),
        Tool("phone.find", "Ring the user's phone so they can find it", {}, PermissionLevel.LOW, find_phone,
             parameters={"phone": P("string", required=False, default="")}, llm_exposed=True, category="phone"),
        Tool("phone.list", "Which phones are connected to this PC", {}, PermissionLevel.LOW, list_phones,
             parameters={}, llm_exposed=True, category="phone"),
    ]
    for t in tools:
        t.availability = _available
        registry.register(t)
