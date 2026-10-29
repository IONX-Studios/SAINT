# Building SAINTOS

SAINTOS 0.1 is built with **KIWI** from Fedora's own image descriptions (`kiwi-descriptions`, branch
`f44`, commit pinned in `packaging/linux/image/UPSTREAM`), profile `SAINTOS-Live` =
Fedora's `KDE-Desktop-Live` + the SAINTOS layer. One command does everything:

```bash
packaging/linux/image/build_iso.sh --workdir /var/tmp/saintos-build/work --dist /var/tmp/saintos-build/dist
```

Output in `--dist` (default `<repo>/dist`):

| File | |
|---|---|
| `SAINTOS-x86_64.iso` | the live + installer ISO (UEFI and BIOS) |
| `SHA256SUMS` | checksum of the ISO |
| `BUILD_INFO.json` | SAINT commit, upstream commit, KIWI version, size, SHA-256, build time |
| `SAINTOS-x86_64.packages` | every RPM in the image, with versions |
| `saint-runtime.lock` | exact Python packages in `/opt/saint/venv` |
| `RELEASE_NOTES.md`, `VALIDATION.txt` | release notes; output of `validate_iso.sh` |

The ISO is ~3 GB and is **not** committed (`dist/` is in `.gitignore`).

## Requirements

- **Fedora 44 (or newer), x86_64**, as root or with sudo that doesn't prompt. KIWI's Fedora
  descriptions use the host's dnf5, rpm and Fedora GPG keys, so other distributions are not
  supported as build hosts (use the container below instead).
- **≥ 30 GB free** in the work directory (checked). A full build writes ~20 GB.
- **≥ 8 GB RAM**; 4+ CPUs (dracut and EROFS compression are CPU-bound).
- Network access to Fedora mirrors, PyPI and GitHub (spaCy model).
- Packages:

  ```bash
  sudo dnf install -y kiwi kiwi-systemdeps distribution-gpg-keys git python3.12 xorriso erofs-utils desktop-file-utils dracut
  ```

SELinux may stay **enforcing**: `build_iso.sh` relabels only its own workspace (see the findings
table in [SAINTOS_RELEASE_STATUS.md](SAINTOS_RELEASE_STATUS.md#build-environment-findings-2026-10-28)).

## Option A — build VM on a Windows host (what's used on the dev laptop)

The Windows laptop has no WSL or native QEMU, so builds run in a VirtualBox VM booted from the
stock Fedora KDE 44 live ISO, with a separate empty virtual disk for the build. Nothing on the
physical disk is touched.

1. **Create the VM** (once). `create_vm.ps1` makes the VM, an empty virtual disk and the shared folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File packaging\linux\vm\create_vm.ps1 -Iso "$env:USERPROFILE\Downloads\Fedora-KDE-44\Fedora-KDE-Desktop-Live-44-1.7.x86_64.iso" -Name SAINTOS-build -MemoryMB 14336 -Cpus 8 -DiskMB 122880 -SshPort 2223
   ```

2. **Start it** and let it boot to the live desktop (`VBoxManage startvm SAINTOS-build`).
3. **In the VM's Konsole** (the live user has passwordless sudo):

   ```bash
   sudo mkdir -p /mnt/s && sudo mount -t vboxsf -o ro saint /mnt/s
   bash /mnt/s/packaging/linux/vm/build_vm_prepare.sh       # formats ONLY the empty VBOX disk, installs KIWI
   git clone /mnt/s /var/tmp/saintos-build/SAINT
   cd /var/tmp/saintos-build/SAINT && git checkout saintos/milestone-a-audit
   packaging/linux/image/build_iso.sh --workdir /var/tmp/saintos-build/work --dist /var/tmp/saintos-build/dist
   ```

   `build_vm_prepare.sh` refuses to run outside VirtualBox and only formats a disk that is a
   `VBOX HARDDISK`, ≥ 80 GB, with no partitions or filesystem. On later boots it re-mounts the
   existing build disk instead of formatting it. Re-run it after every VM boot, because the live
   session forgets installed packages.

4. **Copy the ISO to Windows**, either through SSH (below) or by copying it into a read-write
   shared folder.

**SSH (optional, for automation).** Port forward `127.0.0.1:2223 → 22` is set up by `-SshPort`. In
the VM, add your public key to `~liveuser/.ssh/authorized_keys` and `sudo systemctl start sshd`.
Then from Git Bash on Windows:

```bash
ssh -p 2223 liveuser@127.0.0.1
git push ssh://liveuser@127.0.0.1:2223/var/tmp/saintos-build/SAINT HEAD:refs/heads/incoming
scp -P 2223 liveuser@127.0.0.1:/var/tmp/saintos-build/dist/SAINTOS-x86_64.iso dist/
```

(then `git merge --ff-only incoming` inside the VM's checkout). Saving the VM state (`VBoxManage
controlvm SAINTOS-build savestate`) keeps the live session, its packages and SSH setup for the next day.

## Option B — any Linux host with Docker or Podman

What `.github/workflows/saintos-image.yml` runs. KIWI needs loop devices, so the container is privileged:

```bash
docker run --rm --privileged -v /dev:/dev -v "$PWD:/src" -w /src fedora:44 bash -euxc '
  dnf install -y kiwi kiwi-systemdeps distribution-gpg-keys git python3.12 xorriso erofs-utils desktop-file-utils dracut
  git config --global --add safe.directory /src
  packaging/linux/image/build_iso.sh --workdir /var/tmp/saintos-build --dist /src/dist'
```

Not yet run (CI has never been triggered; the branch isn't pushed).

## Option C — a Fedora 44 machine or VM

Install the packages above, clone the repository, run `build_iso.sh`.

## What the build does

`build_iso.sh` steps (each fails early with a clear message):

1. **Host checks**: Fedora x86_64, root, tools, Fedora GPG key, free space; labels the workspace for SELinux.
2. **Source**: records the SAINT commit; refuses local changes unless `--allow-dirty`.
3. **Upstream**: clones `kiwi-descriptions` and checks out the pinned commit.
4. **Stage**: `git archive` of SAINT → `/opt/saint/app` (no tests, iOS app or Windows packaging);
   builds a Python 3.12 wheelhouse and an exact lock (refuses PyTorch/CUDA); import-checks the
   runtime; copies the rootfs overlay and generated branding; adds `saintos.xml` and a `config.sh`
   hook into a *copy* of the descriptions.
5. **KIWI** `system build --type=iso --profile=SAINTOS-Live`: installs packages, runs `config.sh`
   → `configure-saintos.sh` (offline venv install, import check, os-release, icons, wallpaper,
   Plymouth, services; any failure fails the build), builds the dracut live initrd, packs the root
   as EROFS, writes GRUB (EFI + BIOS) and the hybrid ISO.
6. **Release files** in `--dist` (table above).
7. **Validate**: `validate_iso.sh` (structural checks; does not boot).

Options: `--allow-dirty` (build uncommitted tracked changes), `--keep-work` (keep KIWI's image root
for inspection), `--no-validate`.

Timing on the dev laptop's build VM (8 vCPU): staging ~2 min, KIWI package install ~20 min (less
with a warm `kiwi-cache`), dracut ~3 min, EROFS ~10 min.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `mkfs.erofs` fails / "No space left on device" near the end | KIWI's temp dir was `/var/tmp`, the live session's ~2.8 GB RAM overlay. Fixed: `build_iso.sh` passes `--temp-dir "$WORK/tmp"` (`325f328`). |
| EROFS step crawls (MBs per minute), `kswapd0` busy, SSH unresponsive | `mkfs.erofs -Efragments` keeps an unlinked work file in `$TMPDIR` (default `/tmp`, RAM). Grows to GBs and thrashes the page cache. Fixed: KIWI runs in a private mount namespace whose `/tmp` is `$WORK/hosttmp` on the build disk (`TMPDIR` can't be used: KIWI passes it into the chroot, where dracut fails with "Invalid tmpdir"). The fd still *shows* as `/tmp/tmp.XXXX (deleted)` (the path inside KIWI's namespace), so check the device instead: `sudo stat -L -c %d /proc/$(pgrep -x mkfs.erofs)/fd/4` must equal `stat -c %d $WORK`, not `stat -c %d /tmp`. |
| `%sysusers(...) scriptlet failed, exit status 127` | SELinux `kiwi_t` can't transition to `rpm_script_t`; KIWI is started via `python3` (`8accdb3`). |
| `rpm: can't create transaction lock (Permission denied)` | Workspace label; `build_iso.sh` labels it `var_lib_t` (`5511e45`). |
| `setfiles: Could not set context ... Invalid argument` | Host SELinux policy modules older than the image's; `build_vm_prepare.sh` upgrades them (`e30142c`). |
| pip downloads PyTorch / `nvidia-*` | A dependency pulled `spacy-curated-transformers`; `saint-runtime.txt` lists misaki's deps individually; the lock check refuses torch. |
| `the working tree has N modified tracked file(s)` | Commit first, or `--allow-dirty` for a test build (recorded in `BUILD_INFO.json`). |
| `KIWI failed (exit N)` | Full log: `$WORK/kiwi.log`; KIWI's own: `$WORK/out/build/image-root.log` (search for `err start`). |
| `SAINT's Python runtime doesn't import in the lock venv` | A wheel failed to build or a pin conflicts; the traceback is just above. |

## Changing the image

- **Packages** → `packaging/linux/image/saintos.xml`.
- **Python packages for SAINT** → `packaging/linux/image/saint-runtime.txt` (the lock is generated).
- **Files in the OS** → `packaging/linux/rootfs/` (copied as-is).
- **Image configuration** (users, services, branding steps) → `packaging/linux/image/configure-saintos.sh`.
- **Artwork** → `packaging/linux/branding/make_branding.py`.
- **Version** → `packaging/linux/image/VERSION`. **Fedora / upstream bump** → `UPSTREAM`.

Then rebuild and re-run the [VM tests](VM_TESTING.md).
