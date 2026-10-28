#!/usr/bin/bash
# Run over SSH inside the SAINTOS build VM (a Fedora live session in VirtualBox):
#   1. puts a filesystem on the VM's *empty virtual* build disk and mounts it at /var/tmp/saintos-build
#   2. installs the image-build tools (kiwi, dnf5 plugins, xorriso, python3.12)
#
# Safety: refuses to run outside a VirtualBox VM, and only formats a disk that is
#   - reported as model "VBOX HARDDISK", and
#   - has no partitions and no filesystem signature, and
#   - is at least 80 GB.
# Any other state stops the script with an explanation.
set -euo pipefail
[ "$(systemd-detect-virt)" = oracle ] || { echo "not a VirtualBox VM; refusing" >&2; exit 1; }
MNT=/var/tmp/saintos-build

if ! mountpoint -q "$MNT"; then
  disk=""
  for d in /sys/block/sd?; do
    n=$(basename "$d")
    model=$(cat "$d/device/model" 2>/dev/null | xargs)
    size_gb=$(( $(cat "$d/size") * 512 / 1024 / 1024 / 1024 ))
    parts=$(find "$d" -maxdepth 1 -name "${n}[0-9]*" | wc -l)
    sig=$(sudo wipefs --no-act --noheadings "/dev/$n" 2>/dev/null | wc -l)
    if [ "$model" = "VBOX HARDDISK" ] && [ "$parts" -eq 0 ] && [ "$sig" -eq 0 ] && [ "$size_gb" -ge 80 ]; then
      disk="/dev/$n"; break
    fi
  done
  [ -n "$disk" ] || { echo "no empty VirtualBox build disk (>= 80 GB) found; lsblk:" >&2; lsblk >&2; exit 1; }
  echo "formatting empty virtual build disk $disk"
  sudo mkfs.xfs -q -L saintosbuild "$disk"
  sudo mkdir -p "$MNT"
  sudo mount "$disk" "$MNT"
  sudo chown "$(id -u):$(id -g)" "$MNT"
fi
# SELinux: a filesystem made in a live session starts unlabeled. rpm 6's confined database helper
# (rpmdb_t) can't create its database under unlabeled_t or user_tmp_t (what unconfined root gets
# under tmp_t), so `rpm --root <image> --initdb` (and KIWI) fails with "can't create transaction
# lock ... (Permission denied)". Under var_lib_t new directories inherit var_lib_t and the database
# becomes rpm_var_lib_t. build_iso.sh labels its workspace the same way.
sudo chcon -R system_u:object_r:var_lib_t:s0 "$MNT"
df -h "$MNT"

# Image-build tools. The live overlay is RAM-backed: keep dnf's cache on the build disk.
sudo mkdir -p "$MNT/dnf-cache"
sudo dnf install -y --setopt=cachedir="$MNT/dnf-cache" \
  kiwi kiwi-systemdeps distribution-gpg-keys git python3.12 xorriso erofs-utils desktop-file-utils dracut
python3.12 -m ensurepip --user >/dev/null 2>&1 || true
python3.12 -m pip --version
kiwi-ng --version || true
echo SAINTOS-BUILD-VM-READY
