# SAINTOS target architecture

SAINTOS = **a normal Fedora KDE Plasma desktop** + **SAINT as a user-level service** + **a thin
SAINTOS layer** (branding, launcher, control center, first-run). Every layer can be removed or
upgraded without touching the others, and the desktop stays fully usable when SAINT is stopped.

```
┌──────────────────────── SAINTOS image (Milestone G) ────────────────────────┐
│ Fedora KDE Plasma (Wayland)  ── unchanged Fedora packages, normal updates      │
│ SAINTOS layer  ── Plasma look-and-feel + accent, wallpaper, SAINT launcher      │
│                   applet, control-center KCM / app, first-run wizard            │
│ SAINT (per user)  ── systemd --user service, ~/.local/share/SAINT              │
│   core/   agent, router, permissions, memory, tasks          (portable)        │
│   modules/ai/      providers, model router, budgets          (portable)        │
│   modules/voice/   mic, wake word, STT, TTS                  (portable libs)   │
│   modules/desktop/ shared logic → backend interface                            │
│        ├─ Windows backend  (pywin32, UIA, pycaw, WinRT — existing code)        │
│        └─ Linux backend    (KWin D-Bus, ydotool/portal, Spectacle, PipeWire,   │
│                             MPRIS, Secret Service, Tesseract, AT-SPI)          │
│   modules/extensions/  manifests, sandboxed runners (Milestone F)              │
│   ui/   PySide6 app (existing) + SAINTOS control center pages                  │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Decisions and tradeoffs

| Decision | Chosen | Why / rejected alternative |
|---|---|---|
| Repository layout | Keep SAINT's existing `core/`, `modules/`, `ui/`; add `modules/desktop/linux/`, `packaging/linux/` | A reorganisation into new top-level packages would touch every import and break the Windows build for no functional gain. |
| Platform selection | Runtime (`os.name`, `core/platform_caps.detect_session()`) | Build-time variants would fork the code. |
| Window control on Wayland | KWin scripting over D-Bus | Wayland has no global window API. `xdotool`/`wmctrl` only see XWayland windows. GNOME-style extensions don't apply to Plasma. Downside: KDE-specific — acceptable, SAINTOS *is* KDE. |
| Synthetic input | ydotool first, RemoteDesktop portal second | ydotool works without per-session prompts but needs uinput access (scoped udev rule, user's opt-in). The portal is the "proper" API but asks every session on Plasma. |
| Service model | `systemd --user` unit bound to `graphical-session.target` | Gives start/stop/restart/status/journal with no root. A system service can't reach the user's session bus, audio or Wayland socket. |
| Secrets | `keyring` → Secret Service (KWallet) | Same code as Windows Credential Manager; no plaintext. |
| Control center | Extend the existing PySide6 Settings/System pages first; a Plasma KCM wrapper later | Reuses working UI; a KCM (QML + C++) is a separate build chain — worth it only once the pages are stable. |
| Launcher | `.desktop` entry + Plasma global shortcut + (later) a Plasma applet that talks D-Bus to SAINT | Applet needs a D-Bus API on SAINT first. |
| OS image | Decide in Milestone G between **bootc / Fedora Kinoite-style image** and **kickstart + Fedora KDE spin customisation** | Image-based (bootc) gives atomic updates and rollback but is newer for KDE desktops; a kickstart/Kiwi-built ISO is the conventional path for dual-boot installs. Both must be tested in a VM, including the installer's partition behaviour, before touching the real disk. |

## Desktop backend interface (Milestone B)

```python
class DesktopBackend(Protocol):
    def monitors(self) -> list[MonitorInfo]: ...
    def list_windows(self) -> list[WindowInfo]: ...
    def foreground(self) -> WindowInfo | None: ...
    def activate(self, handle) -> None: ...
    def move_resize(self, handle, left: int, top: int, width: int, height: int) -> None: ...
    def set_state(self, handle, state: Literal["minimized", "maximized", "normal"]) -> None: ...
    def close(self, handle) -> None: ...
    def process_of(self, handle) -> str: ...

class InputBackend(Protocol):
    def key_combo(self, keys: list[str]) -> None: ...      # already validated by the controller
    def type_text(self, text: str) -> None: ...
    def pointer(self, action, x, y, button) -> None: ...
```

`DesktopController` keeps all decision logic (matching, monitor words, ambiguity, safety) and
delegates only these primitives, so Windows behaviour is pinned by the existing tests before the
split.

Further interfaces, same pattern: `ScreenCapture`, `TextRecognizer` (OCR), `AudioControl`,
`MediaSessions`, `PowerControl`, `Clipboard`, `Notifications`, `SecretStore` (keyring already),
`SystemInfo` (psutil already).

## AI layer (Milestone F)

- `modules/ai/providers.py` keeps the provider classes; add a registry with explicit errors for
  unknown names, and per-provider model lists + pricing that **you** configure (never hard-coded
  prices).
- `modules/ai/router.py`: picks a model from objective signals: the task category, the tools the
  task needs, whether a cheaper attempt failed validation, the measured hardware (from
  `tools/preflight.py`'s probes). The planner/recovery code asks the router; it never names a model.
- `modules/ai/budget.py`: a ledger (`budget.jsonl`: time, provider, model, tokens in/out, cost,
  latency, task category, outcome — no prompt text), daily/monthly/per-task limits, a hard stop, and
  an `ai.allow_paid` switch that defaults to **off**. An escalation that made no progress isn't
  retried with the same model.

## Extensions (Milestone F)

A manifest (`extension.toml`): id, version, entry point, declared capabilities → the permission
levels they map to, dependencies, config schema. Installing or activating one requires your
approval. Each extension runs in its own process with no access to secrets, can only reach SAINT
through declared tools, and can be disabled, rolled back or uninstalled. A failed generation attempt
stays in its git branch / staging folder and never touches the active install.
