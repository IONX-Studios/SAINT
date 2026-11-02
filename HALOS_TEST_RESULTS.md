# Halo OS test results

Only tests that actually ran are listed. "Not run" is said explicitly.

## 2026-11-02 — Windows dev host, `halo/arch-rebase`

| Test | Result |
|---|---|
| Full pytest suite (`.venv`, Windows 11) | 1579 passed, 5 skipped, **4 failed** — the same 4 that failed before this session (`test_history_stats::test_daily_summary_survives_trimming`, `test_tts_fallback` ×2, `test_v04_selftest::test_quick_checks_pass_from_source`); not caused by these changes, still open |
| `tests/test_halos_repairs.py` (new, 17 tests) | passed — failure reasons, run_plan per-app errors, Linux filesystem dedupe, unallocated space, Halo edge geometry, image defaults |
| `tests/test_ui_smoke.py`, `tests/test_v04_ui.py` (offscreen Qt) | passed |
| Halo frame render (offscreen Qt, 800×800) | centre pixel alpha 0, top edge alpha 128 — paints edges only |
| Kokoro model download from upstream | 2 files, SHA-256 recorded in the PKGBUILD |

## 2026-11-02 — build 7 features (Windows dev host)

| Test | Result |
|---|---|
| `tests/test_sysinfo.py` (9) + new packaging tests in `test_halos_repairs.py` | passed |
| Full suite | 1596 passed, 5 skipped, same 4 pre-existing failures |
| Task Manager offscreen on this PC's real data | 159/348 processes listed, CPU/memory/disk/network graphs, RTX A1000 found via nvidia-smi (42 °C), dark and light palettes rendered |
| Halo + Music widget QML | parses (`qmlformat`); **not run** — needs Plasma (VM) |
| Halo Dark/Light colour schemes | generated from `ui/theme.py`; test checks the shipped files match; **not seen in Plasma yet** |

## Halo VM / ISO

| Test | Result |
|---|---|
| Build #7 ISO | built and validated (ALL CHECKS PASSED: Task Manager shortcut, Halo/Music widgets, Halo Dark/Light, Alt+`, Kokoro); first copy to Windows failed mid-way (`Invalid cross-device link`, file opened on the Windows side) and left a truncated ISO — the cause of the failed USB write; recopied over SSH via a `.part` file |
| Merge of Workstream B into `halo/arch-rebase` | 1647 Python tests pass (same 4 old failures), 19 browser-client tests pass; pushed to `origin/halo/arch-rebase` |
| Build #6 ISO | built and validated (ALL CHECKS PASSED, incl. Secure Boot step and Kokoro in image); copied to `dist/halo/build6/`, SHA-256 OK; **not boot-tested** (skipped at user's request, build #7 next) |
| Build #6 `saint` package: Kokoro synthesis from the packaged files | **passed** — "2.6s of speech in 14.51s" (first run in a VM container, includes ONNX warm-up; speed on real hardware still to measure) |

| Test | Result |
|---|---|
| ISO build from this branch | see below once the build finishes |
| UEFI boot to desktop | Not run on this branch (last Halo ISO, 2026-10-30, stopped at systemd-firstboot) |
| BIOS boot | Not run |
| Kokoro speaks / Whisper transcribes on Halo | Not run |
| Ollama service + inference | Not run |
| Heroic install from Flathub (live + installed) | Not run |
| Alt+` toggles overlay, Halo at screen edges, notices at bottom | Not run (needs a Wayland session) |

## Not testable on this host
- Swift / iOS (needs macOS — GitHub Actions `mobile-ios.yml`)
- Physical hardware: Secure Boot/MOK, dual boot with Windows + BitLocker, real GPUs

## Workstream B — Halo Remote (branch `saint-mobile`, 2026-11-02, Windows 11 dev host)

What ran, on this Windows machine (Python 3.13 `.venv` + aiohttp 3.11.18, Node 24.20, the Claude app's Chromium):

| Test | Result |
|---|---|
| `pytest tests/test_remote_protocol.py` — schemas, canonical JSON, MACs vs `remote_vectors.json`, tampered/forged/reflected/stale/replayed refused, relay proof, pairing MAC, **unpaired, not-allowed and revoked keys refused** against the real Link `PeerStore` | 19 passed |
| `pytest tests/test_relay.py` — relay on localhost, real WebSockets: login, wrong-key login refused, presence, signed routing untouched, unauthorised device gets no presence/route, revocation drops the route, pairing, pair rate limit, no client↔client, TURN REST credentials | 8 passed |
| `pytest tests/test_remote_host.py` — ask/allow/deny/busy/replace, timeouts, revoked mid-session, answer/ICE only for the live session, input → portal calls with stuck-key release, keysyms, file receive (size/hash/name), one-time pairing codes, device key never created, pipeline + webrtcbin ICE URIs, unit file | 25 passed |
| `node --test "web/remote/test/*.test.mjs"` — JS protocol vs the same vectors (byte-identical canonical JSON and MACs), input/gesture logic, **browser client against the real relay** (pairing, presence, signed request/accept/offer/answer, ICE, forged end ignored, revoke) | 19 passed |
| Browser client, real WebRTC, Chromium: relay serving `web/remote`, `test/fake_host.html` as the PC (canvas video + data channels) | pairing by link (code removed from the URL), video 640×360 @ ~30 fps, click/right-click/scroll with letterbox-correct coordinates, keys as evdev codes, Ctrl+A (found and fixed: modifiers now synced from event flags), PC→client clipboard, 300 KB file with SHA-256 confirmed, stats, Disconnect → signed `session.end`, reconnect after reload, phone-width layout |
| Full `pytest` on `saint-mobile` | 1631 passed, 4 failed — the same tests fail on the base commit 1452e3a (`test_tts_fallback` ×2, `test_v04_selftest`, and a Windows "Access is denied" race in `test_history_stats`); none touch Halo Remote |

Second round (same day) — the desktop app as a client, relay address over Link, codec choice:

| Test | Result |
|---|---|
| `pytest tests/test_remote_desktop_client.py` — relay address from the PC over Link and remembered for when it's offline, PC claiming another account refused, "not set up" message, loopback-only static server (JS MIME, no listing, no `..`) | 5 passed |
| `pytest tests/test_remote_host.py` (+2) — codec from `want.codecs` (H.264 preferred, VP8 fallback, VP8 pipeline), `remote.info` answer and refusal for friends / denied devices | 27 passed |
| `node --test` (+1) — an identity that lends `sharedSecret` (the desktop app) signs exactly like one holding the key | 20 passed |
| **SAINT desktop window, real run on Windows** (PySide6 6.12 Qt WebEngine) ↔ local relay ↔ `test/fake_host.html` in Chromium | connected, live video 640×360 @ 30 fps "direct"; one click and one key arrived at the host; the client offered `codecs: [H264, VP8]` (this Qt WebEngine build does decode H.264). Found and fixed: the window pushed the computer's existing clipboard on connect — it now only sends what is copied while connected |
| Browser page after moving its viewer into `viewer.js` | pairing link + connect + live video + stats still work |
| GitHub Actions `Halo Remote` (ubuntu: pytest, node, relay Docker image build) on 40f2409 | success |
| Full `pytest` | 1638 passed, the same 4 pre-existing failures |

Not run (and why):

- **Host media path** (`modules/remote_desktop/media.py`: xdg-desktop-portal RemoteDesktop/ScreenCast, PipeWire, GStreamer
  `webrtcbin`, libsoup, wl-clipboard, kdialog): implemented, **untested** — needs a Plasma Wayland session (Halo VM). Next:
  enable it on the VM, connect from the browser, then from a phone on cellular through TURN.
- **Swift** (`SaintCore/Remote` + XCTests, `SaintMobile/Remote` with WebRTC): written, **not compiled** — no Swift toolchain
  here. CI (`mobile-ios.yml`: `swift test` + unsigned `.ipa`) will be the first compile.
- **Relay Docker image / compose with coturn and Caddy**: not built (no Docker here); `.github/workflows/halo-remote.yml`
  builds the image.
- **Safari on iPad/iPhone, Chrome on a Chromebook, real touch**: not tried (no devices); gestures are unit-tested only.
- **TURN across networks** (phone on cellular ↔ PC behind NAT): needs a VPS running the relay + coturn.
