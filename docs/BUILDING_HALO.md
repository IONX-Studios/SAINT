# Building Halo (Arch Linux)

Halo is SAINT's operating system from v0.2 on: Arch Linux + KDE Plasma 6 (Wayland) + SAINT, built with
archiso. It replaces the Fedora/KIWI SAINTOS build (`packaging/linux/image`, kept until Halo has passed
its boot and install tests). Plan: [plan.md](../plan.md) §2b.

## Build

On any x86_64 Linux host with podman or docker and ~25 GB free:

```bash
packaging/arch/build_in_container.sh --workdir /var/tmp/halo-build
```

On Arch itself (as root): `packaging/arch/build_iso.sh --workdir /var/tmp/halo-build`.

Output in `dist/halo/`: `Halo-x86_64.iso`, `SHA256SUMS`, `BUILD_INFO.json`, `Halo-x86_64.packages`,
`AUR_COMMITS`, `VALIDATION.txt`.

Options: `--allow-dirty` (build tracked local changes), `--live-mirrors` (today's Arch mirrors instead of
the pinned snapshot: faster, not repeatable), `--no-validate`.

## What's where

| Path | Role |
|---|---|
| `packaging/arch/VERSION` | Halo version (also every Halo package's `pkgver`) |
| `packaging/arch/ARCH_SNAPSHOT` | Arch Linux Archive date the image and the AUR builds use |
| `packaging/arch/profile/` | archiso profile: boot entries (BIOS syslinux, UEFI systemd-boot, no menu), package list, live-only files |
| `packaging/arch/pkgbuilds/saint` | `/opt/saint`: the app, a uv-managed CPython 3.12 and a venv from `saint-runtime.lock` |
| `packaging/arch/pkgbuilds/halo-branding` | os-release (applied by a pacman hook), artwork, Plymouth, Plasma and SDDM defaults |
| `packaging/arch/pkgbuilds/halo-calamares-config` | the installer: Btrfs subvolumes, GRUB + os-prober, live cleanup |
| `packaging/arch/build_iso.sh` | builds the `[halo]` repo (ours + `paru-bin` and `calamares` from the AUR) and runs mkarchiso |
| `packaging/arch/validate_iso.sh` | structural checks of an ISO (root) |
| `tests/test_halo_image.py` | static checks that run anywhere, in the normal test suite |

## Live session and installed system

- The live ISO logs `liveuser` (no password) straight into Plasma. That user, its autologin, NOPASSWD
  sudo and polkit rule are created at boot by `halo-live.service`, only when the kernel command line has
  `archisobasedir=`; none of it is in the image, so the installer can't copy it to a disk.
- "Install Halo" (desktop and menu) runs Calamares. Its last steps remove the live tools, write Arch's
  normal kernel preset, create the pacman keyring and install GRUB with os-prober (dual boot).

## Known gaps

- **Secure Boot:** Arch has no Microsoft-signed boot loader. Boot Halo with Secure Boot off until the
  shim/MOK work in plan.md §14 Q11 is decided.
- **Updates:** installed systems follow Arch's live mirrors; SAINT and the branding come from the ISO
  until a hosted `[halo]` repository exists.
- **AUR packages** (`paru-bin`, `calamares` and its AUR dependencies) are built from the AUR's current
  PKGBUILDs; the commits used are recorded in `AUR_COMMITS` and `BUILD_INFO.json`.
