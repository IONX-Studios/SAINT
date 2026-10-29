# SAINTOS — current state

Single place for what SAINTOS **is**, what is **verified**, and what is left. Section 1 is the audit
as found at the start of the 2026-10-29 session and is kept unchanged so the starting point isn't
lost. Section 2 onward is updated as work lands.

Related: [release checklist](SAINTOS_RELEASE_STATUS.md) · [architecture](SAINTOS_ARCHITECTURE.md) ·
[building](BUILDING_SAINTOS.md) · [VM testing](VM_TESTING.md) · [Milestone A code audit](SAINTOS_AUDIT.md)

Status words: **Verified working** (executed, with evidence) · **Implemented, unverified** ·
**Partially implemented** · **Placeholder** · **Missing** · **Blocked**.

---

## 1. Initial audit (2026-10-29, start of session — not updated afterwards)

### 1.1 Executive summary

- **What SAINTOS is:** a Fedora 44 KDE Plasma live/installer ISO built with KIWI from Fedora's own
  `kiwi-descriptions` (pinned commit), plus a SAINTOS layer: the real SAINT application in
  `/opt/saint` with its own offline-installed Python 3.12 venv, launchers, a per-user systemd
  service, SAINTOS branding (os-release, Plymouth, wallpaper, icons, Plasma look-and-feel), Control
  Center, App Installer and a serial-port boot reporter for automated VM tests. It is not a custom
  kernel or a from-scratch distribution, and doesn't need to be.
- **What it can do right now:** nothing bootable yet. All source, scripts and tests exist. SAINT's
  test suite passes on Windows (1501 passed / 4 baseline env failures) and largely on Linux
  (1475 passed / 30 failed, triaged).
- **Bootable?** Not demonstrated. **No SAINTOS ISO existed anywhere** (host or build VM).
- **Last build:** the `SAINTOS-build` VirtualBox VM was left *saved* (suspended) mid-build at
  `e30142c`. Resuming it showed KIWI had completed package install, the SAINTOS configure hook
  (exit 0, including the in-image import check) and the dracut live initrd, and was compressing the
  root filesystem with `mkfs.erofs`.
- **Biggest obstacle found:** that `mkfs.erofs` was writing its ~3 GB output to `/var/tmp`, the live
  session's 2.8 GB RAM overlay (1.8 GB free), so the build would have failed with ENOSPC at the last
  step. Cause: `build_iso.sh` exported `TMPDIR` to the build disk, but `sudo` drops it and KIWI
  defaults to `/var/tmp`.
- **Recommended next milestone:** fix the KIWI temp dir, complete one build, validate the ISO, boot it
  in a disposable VirtualBox VM (UEFI and BIOS) using the existing serial-marker harness.

### 1.2 Repository architecture

The project root `C:\Users\senior\Documents\SAINTOS` is **not** a git repository. It holds the
`SAINT/` repository plus loose session logs from 2026-10-28 (`*-pytest.txt`, `build-vm-prepare*.log`,
`preflight-windows.json`, `saint.bundle`, a git bundle used to move commits into the build VM).

`SAINT/` (git, branch `saintos/milestone-a-audit`, 30 commits ahead of `main`, **not pushed**):

| Path | Role |
|---|---|
| `app.py`, `core/`, `modules/`, `ui/` | SAINT itself (PySide6 desktop assistant). Cross-platform at runtime via `core/platform_caps.py`; Linux backends added in `modules/desktop/` (KWin D-Bus), `modules/voice/espeak.py` |
| `tests/` | 66 pytest files, isolated by `conftest.py` |
| `packaging/windows/` | Existing Windows build (PyInstaller + Inno Setup), untouched |
| `packaging/linux/image/` | ISO pipeline: `build_iso.sh` (entry point), `saintos.xml` (KIWI profile `SAINTOS-Live` on top of Fedora's `KDE-Desktop-Live`), `configure-saintos.sh` (runs inside the image), `saint-runtime.txt` (Python deps → wheelhouse + lock), `validate_iso.sh`, `UPSTREAM` (pinned kiwi-descriptions f44 @ `dfc49a5`), `VERSION` (0.1.0) |
| `packaging/linux/rootfs/` | Files copied verbatim into the image: `/usr/bin/saint`, `saintos-control`, `saintos-apps`, `saintos-diagnose`, user unit `saint.service`, `saintos-boot-report.service`, KDE defaults, first-run |
| `packaging/linux/branding/make_branding.py` | Generates logos, wallpapers, Plymouth watermark at build time |
| `packaging/linux/vm/` | VirtualBox build VM prep, dev VM, `boot_test.ps1` (serial markers), `qemu_boot_test.sh`, `install-test.ks` + `install_test.sh` |
| `.github/workflows/saintos-image.yml` | CI: Linux tests, ISO build in a privileged `fedora:44` container, QEMU boot. Never run (branch not pushed) |

Boot chain (inherited from Fedora via KIWI): El Torito UEFI (shim → signed GRUB) and BIOS (GRUB
i386-pc) → kernel + dracut initrd with `dmsquash-live` → EROFS root at `LiveOS/squashfs.img` →
systemd → SDDM autologin `liveuser` → Plasma 6 Wayland. SAINT runs as the user (`/usr/bin/saint`),
not as root.

### 1.3 Component status (as found)

| Component | Status | Evidence | Missing work |
|---|---|---|---|
| Linux base (Fedora 44 KDE via KIWI) | Implemented, unverified | `saintos.xml`, `UPSTREAM`; KIWI log shows all profiles resolved and packages installed | A finished build |
| Kernel and boot process | Implemented, unverified | KIWI log: kernel 7.2.13-200.fc44, dracut initrd with `dmsquash-live livenet pollcdrom` built | Boot test |
| Root filesystem | Partially implemented | Image root prepared (9.2 GB) | EROFS packing would fail (temp space) |
| Bootloader | Implemented, unverified | KIWI log: shim/GRUB EFI images, BIOS image, `GRUB_DISTRIBUTOR=SAINTOS` | ISO + validation |
| Init and system services | Implemented, unverified | `saintos-boot-report.service` enabled in the image (KIWI log) | Boot test |
| Desktop (Plasma 6, Wayland) | Implemented, unverified | Inherited `KDE-Desktop` profile; SAINTOS look-and-feel files | Boot test |
| SAINT integration | Implemented, unverified (build-time verified) | In-image venv installed offline from a 108-package lock; import check of PySide6, numpy, onnxruntime, faster_whisper, kokoro_onnx, misaki, spaCy model, keyring passed inside the image root | Launch in a booted system |
| Networking | Implemented, unverified | NetworkManager from Fedora profiles | Boot test |
| Audio and device support | Implemented, unverified | PipeWire from Fedora profiles | Manual test |
| Package management | Implemented, unverified | dnf5 + Fedora repos, no third-party repo | — |
| Build automation | Partially implemented | `build_iso.sh` runs to the last KIWI step | Temp-dir bug |
| ISO generation | **Missing (no artifact)** | No `*.iso` built | — |
| VM boot testing | Implemented, unverified | `boot_test.ps1`, `qemu_boot_test.sh` never run against a SAINTOS ISO | Run them |
| Persistence / installation | Implemented, unverified | Anaconda live installer rebranded; `install_test.sh` + kickstart | Run after live boot passes |
| CI | Implemented, unverified | Workflow file only | Push the branch |

### 1.4 Build and test history (reconstructed from git, logs and the build VM)

| When (2026-10-28) | What | Result |
|---|---|---|
| 09:11 | Windows baseline `pytest` (before SAINTOS changes) | 1428 passed, 4 failed (env: no Kokoro/faster-whisper/winrt in test venv) |
| 09:22–11:22 | Re-runs after each change set | 1501 passed, 4 failed (same baseline failures) |
| 10:49–12:40 | `build_vm_prepare.sh` ×3 in VM `SAINTOS-build` | Ends `SAINTOS-BUILD-VM-READY`; tools updated to rpm 6.0.2, dnf5 5.4.6, KIWI 11.1.1 |
| ~15:00–16:04 (VM clock) | Linux pytest in the build VM | 1475 passed, 30 failed, 6 skipped |
| 11:00–13:08 | Several KIWI attempts, each fixed in a commit: SELinux `kiwi_t` transition (`8accdb3`), rpmdb labels (`92ff7ee`, `5511e45`), policy-module skew (`e30142c`) | Each failed at a later stage than the one before |
| 17:10–17:35 (VM clock) | Build at `e30142c` | Reached `mkfs.erofs`; VM then saved. No ISO |

Environment: Windows 11 Pro host (i7-13700HX, 24 threads, 31.7 GB RAM), VirtualBox 7.0.20 running on
Hyper-V (no WSL, no native QEMU). The build VM boots the stock Fedora KDE 44 live ISO; its 120 GB
virtual disk is mounted at `/var/tmp/saintos-build`; SSH on `127.0.0.1:2223` with a dev key.

### 1.5 Bootability assessment (as found)

1. **Can the project produce a bootable ISO?** Not yet: the build fails at the final packing step.
2. **What's missing?** Only the KIWI temp-dir fix. Then a completed build, validation and a boot test.
3. **Shortest route:** fix → rebuild in the existing build VM (package cache is warm) → copy the ISO
   to the host → `validate_iso.sh` → `boot_test.ps1` in a fresh VirtualBox VM.
4. **Feasible here:** all of it. The build VM, VirtualBox and the boot harness are present.
5. **Needs Linux / privileges / virtualization:** the KIWI build (Fedora, root, loop devices) runs
   in the build VM. Boot tests need VirtualBox (present). CI needs the branch pushed (your call).
6. **Next action:** `--temp-dir` on the KIWI command line.

### 1.6 Roadmap (as found)

- **P0 (first VM boot):** KIWI temp dir; finished ISO; validation; UEFI live boot to the desktop with
  SAINT's self-test passing.
- **P1 (usable live environment):** BIOS boot; SAINT GUI actually opens in the session; networking;
  audio; first-run flow; known Linux test failures (Windows-path test guards, PortAudio JACK abort
  at exit).
- **P2 (SAINT on the OS):** Wayland input backend (ydotool/portal); Control Center end-to-end; model
  download after boot; wake word under load.
- **P3:** Installation to a virtual disk + reboot (`install_test.sh`), persistence, real-hardware and
  dual-boot test, Secure Boot verification, in-place SAINT updates, os-release drift, CI on push.

---

## 2. Progress (2026-10-29)

_Updated as work lands; see section 3 for the current verified state._

| Step | Result |
|---|---|
| KIWI temp dir on the build disk (`325f328`) | Committed; rebuild started |

## 3. Current status

_Pending the rebuild._
