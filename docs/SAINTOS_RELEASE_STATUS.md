# SAINTOS release status

Living record of what is **verified**, with evidence, for the SAINTOS ISO. Anything not listed as
verified hasn't been shown to work. Updated 2026-10-28.

## Release candidate

| | |
|---|---|
| Version | SAINTOS 0.1.0 (Fedora Remix 44, KDE Plasma 6) |
| Branch | `saintos/milestone-a-audit` (not merged to `main`) |
| Image architecture | KIWI + Fedora's `kiwi-descriptions` f44 @ `dfc49a5`, profile `SAINTOS-Live` = `KDE-Desktop-Live` + SAINTOS layer ([architecture](SAINTOS_ARCHITECTURE.md#image-architecture-decided-2026-10-28)) |
| Build command | `packaging/linux/image/build_iso.sh --workdir /var/tmp/saintos-build/work --dist /var/tmp/saintos-build/dist` |
| Build environment | VirtualBox VM `SAINTOS-build` on the Windows 11 dev laptop: Fedora 44 KDE live session, 8 vCPU, 14 GB RAM, 120 GB virtual build disk, KIWI 11.1.1 |
| ISO | _build in progress_ |

## Definition of done

| Criterion | Status | Evidence |
|---|---|---|
| Original SAINT + Windows behaviour intact | **Verified (tests)** | Windows suite: 1500 passed, 3 failed, 3 skipped. The 3 failures are the same environment failures as the pre-change baseline (no Kokoro/faster-whisper/winrt installed in the test venv). |
| Real Fedora-based image definition | **Done** | `packaging/linux/image/` |
| Distinct SAINTOS identity | **Done (in image)** | os-release, wallpaper, icons, Plymouth theme, Breeze Dark + SAINT orange, Control Center |
| SAINT integrated as app + user service | **Done (in image)** | `/opt/saint`, `/usr/bin/saint`, `saint.desktop`, `saint.service` (user) |
| Reproducible build from documented source | **Done** | pinned upstream commit, Python lock file, `BUILD_INFO.json` |
| Non-empty bootable x86-64 ISO generated | _in progress_ | |
| Checksum + build metadata | _pending build_ | |
| ISO structurally validated | _pending build_ | `validate_iso.sh` |
| Booted in a VM | _pending build_ | `boot_test.ps1` (serial markers) |
| Live desktop verified | _pending_ | |
| Installed to a disposable virtual disk | _pending_ | `install_test.sh` |
| Installed OS booted and launched SAINT | _pending_ | |
| SAINT regression tests recorded | **Verified (Windows + Linux)** | Windows: 1501 passed / 4 baseline failures. **Linux (Fedora 44, Python 3.12, SAINTOS's exact runtime lock, offscreen Qt): 1475 passed, 30 failed, 6 skipped** at `4dc9909` (see below). CI workflow written, not run. |
| Dual-boot procedure documented | **Done** | [DUAL_BOOT_INSTALLATION.md](DUAL_BOOT_INSTALLATION.md) |
| No physical disk repartitioned or formatted | **Verified** | Only VirtualBox virtual disks (`.vdi` files) were formatted, inside VMs, by scripts that refuse any other disk. |
| ISO at a verified path | _pending_ | |

## Known defects and limitations

- Fixed after the first build: `OpenAICompatibleProvider.test_connection()` reported success without
  contacting anything (`89cf48a`), and hosted-provider API keys sat in plaintext in `config.json`.
  They now go to the OS secret store (Credential Manager / Secret Service), and old values are
  migrated out on load. If no secret store works, the key stays in the file with a logged warning.
- No budgets or cost accounting for hosted models yet.
- Keyboard/mouse automation on Wayland (ydotool or the portal) isn't set up in the image.
- Reading other apps' notifications has no Linux equivalent (blocked).
- KWin window control is unit-tested against a simulated KWin. The real-session test runs only inside Plasma.
- `/etc/os-release` branding can revert after a `fedora-release` package update.
- No in-place updater for `/opt/saint`. Updating means a newer ISO.
- Not tested on physical hardware.

## Build-environment findings (2026-10-28)

Problems hit while building on a Fedora 44 KDE live session in VirtualBox, and how each was fixed.
Each fix is in the scripts, so a fresh build host doesn't need them redone by hand.

| Symptom | Cause | Fix |
|---|---|---|
| pip resolver downloads PyTorch + ~3 GB of `nvidia-*` wheels | `misaki[en]` → `spacy-curated-transformers` → torch | List misaki's English dependencies individually; pin spaCy 3.8.x; `build_iso.sh` refuses a lock with torch/nvidia (`919a323`) |
| `%sysusers(...) scriptlet failed, exit status 127`, bootstrap aborts | SELinux: `kiwi-selinux` confines `kiwi-ng` as `kiwi_t`, which may not transition to `rpm_script_t` (AVC `denied { transition }`) | Start KIWI through `/usr/bin/python3`, so it runs as ordinary unconfined root (`8accdb3`) |
| `rpm: can't create transaction lock ... (Permission denied)` | SELinux: rpm 6's `rpmdb_t` can't create its database under `user_tmp_t`/`unlabeled_t`, the labels a `/var/tmp` workspace or fresh disk gives KIWI's image root | Label the workspace `var_lib_t` (`5511e45`). Verified with `sesearch` and `rpm --root … --initdb` |
| VM can't reach the host's `127.0.0.1` bootstrap server | VirtualBox 7 NAT has `localhost-reachable` off by default | Read-only transient shared folder instead |
| Typed commands arrive mangled | `keyboardputstring` sticks Shift under Hyper-V | `vm_type.sh` types one character at a time; commands kept lowercase |

SELinux stayed **enforcing** throughout. The build VM's tools were updated from the April live media
to current Fedora 44 updates (rpm 6.0.2, dnf5 5.4.6, selinux-policy 44.11).

## Linux test run (Fedora 44, 2026-10-28)

`QT_QPA_PLATFORM=offscreen pytest -q` in the build VM, in a venv with SAINTOS's locked runtime
(`saint-runtime.lock`) plus pytest: **1475 passed, 30 failed, 6 skipped** (7 min 41 s). After the
run, the interpreter aborted at exit: PortAudio's JACK host API asserted in `Pa_Terminate`
(`pa_jack.c:867`). That's a real Linux issue: SAINT could crash on exit on systems with
pipewire-jack.

| Group | Tests | Verdict |
|---|---|---|
| Windows paths and tools assumed (`C:\`, `D:\`, `cmd`, `SAINT.exe`, `%LOCALAPPDATA%`, `os.startfile`, Start-menu shortcut, junctions) | test_paths_frozen ×5, test_files ×5, test_session_0930::open_exact_path, test_session_1005::profiles, test_spotify_0929::autostart_shortcut, test_steam ×2, test_automation::shell_command, test_tools_and_safety::app_resolution | Tests need platform guards. Product behaviour on Linux is different by design (XDG paths, no `.exe`) |
| Keyboard/mouse on Linux | test_tools_and_safety::desktop_key_validation, test_short_term::scroll, test_session_0928::media_keys | **Real gap**: no Linux input backend yet (ydotool / RemoteDesktop portal) |
| Fallback voice | test_tts_fallback ×4 | These 4 fail on Windows too (pre-existing baseline). The Linux fallback voice gap they pointed to is now filled: espeak-ng engine `modules/voice/espeak.py` (`29f4889`) |
| Learn-by-watching offers | test_learning ×2, test_short_term::learning_by_watching | Expected: the demonstration recorder is Windows-only, so SAINT doesn't offer it |
| Game detection | test_game_mode::classifies_steam_games | Investigate (Windows paths likely) |
| Wake-word speed | test_wake_and_voice::real_model_quiet_on_silence: `avg_infer_ms` 50 vs the test's < 40 limit | Not a model difference (same onnxruntime 1.31.1, and the silence score check passed): the build VM was running KIWI at the same time. Re-run on an idle machine |
