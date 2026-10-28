# Feature compatibility — Windows SAINT → SAINTOS (Fedora KDE)

Updated 2026-10-28 (Milestone A). "Linux" means Fedora KDE Plasma 6 on **Wayland**, Fedora's
default session; X11 notes are given where they differ. **Nothing in the Linux column has been run on
Fedora yet.** It comes from reading the code, the platform APIs, and mocked tests. A status changes
to *verified* only when `packaging/linux/run_tests.sh` or a manual check on Fedora shows it working.

Status: **portable** (no OS code; expected to work) · **ported** (Linux backend exists, mocked tests) ·
**planned** (backend designed, not written) · **blocked** (no equivalent; alternative given) ·
**verified** (seen working on Fedora).

| Feature | Current implementation | Existing behaviour | Linux compatibility | Dependencies / blockers | Required implementation | Automated test | Status |
|---|---|---|---|---|---|---|---|
| Startup, tray, single instance | `app.py`, `QLockFile` | One SAINT per data folder; tray keeps it alive | Qt portable; tray via StatusNotifierItem on Plasma | AppUserModelID call already in `try` | none beyond data paths | `test_ui_smoke`, `test_linux_platform` (paths) | portable |
| Data folder | `core/paths.py` | `%LOCALAPPDATA%\SAINT` | `~/.local/share/SAINT` (XDG) | — | **done** | `test_linux_platform::test_linux_user_data_*` | ported |
| Data migration / backup | `core/migration.py` | Backup then idempotent steps | portable (pathlib, json) | Windows → Linux data import isn't automatic | add an "import from a SAINT backup folder" action (Milestone D) | `test_v04_migration` | portable |
| Settings, logging | `core/config.py`, `core/logger.py` | JSON config, rotating logs | portable | — | — | many | portable |
| Deterministic router | `modules/agent/router.py` | Intent → one tool | portable | intents that hit Windows-only tools return `UNSUPPORTED` | none in router; backends below | `test_agent_router`, `test_natural_commands` | portable |
| Planner / executor / verifier / recovery | `modules/agent/autonomy/` | Bounded task loop | logic portable; `observe.py` reads windows/UIA | depends on the window backend | Observer reads through the desktop backend interface | `test_v04_agent` | partial |
| Permission modes | `core/permissions.py`, `autonomy/policy.py` | Safe / Confirm / Autonomous | portable | Linux tools must register with the same levels | keep as the only gate | `test_permissions`, `test_meta_and_policy` | portable |
| Persistent memory | `modules/memory/` (SQLite) | Facts, learner, profile | portable | — | — | `test_memory*` | portable |
| Procedures / skills / scenes | `modules/learning/`, `modules/automation/` | Learned steps, scenes, schedules | portable logic; demonstration recorder uses Win32 hooks | `learning/demonstration.py` (`ctypes.wintypes`) | Linux: disable "watch and learn" with a clear message (no global input hooks on Wayland) | `test_learning` | partial |
| Reminders / scheduler | `modules/automation/scheduler.py` | Timed tasks | portable | — | — | `test_scheduler` | portable |
| Wake word | `modules/voice/wake_word.py`, `data/wake/*.onnx` | onnxruntime model | portable | onnxruntime wheel for the Python version | — | `test_wake_and_voice` | portable |
| Microphone / VAD | `modules/voice/module.py`, `mic_select.py` | sounddevice, device index | PortAudio → PipeWire | `portaudio` rpm; device names differ | device selection by name, not index | `test_voice_assistant_fixes` | portable (unverified) |
| Speech-to-text | `modules/voice/stt.py` | faster-whisper, CUDA → CPU | portable | CUDA libs on Fedora need the NVIDIA driver (RPM Fusion) | — | `test_wake_and_voice` (mock) | portable |
| Text-to-speech | `modules/voice/tts.py`, `kokoro_onnx.py`, `tts_service.py` | Kokoro; SAPI fallback | Kokoro portable; **SAPI is Windows-only** | `espeak-ng` | espeak-ng fallback engine in `tts_service._fallback` | `test_tts_fallback` | partial |
| Interruption / barge-in | `voice_state.py`, `audio_echo.py` | Stop on speech, echo gate | portable | — | — | `test_wake_and_voice` | portable |
| App discovery / launch | `modules/desktop/apps.py` | Start menu, Store apps, App Paths | `.desktop` entries + Flatpak exports; `gio launch` | `glib2`, `xdg-utils` | **done** | `test_linux_platform::test_*desktop*`, `*launch*` | ported |
| Window list / focus / move / snap / close | `modules/desktop/controller.py`, `window_match.py` | Win32 HWNDs | Wayland: KWin script over D-Bus. X11: wmctrl/xdotool | `qt6-qttools` (qdbus6); KWin only | `modules/desktop/linux/kwin.py` backend | — | planned |
| Monitors | `controller.monitors()` | EnumDisplayMonitors + DPI | KWin `workspace.screens` / Qt `QGuiApplication.screens()` | — | Qt screens first (portable), KWin for work areas | — | planned |
| Keyboard / mouse | `controller.py` (SendInput), pyautogui | Validated keys, blocked combos | ydotool (uinput) or RemoteDesktop portal | `/dev/uinput` access; portal asks once | `modules/desktop/linux/input.py` | `test_linux_platform` (caps only) | planned |
| Clipboard | `modules/desktop/clipboard.py` | win32clipboard | `wl-copy`/`wl-paste`, or Qt clipboard while SAINT has focus | `wl-clipboard` | Linux branch in `clipboard.py` | — | planned |
| Screenshot | `modules/vision/screen.py`, `system_controls.screenshot` | Pillow ImageGrab | `spectacle -b -n -o` (KDE) or screenshot portal | `spectacle` | Linux capture function | — | planned |
| OCR | `modules/vision/ocr.py` | Windows.Media.Ocr | Tesseract | `tesseract`, langpack | `ocr.py` Tesseract backend | — | planned |
| UI element tree | `modules/desktop/uia.py` | UI Automation | AT-SPI (`pyatspi`), partial coverage | apps must expose accessibility | AT-SPI backend for read-only lookups | — | planned |
| System volume / mic mute / output device | `system_controls.py` (pycaw) | Core Audio | `wpctl` (PipeWire) | `wireplumber` | Linux branch | — | planned |
| Per-app volume | `system_controls.app_volume` | Core Audio sessions | `pw-dump` + `wpctl set-volume <id>` | — | match streams by `application.name` | — | planned |
| Brightness | `system_controls.brightness` (WMI) | Laptop panel | PowerDevil D-Bus or `brightnessctl` | — | Linux branch | — | planned |
| Lock / sleep / shutdown | `system_controls.power` | shutdown.exe (ALWAYS_CONFIRM) | `loginctl lock-session`, `systemctl suspend/poweroff` (polkit, no root) | — | Linux branch, still ALWAYS_CONFIRM | — | planned |
| Media now-playing / mini player | `modules/desktop/media.py` (WinRT) | Any app's media session | MPRIS over D-Bus (`playerctl`) | `playerctl` | `media.py` MPRIS backend | — | planned |
| Notifications (show) | Qt tray / toasts | Toasts in SAINT | Qt tray + `org.freedesktop.Notifications` | — | — | `test_ui_smoke` | portable |
| Notifications (read others') | `modules/notifications/reader.py` | Reads Windows' notification DB | **blocked**: freedesktop has no API to read other apps' notifications; eavesdropping the bus is fragile and disabled by policy | — | Alternative: Plasma's notification history isn't public API → document; offer KDE Connect phone notifications later | — | blocked |
| Spotify | `modules/spotify/` | Web API, PKCE, keyring tokens | portable; Secret Service for tokens | KWallet / GNOME Keyring running | — | `test_spotify*` | portable |
| Steam | `modules/steam/` | VDF libraries, `steam://` | Native / `~/.steam` / Flatpak paths | — | **done** (path lookup) | `test_linux_platform::test_steam_*`, `test_steam` | ported |
| Browser / YouTube control | `modules/desktop/browser.py`, `youtube.py` | Keyboard + UIA | depends on input + window backends | — | follows the backends | `test_youtube`, `test_browser_choice` | planned |
| Files, archives, junk clean-up | `modules/files/` | Recycle Bin via shell, winreg for associations | `gio trash` for the Recycle Bin; paths portable | `glib2` | Linux trash + skip Windows-only junk locations | `test_files` | partial |
| Voicemeeter | `modules/desktop/voicemeeter.py` | Voicemeeter DLL | **blocked** (Windows product) | — | Not offered on Linux; PipeWire covers routing | — | blocked |
| Game Mode | `core/game_mode.py` | Detects fullscreen games, moves the Halo | Partial: KWin can report fullscreen windows; `gamemoded` status over D-Bus | — | KWin/gamemode backend | `test_game_mode` | planned |
| Focus guard / idle time | `core/focus_guard.py` | GetLastInputInfo | `org.freedesktop.ScreenSaver.GetSessionIdleTime` (KDE) | — | Linux branch | — | planned |
| Autostart | `core/autostart.py` | Startup-folder shortcut | systemd user service / XDG autostart | — | `packaging/linux/install_user.sh` **done**; wire the Settings toggle | — | partial |
| Global hotkey (overlay Alt+`) | `ui/win.py` | Win32 RegisterHotKey | KGlobalAccel / a Plasma shortcut that runs `saint --overlay` | — | D-Bus method + `.desktop` action | — | planned |
| SAINT Link | `modules/link/` | Encrypted peer sync, mDNS | portable; firewall via firewalld D-Bus | `zeroconf` | firewalld branch (polkit) | `test_link_*` | portable (firewall planned) |
| MCP servers | `modules/mcp/` | stdio clients | portable | — | — | `test_mcp` | portable |
| Local LLM (Ollama) | `modules/ai/providers.py` | `/api/chat`, tools | portable | Ollama installed separately | hardware-aware model selection (Milestone F) | `test_capabilities` | portable |
| Hosted providers | `providers.OpenAICompatibleProvider` | Bearer key from **plaintext config** | portable | — | keyring storage, budgets, accounting (Milestone F) | — | partial |
| Self-test | `core/selftest.py` | Imports + assets + UI + agent | imports list is Windows-specific (`winrt`, `uiautomation`) | — | per-platform required imports | `test_v04_selftest` | partial |
| Packaging | `packaging/windows/` | PyInstaller + Inno Setup | Linux: source install + user service now; RPM later | — | `packaging/linux/` **started** | `test_linux_platform::test_linux_scripts_*` | partial |
| Hardware/storage preflight | — (new) | — | Linux + Windows, read-only | — | **done** `tools/preflight.py` | `test_linux_platform::test_preflight_*` | ported |
