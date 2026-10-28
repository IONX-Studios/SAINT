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
| SAINT regression tests recorded | **Verified (Windows)** | see above. Linux run: CI workflow written, not run (no GitHub access from this machine). |
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
