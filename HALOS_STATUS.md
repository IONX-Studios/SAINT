# Halo OS (HalOS) — status

Last updated: 2026-11-02 · branch `halo/arch-rebase` · Halo version 0.2.0

This file says what works, what is broken and what is missing, from the source code and from tests that
actually ran. "Verified" means a test or a run on real/VM hardware; "implemented" means code exists but has
not been exercised on a running Halo system yet.

## Architecture (from the code, 2026-11-02)

| Layer | What it is | Where |
|---|---|---|
| Base | **Arch Linux** (snapshot 2026/10/28) + KDE Plasma 6 on Wayland, built with **archiso**. Rebased from Fedora 44/KIWI on 2026-10-30 (user decision recorded in `plan.md` §14). The Fedora pipeline is still in the tree (`packaging/linux/image`) but is no longer the target. | `packaging/arch/` |
| Packages | `[halo]` local repo built per ISO: `saint`, `halo-branding`, `halo-calamares-config`, plus AUR `paru-bin`, `calamares` | `packaging/arch/pkgbuilds/` |
| Installer | Calamares (Btrfs subvolumes, GRUB + os-prober for dual boot) | `halo-calamares-config` |
| Secure Boot | shim-signed (AUR) + MOK enrolment (commit f655c53); untested on hardware | `build_iso.sh` |
| SAINT | Python 3.12 (uv-managed) + PySide6 6.12 venv in `/opt/saint`, user systemd service, single instance, D-Bus `org.saintos.Saint` | `app.py`, `core/`, `modules/`, `ui/` |
| AI | Ollama (Arch `ollama` package, `saint-ollama-model.service` pulls a small default model) or OpenAI-compatible | `modules/ai/providers.py` |
| Voice | Kokoro-82M on onnxruntime (TTS), faster-whisper base.en (STT), ONNX wake word | `modules/voice/` |
| Store | One search over SAINT's picks, Arch repos, Flathub, AUR (paru) and Windows apps (Wine) | `core/store.py`, `core/saintos_apps.py`, `ui/pages/store.py` |
| Desktop integration | KWin script `saint-shell` (Wayland placement, top-edge open), KWin effect `saint-star`, Plasma wallpaper + widget, KGlobalAccel Alt+` | `packaging/linux/rootfs/usr/share/` |
| Phone / remote | SAINT Link (Noise IK/XXpsk3 over TCP 8765, UPnP, Tailscale addresses), iOS app `mobile/ios` (SwiftUI, ~13.7k lines), KDE Connect, KRdp | `modules/link`, `mobile/` |
| Build host | VirtualBox VM `SAINTOS-build` (Fedora live, podman, SSH 127.0.0.1:2223, repo shared in); boot tests in VMs `Halo`, `SAINTOS-*` | `packaging/linux/vm/` |
| Tests / CI | ~1,520 pytest tests (Windows host); GitHub workflows `saintos-image`, `windows-release`, `mobile-ios` | `tests/`, `.github/workflows/` |

## ISO readiness

| Image | Built | Boot-tested | Result |
|---|---|---|---|
| SAINTOS 0.1 (Fedora 44) `dist/SAINTOS-x86_64.iso` | 2026-10-30 | UEFI + BIOS VM | Boots to Plasma. **This is the image that shows "SAINTOS" at boot and lacks KDE Connect** — it predates Halo. |
| Halo 0.2 `dist/halo/Halo-x86_64.iso` | 2026-10-30 16:27 | UEFI VM | **Stopped at a `systemd-firstboot` timezone prompt** (screenshot `dist/boot-test-Halo-uefi/screen-0606s.png`). Fixed in commit 818423a; not rebuilt/retested since. |
| Halo 0.2 `dist/halo/Halo-x86_64-preview.iso` | 2026-10-30 17:17 | not tested | — |
| Halo 0.2 from this branch | building — see `HALOS_TEST_RESULTS.md` | — | — |

## Reported problems: cause found in the code and state

| Problem | Cause (from the source) | State |
|---|---|---|
| Boot shows SAINTOS | Fedora 0.1 image. Halo image is branded Halo (os-release, Plymouth, boot entries). | Halo branding exists; verify on next boot test |
| SAINT says Kokoro, voice is robotic | Kokoro model files were **not in the image**; SAINT downloaded them in the background and meanwhile spoke with **espeak-ng**, while the Control Center printed the *configured* backend ("kokoro"). | **Fixed (implemented):** model ships in `/opt/saint` with pinned SHA-256; the package build fails unless Kokoro really synthesises; Voice page reports the engine actually speaking + Test buttons |
| Health checks report things installed that aren't | Voice line read config only. Ollama detection already queries the live API (`detect_ollama`). | Voice fixed; full dashboard audit is remaining work |
| `flatpak install --user` fails (Heroic) | Live ISO's write overlay is archiso's default **256 MB**; Heroic + its runtimes need ~1 GB → "No space left". Error message gave no reason. | **Fixed (implemented):** `cow_spacesize=50%`; every failed step now says why (space, network, not found, password prompt, lock, signature) in the log, the Store and the notification. Heroic install on Halo still to verify in VM |
| Store search needs Enter; clearing doesn't reset | `returnPressed` only | **Fixed:** live debounced search (350 ms), instant reset on clear, stale results discarded |
| Apps flashes Easy Install then jumps to Store | Halo page redirected the "Apps" row to the Store | **Fixed:** Apps stays on Easy Install, with a "Search every app in the Store…" button |
| Menu inside a menu | SAINT sidebar + Halo's own list side by side | **Fixed:** SAINT's sidebar folds to icons while Halo is open; Back button at the top |
| Notifications in the middle of the screen | `ActionNotice` used `move()`, which Wayland ignores, so KWin centred it | **Fixed (implemented):** placed through the `saint-shell` KWin script like the Halo |
| Halo is a cross in the middle | Halo = 4 thin edge windows placed with `move()`; on Wayland unplaced windows are centred, so they overlap into a cross | **Fixed (implemented):** on Wayland one click-through, screen-sized window per monitor paints only the edge bands (verified offscreen: centre pixel transparent) |
| Alt+` acts like Windows-Tab / does nothing | Plasma binds Alt+` to KWin "Walk Through Windows of Current Application"; KWin holds the key so SAINT's binding is dropped | **Fixed (implemented):** image ships `/etc/xdg/kglobalshortcutsrc` releasing it to SAINT; ISO validation checks it |
| Storage numbers wrong | On Linux every psutil mount was listed: the live RAM overlay as `/`, the ISO, each Btrfs subvolume again, all labelled "/:"; no unallocated space | **Fixed:** each filesystem once (by device), images/RAM filesystems skipped, live session labelled, reserved space and unallocated disk space (lsblk) shown separately |
| KDE Connect missing | Fedora 0.1 image. Halo ships `kdeconnect`. | Verify in Halo VM |
| Ollama missing | Fedora 0.1 image did not install it. Halo ships `ollama` + a model-pull service. | Verify service + inference in Halo VM |

## Not built yet (from the request)

Universal installation assistant (URL/GitHub → plan → verify), browser/desktop/iOS remote desktop
(Workstream B — design in `docs/REMOTE_DESKTOP.md`), Halo Light/Dark theme pair for Plasma, SAINT Task
Manager app (Ctrl+Shift+Esc), floating Halo widget, music widget (a Spotify mini player exists), first-run
wizard beyond the installer, OS Expansion Agent upgrades (an "Evolve" page exists: `core/evolve.py`),
dependency manager with declared per-subsystem dependencies. See `HALOS_ROADMAP.md`.

## Known limitations

- Nothing in this list has been tested on physical hardware; Secure Boot/MOK and dual boot with BitLocker need the user's laptop.
- iPhone notification mirroring of *other apps* is not possible through Apple's public APIs (ANCS over BLE is the only route and is experimental on Linux).
- iOS builds need macOS (GitHub Actions `mobile-ios.yml`); this Windows host can't compile Swift.
