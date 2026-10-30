#!/usr/bin/env bash
# Halo archiso profile (mkarchiso). build_iso.sh fills in @VERSION@ and pacman.conf before building.
# shellcheck disable=SC2034

iso_name="Halo"
iso_label="HALO_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="Halo <https://github.com/IONX-Studios/SAINT>"
iso_application="Halo live and installer"
iso_version="@VERSION@"
install_dir="halo"
buildmodes=('iso')
bootmodes=('bios.syslinux' 'uefi.systemd-boot')
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-b' '1M')
file_permissions=(
  ["/usr/local/lib/halo/live-setup"]="0:0:755"
)
