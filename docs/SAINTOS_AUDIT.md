# SAINTOS audit — Milestone A (2026-10-28)

Read-the-code audit of SAINT at `00cf559` (`main`, v0.4.0) as the starting point for SAINTOS, a
Fedora KDE Plasma desktop with SAINT built in. Everything below was checked in the source, not taken
from the README. Status words: **implemented** (code exists and is tested), **partial**, **broken**,
**Windows-only**, **unverified** (code exists, no evidence it works here).

Companion documents: [feature compatibility](FEATURE_COMPATIBILITY.md) ·
[Linux port plan](LINUX_PORT_PLAN.md) · [target architecture](SAINTOS_ARCHITECTURE.md) ·
[dual-boot installation](DUAL_BOOT_INSTALLATION.md).

## 1. Repository at a glance

| | |
|---|---|
| Branches | `main` (v0.4.0) plus `unified-release` (CI release), `ios-app`, `saint-link`, several `feat/*` / `claude/*` |
| Size | 448 tracked files; ~79,000 lines of Python in `core/`, `modules/`, `ui/`, `tests/`; a Swift iOS app in `mobile/` |
| Entry point | `app.py` (Qt app, tray, single-instance lock) → `core/runtime.py` → `ui/main_window.py`; `app.py --selftest` |
| Python | 3.12 (CI and `setup.bat`); runs on 3.13 (this audit's test run) |
| Packaging | `packaging/windows/` — PyInstaller one-folder `SAINT.exe` + Inno Setup `SAINT-Setup.exe` |
| CI | `.github/workflows/windows-release.yml` (tests, build, self-test, rolling release on `unified-release`); `mobile-ios.yml` |
| Tests | 66 files under `tests/`, isolated by `conftest.py` (temp data dir, mock AI/STT/TTS, in-memory keyring) |

### Lifecycle

```
app.py
  stdout/stderr guard → --profile → core.migration.run() (backup + idempotent steps)
  → config + logger → QApplication → QLockFile (one SAINT) → first-run setup
  → core.module_manager (enable modules, register tools + permission levels)
  → core.runtime (TTS service, ConversationController, voice, scheduler, live settings)
  → ui.main_window (tray, pages, Halo, overlay Alt+`, palette Ctrl+K, mini player)
```

### Request path

```
mic → Silero VAD + echo gate → "hey_saint.onnx" wake word → faster-whisper → stt_filters
  → core/conversation.py → modules/agent/agent.handle()
      pending answers → meta ("stop") → task control → lessons/scenes/aliases/corrections
      → modules/agent/router.py (2,063-line deterministic router) → one validated tool
      → task start → modules/agent/autonomy (observe → plan → act → verify → recover → learn)
      → fallback: modules/agent/llm.py (Ollama /api/chat with tool calling, LLM-exposed tools only)
  → output guard → TTS (Kokoro torch / kokoro-onnx → SAPI fallback), sentence-chunked, barge-in
```

## 2. Subsystems (verified in code)

| Area | Where | Finding |
|---|---|---|
| Router | `modules/agent/router.py`, `*_intents.py` | Implemented, platform-independent logic; some intents call Windows-only tools underneath. |
| Agent task loop | `modules/agent/autonomy/` | Implemented: plan cheapest-first, bounded retries (2/step, 6/task, 15 min, 24 steps), verification by polling. Planner and verifier are portable; the **observer** (`observe.py`) reads windows/UIA/OCR through Windows APIs. |
| Permissions | `core/permissions.py`, `autonomy/policy.py` | Implemented. Safe / Confirm / Autonomous × LOW / MEDIUM / HIGH, per-tool overrides, `ALWAYS_CONFIRM` set. Platform-independent — must be kept as the single gate for Linux tools too. |
| Memory | `modules/memory/` (SQLite), `core/migration.py` | Implemented, portable (sqlite3). Migration backs up before changing anything; backups go next to the data folder. |
| Voice | `modules/voice/` | Wake word (onnxruntime), VAD, faster-whisper (CUDA → CPU fallback), Kokoro (torch or onnx) — all cross-platform libraries. Fallback voice `sapi.py` is Windows-only. Audio via `sounddevice` (PortAudio → PipeWire on Fedora). |
| Desktop control | `modules/desktop/controller.py` (1,130 lines), `uia.py`, `window_match.py`, `system_controls.py`, `media.py`, `clipboard.py`, `apps.py` | **Windows-only primitives** (pywin32 HWNDs, SendInput, UI Automation, pycaw, WinRT media). The decision logic on top (monitor resolution, window matching, safety checks, blocked key combos) is portable. Imports are guarded, so the module loads on Linux but its tools raise `UNSUPPORTED`. |
| Screen awareness | `modules/vision/screen.py`, `ocr.py` | Screenshot via Pillow; OCR via `Windows.Media.Ocr` (WinRT) — Windows-only. Vision-model path (Ollama) portable. |
| Spotify | `modules/spotify/` | Web API + PKCE, tokens in `keyring` — portable. Opening the desktop app goes through app launch. |
| Steam | `modules/steam/` | VDF parsing portable; Steam location was registry/`C:\` only — **broken on Linux** (`import winreg` raised `ImportError`, not caught). **Fixed in this milestone.** |
| Notifications | `modules/notifications/reader.py` | Reads Windows' notification database — Windows-only. |
| SAINT Link | `modules/link/` | Noise-protocol crypto, mDNS, sync — portable. Firewall management uses `netsh` via UAC — Windows-only (guarded by `os.name`). |
| MCP client | `modules/mcp/` | Portable (stdio subprocesses). |
| Game Mode, focus guard | `core/game_mode.py`, `core/focus_guard.py` | Win32 foreground/fullscreen detection — Windows-only, guarded. |
| Autostart | `core/autostart.py` | Startup-folder `.lnk` via PowerShell — Windows-only (no-op elsewhere). |
| Data paths | `core/paths.py` | `%LOCALAPPDATA%\SAINT`. On Linux this fell back to `~/AppData/Local` — **fixed**: `$XDG_DATA_HOME/SAINT` (`~/.local/share/SAINT`). |
| AI providers | `modules/ai/providers.py` | `mock`, `ollama`, `openai` (OpenAI-compatible). No cost/token accounting, no budgets. |
| UI | `ui/` (PySide6) | Portable Qt. `ui/win.py` (DWM dark title bar, hotkeys via Win32 message loop) is Windows-only and already guarded by `sys.platform`. The global hotkey for the overlay needs a KDE replacement (KGlobalAccel). |

## 3. Highest-risk Windows dependencies

Ranked by how much SAINT loses on Linux without them:

1. **Window enumeration/focus/move** (`controller.py`, pywin32). Wayland gives apps no global
   window list. On Plasma the supported route is a **KWin script** loaded over D-Bus. This is the
   biggest porting job, and agent verification depends on it.
2. **Synthetic keyboard/mouse** (SendInput/pyautogui). On Wayland: `ydotool` (needs `/dev/uinput`
   access) or the xdg-desktop-portal **RemoteDesktop** portal (asks the user once). Both need
   setup; neither works silently.
3. **UI Automation tree** (`uia.py`, observer). Nearest equivalent is AT-SPI, with less coverage on
   Wayland. Verification will rely more on window titles, processes, ports and OCR.
4. **OCR** (WinRT) → Tesseract.
5. **Audio endpoints and per-app volume** (pycaw) → PipeWire (`wpctl`, or `pw-dump` for per-app streams).
6. **Media sessions** (WinRT) → MPRIS (`playerctl` / D-Bus), which is actually richer.
7. **SAPI fallback voice** → espeak-ng (already a Kokoro dependency).

## 4. Security findings

| # | Finding | Severity | Action |
|---|---|---|---|
| S1 | `ai.api_key` is stored **in plaintext** in `config.json` (`core/config.py:24`, read in `modules/ai/module.py:139,279`). Spotify and Link secrets already use `keyring`. | High for paid providers | Milestone F: move to keyring (Secret Service on Linux) with a one-time migration that clears the config value. |
| S2 | `get_provider()` silently falls back to `MockProvider` for an unknown provider name (`providers.py:337-341`) — a typo yields canned answers that look like success. | Medium | Milestone F: raise a clear configuration error instead. |
| S3 | No spend limits exist; only `ai.max_tokens` (160) bounds a call. | Medium once paid APIs are enabled | Milestone F (budget ledger, hard stop). |
| S4 | Link firewall changes elevate through UAC (`ShellExecuteW runas`) — Windows-only, gated by `link.manage_firewall`. The Linux equivalent must use firewalld's polkit-mediated D-Bus API, never `sudo`. | Note | Milestone D. |
| S5 | Desktop tools are bounded (validated key names, blocked session-ending combos, length limits, coordinate checks) and go through the permission manager. | Good | Keep — Linux backends plug in **under** these checks, not around them. |

## 5. Baseline tests (before any change)

Run on this machine (Windows 11 26200, Lenovo 21FB, i7-13700HX, 32 GB, RTX A1000 6 GB) with
**Python 3.13.0** (the Store-installed 3.12 can't be launched from this sandbox) and the core
dependencies only — **not** installed: `torch`, `kokoro`, `kokoro-onnx`, `misaki`, `faster-whisper`,
`winrt-*`, `en_core_web_sm`, `PyInstaller`.

```
QT_QPA_PLATFORM=offscreen python -m pytest -q -rfE -p no:randomly      (main @ 00cf559)
4 failed, 1428 passed, 2 skipped in 105.13s
```

| Failing test | Cause | Code defect? |
|---|---|---|
| `test_history_stats::test_daily_summary_survives_trimming` | `WinError 5` renaming `history_daily.json.tmp` in the pytest temp dir (file briefly locked — antivirus/indexer) | No — environment; worth making the write retry (`os.replace` on a locked target) |
| `test_tts_fallback::test_fallback_engine_reaches_fallback_state` | Kokoro not installed → the real SAPI voice was picked instead of the test's engine | No — the test depends on the Kokoro package being importable |
| `test_tts_fallback::test_non_fallback_engine_reaches_ready_state` | same | same |
| `test_v04_selftest::test_quick_checks_pass_from_source` | self-test requires `faster_whisper`, `winrt` | No — missing optional packages |

Not run: the Windows build (`packaging/windows/build.py`), `--selftest-voice`, anything needing a
microphone, GPU models, Ollama, or a Spotify account. The previous audit's numbers (820 → 864 passing)
were from a full environment and are not comparable.

## 6. Minimum viable Linux port

SAINT is useful on Fedora once these work, in this order:

1. Starts on Fedora KDE (Wayland) with correct data paths, single instance, tray, UI. **← paths done**
2. Chat + deterministic commands + memory + reminders (portable already — needs a test run on Fedora).
3. Voice: PipeWire mic, wake word, Whisper (CPU or CUDA), Kokoro; espeak-ng fallback.
4. Opening apps (XDG desktop entries) **← done**, Spotify (Web API), Steam library **← done**.
5. Window list/focus/move via KWin, then input via ydotool/portal, screenshots via Spectacle, OCR via Tesseract.

## 7. What changed in this milestone

Small, tested, Windows behaviour unchanged:

| Change | Files |
|---|---|
| XDG data directory on Linux; system-install detection | `core/paths.py` |
| Linux app discovery from `.desktop` files; launch via `gio`/`kioclient`/`xdg-open`; KDE built-ins | `modules/desktop/apps.py` |
| Steam found in Linux/Flatpak locations; no `ImportError` crash | `modules/steam/library.py` |
| Capability probe (what works, what's missing, which package fixes it) | `core/platform_caps.py` |
| Read-only hardware/storage/boot preflight (refuses any disk-changing command) | `tools/preflight.py` |
| Windows-only requirements marked `sys_platform == "win32"`; `requirements-linux.txt` | `requirements*.txt` |
| Fedora setup, launcher, tests, diagnostics, menu entry, user service | `run.sh`, `packaging/linux/*` |
| 38 new tests (mocked Linux environments + 2 real-machine probes) | `tests/test_linux_platform.py` |
