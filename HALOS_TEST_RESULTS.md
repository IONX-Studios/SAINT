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
