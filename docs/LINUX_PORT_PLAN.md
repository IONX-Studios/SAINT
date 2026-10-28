# Linux port plan

How SAINT goes from Windows-only to running well on Fedora KDE Plasma, without breaking Windows.
Status per feature is in [FEATURE_COMPATIBILITY.md](FEATURE_COMPATIBILITY.md).

## Principles

- **One codebase.** Windows code stays where it is and keeps working. Linux support goes next to it,
  behind the same functions and tools, chosen at runtime.
- **The permission manager stays the only gate.** A Linux backend is a primitive *under*
  `core/permissions.py` and the validated tool layer: same risk levels, same `ALWAYS_CONFIRM`, same
  blocked key combos.
- **Say why, never fail silently.** `core/platform_caps.py` knows what's missing and which package
  fixes it. Tools raise `ToolError(..., "UNSUPPORTED")` with that sentence.
- **No root for ordinary work.** Power goes through logind (polkit), the firewall through firewalld's
  D-Bus API, input through a user-accessible `uinput` device. Nothing uses `sudo`.

## Target platform

- **Fedora KDE Plasma Desktop**, current stable. Fedora 44 ships Plasma 6.6. If a newer release is out
  when you install, use that: the scripts read `/etc/os-release` and don't hard-code a version.
- **Wayland** (Fedora KDE's default session). X11 tools are supported as a fallback where they exist.
- **Python 3.12** from `dnf install python3.12`, kept in `.venv`. Fedora's default `python3` may be newer
  than some wheels support (onnxruntime, spaCy for Kokoro's phonemizer). 3.13 ran the full test suite
  in the baseline.

## Getting started on Fedora (Milestone C)

```bash
git clone https://github.com/IONX-Studios/SAINT.git ~/SAINT && cd ~/SAINT
git switch saintos/milestone-a-audit          # until it's merged
packaging/linux/setup_fedora.sh               # asks before sudo dnf; asks before the 350 MB voice download
packaging/linux/run_tests.sh                  # saves output to build/test-results/
python -m core.platform_caps                  # (inside .venv) what works, what's missing
./run.sh
packaging/linux/install_user.sh --autostart   # menu entry + systemd user service (optional)
packaging/linux/diagnose.sh                   # read-only report to attach to bug reports
```

**Ollama** is not installed by the script. Install it from Fedora (`dnf info ollama`, if your release
packages it) or the official installer at https://ollama.com/download/linux after reading the script.
Then pull **one** model you chose: SAINT never downloads models without asking.

**NVIDIA (RTX A1000 on this laptop):** Fedora ships `nouveau`, which has no CUDA. For CUDA Whisper
and torch, install the proprietary driver from RPM Fusion (`akmod-nvidia`). With **Secure Boot on**,
the kernel module must be signed with a MOK key you enroll yourself; RPM Fusion documents this. SAINT
doesn't do it for you. Without the driver everything runs on the CPU (slower Whisper; Kokoro is fine).

## Dependency classification

| Package | Class | Linux notes |
|---|---|---|
| PySide6, requests, psutil, numpy, pillow, keyring, segno, zeroconf, cryptography, pytest | cross-platform | wheels on PyPI |
| sounddevice, soundfile | cross-platform, needs config | `portaudio`, `libsndfile` rpms; PipeWire provides the PulseAudio/ALSA side |
| onnxruntime | cross-platform | CPU wheel; GPU via `onnxruntime-gpu` only if wanted |
| faster-whisper (CTranslate2) | cross-platform, needs config for GPU | CUDA 12 + cuDNN 9 libs (pip `nvidia-*` wheels or system) |
| torch, torchaudio | optional | CPU or CUDA 12.8 index; only for Kokoro-from-source. `kokoro-onnx` works without it |
| kokoro, kokoro-onnx, misaki, phonemizer, espeakng_loader, en_core_web_sm | cross-platform | `espeak-ng` rpm helps |
| keyring | cross-platform, needs config | + `SecretStorage`/`jeepney`; KWallet (Plasma 6 serves `org.freedesktop.secrets`) |
| pywin32, uiautomation, pycaw, winrt-* | Windows-only, behind adapters | marked `sys_platform == "win32"` |
| pyautogui | Windows-only here | needs X11 (Xlib); useless on Wayland → ydotool / portal |
| PyInstaller | Windows packaging | not used for Linux packaging |

System packages: [`packaging/linux/fedora-packages.txt`](../packaging/linux/fedora-packages.txt)
(installed with `--skip-unavailable`, so a renamed package gets reported and the install keeps going).

## Linux backends to write (Milestones B–D), in order

1. **Desktop backend interface** (`modules/desktop/backend.py`): `list_windows`, `monitors`, `activate`,
   `move_resize`, `close`, `minimize`, `foreground` returning the existing `WindowInfo`/`MonitorInfo`.
   `DesktopController` delegates its primitives to it. The Windows backend wraps the current pywin32 code
   *unchanged*; the controller's tests pin that behaviour first.
2. **KWin backend** (`modules/desktop/linux/kwin.py`): a small KWin script (JavaScript) loaded with
   `org.kde.kwin.Scripting.loadScript`, which reports `workspace.windowList()` / screens back over D-Bus
   and applies focus/geometry changes. Window IDs are KWin's internal UUIDs, mapped to `WindowInfo.hwnd`
   as opaque handles. Plasma 6 API: `workspace.windowList()`, `workspace.activeWindow`,
   `window.frameGeometry`.
3. **Input**: ydotool with a user-group-scoped udev rule for `/dev/uinput`, installed only after you
   confirm. Alternative: the RemoteDesktop portal (Plasma asks once per session). The key-combo deny-list
   applies before either.
4. **Screenshot / OCR**: `spectacle --background --nonotify --output`, then Tesseract (`--psm 11`, TSV for
   boxes, mapped to the same `find_text()` result shape).
5. **Audio / brightness / power / media / clipboard / idle**: `wpctl`, PowerDevil or `brightnessctl`,
   `loginctl`/`systemctl` (polkit), MPRIS, `wl-clipboard`, `org.freedesktop.ScreenSaver`.
6. **TTS fallback**: espeak-ng engine in `tts_service`.
7. **Self-test**: per-platform required imports.

Every backend gets mocked unit tests (command lines built, output parsed) **and** a `@pytest.mark.real`
test that runs only on Linux and is skipped elsewhere.

## Moving your Windows data to Fedora

Memory, history, scenes and settings are ordinary files in `%LOCALAPPDATA%\SAINT`. Copy that folder
yourself (e.g. to a USB stick) to `~/.local/share/SAINT` on Fedora. Secrets (Spotify login, Link key)
stay in the Windows Credential Manager and must be re-entered once. SAINT never reads the Windows
partition on its own. Writing to NTFS from Linux while Windows Fast Startup is on can corrupt it.
