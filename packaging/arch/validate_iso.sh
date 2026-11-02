#!/usr/bin/env bash
# Structural checks of a Halo ISO (no boot): BIOS + UEFI boot images, boot entries, initramfs, and
# the image root (identity, SAINT and its Python runtime, installer, desktop, live-only safety).
#
#   packaging/arch/validate_iso.sh dist/halo/Halo-x86_64.iso
#
# Needs root (loop mounts, a read-only chroot for SAINT's import check), xorriso, lsinitcpio.
# Prints PASS/FAIL per check and exits 1 if any check failed.
set -uo pipefail
ISO="${1:?usage: validate_iso.sh ISO}"
[ "$(id -u)" -eq 0 ] || { echo "validate_iso: run as root" >&2; exit 2; }
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi; }

MNT="$(mktemp -d)"; SFS="$(mktemp -d)"
cleanup() {
  for m in "$SFS/dev" "$SFS/proc" "$SFS" "$MNT"; do mountpoint -q "$m" && umount "$m"; done
  rmdir "$MNT" "$SFS" 2>/dev/null
}
trap cleanup EXIT

info="$(xorriso -indev "$ISO" -report_el_torito plain 2>/dev/null)"
volid="$(xorriso -indev "$ISO" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p')"
[[ "$volid" == HALO_* ]] && pass "volume id $volid" || fail "volume id is '$volid', expected HALO_*"
grep -qiE 'El Torito.*BIOS|boot img :.*BIOS' <<<"$info" && pass "BIOS El Torito boot image" || fail "no BIOS El Torito boot image"
grep -qiE 'El Torito.*UEFI|boot img :.*UEFI' <<<"$info" && pass "UEFI El Torito boot image" || fail "no UEFI El Torito boot image"

mount -o loop,ro "$ISO" "$MNT" || { fail "mount ISO"; exit 1; }
B="$MNT/halo/boot/x86_64"
check "kernel halo/boot/x86_64/vmlinuz-linux" test -s "$B/vmlinuz-linux"
check "initramfs halo/boot/x86_64/initramfs-linux.img" test -s "$B/initramfs-linux.img"
check "root image halo/x86_64/airootfs.sfs" test -s "$MNT/halo/x86_64/airootfs.sfs"
check "UEFI loader EFI/BOOT/BOOTx64.EFI" test -s "$MNT/EFI/BOOT/BOOTx64.EFI"
check "BIOS loader boot/syslinux/syslinux.cfg" test -s "$MNT/boot/syslinux/syslinux.cfg"
# Secure Boot: shim (signed by Microsoft's UEFI CA) first, then systemd-boot signed with Halo's key.
grep -aq "UEFI SHIM" "$MNT/EFI/BOOT/BOOTx64.EFI" && grep -aqE "Microsoft Corporation UEFI CA 20(11|23)" "$MNT/EFI/BOOT/BOOTx64.EFI" \
  && pass "Secure Boot: BOOTx64.EFI is shim signed by Microsoft" || fail "Secure Boot: BOOTx64.EFI isn't Microsoft-signed shim"
check "Secure Boot: systemd-boot as grubx64.efi" test -s "$MNT/EFI/BOOT/grubx64.efi"
check "Secure Boot: MokManager (mmx64.efi)" test -s "$MNT/EFI/BOOT/mmx64.efi"
check "Secure Boot: Halo's key to enroll (HALO-MOK.cer)" test -s "$MNT/HALO-MOK.cer"
if command -v sbverify >/dev/null; then
  check "Secure Boot: kernel signed with Halo's key" sbverify --list "$B/vmlinuz-linux"
fi
entry="$MNT/loader/entries/01-halo.conf"
if [ -f "$entry" ]; then
  grep -q 'archisobasedir=halo' "$entry" && pass "UEFI entry: archisobasedir=halo" || fail "UEFI entry: archisobasedir isn't halo"
  grep -q 'copytoram=n' "$entry" && pass "UEFI entry: copytoram=n (installer reads the ISO)" || fail "UEFI entry: copytoram=n missing"
  grep -qE 'archisosearchuuid=[0-9]{4}-' "$entry" && pass "UEFI entry: ISO UUID filled in" || fail "UEFI entry: archisosearchuuid not filled in"
else
  fail "UEFI entry loader/entries/01-halo.conf missing"
fi
grep -q '^TIMEOUT 0' "$MNT/boot/syslinux/syslinux.cfg" && pass "BIOS: boots straight into Halo" || fail "BIOS: menu timeout isn't 0"
if command -v lsinitcpio >/dev/null; then
  ird="$(lsinitcpio "$B/initramfs-linux.img" 2>/dev/null)"
  grep -q 'hooks/archiso$' <<<"$ird" && pass "initramfs: archiso hook" || fail "initramfs: archiso hook missing"
  grep -q 'plymouth/themes/halo/' <<<"$ird" && pass "initramfs: Halo boot splash" || fail "initramfs: Halo boot splash missing"
else
  echo "SKIP  initramfs contents (lsinitcpio not installed)"
fi

mount -t squashfs -o loop,ro "$MNT/halo/x86_64/airootfs.sfs" "$SFS" || { fail "mount airootfs.sfs"; exit 1; }
R="$SFS"
grep -q '^NAME="Halo"' "$R/etc/os-release" && pass "os-release: NAME=Halo" || fail "os-release isn't Halo's"
grep -q '^ID_LIKE=arch' "$R/etc/os-release" && pass "os-release: ID_LIKE=arch" || fail "os-release: ID_LIKE=arch missing"
grep -q '^Theme=halo' "$R/etc/plymouth/plymouthd.conf" && pass "Plymouth theme: halo" || fail "Plymouth default theme isn't halo"
check "SAINT launcher /usr/bin/saint" test -x "$R/usr/bin/saint"
check "SAINT app /opt/saint/app/app.py" test -s "$R/opt/saint/app/app.py"
[ "$(readlink "$R/opt/saint/venv/bin/python")" = /opt/saint/python/bin/python3.12 ] \
  && pass "SAINT venv points at /opt/saint/python" || fail "SAINT venv python link: $(readlink "$R/opt/saint/venv/bin/python")"
mount -t proc proc "$R/proc" && mount --bind /dev "$R/dev"
if out=$(chroot "$R" /usr/bin/env -i PATH=/usr/bin QT_QPA_PLATFORM=offscreen HOME=/tmp \
         /usr/bin/bash -c 'cd /opt/saint/app && /opt/saint/venv/bin/python -c "import PySide6.QtWidgets, numpy, onnxruntime, faster_whisper, kokoro_onnx, misaki.en, en_core_web_sm, sounddevice, keyring, core.paths; assert core.paths.system_install()"' 2>&1); then
  pass "SAINT runtime imports inside the image"
else
  fail "SAINT runtime imports inside the image: $(tail -n 3 <<<"$out" | tr '\n' ' ')"
fi
umount "$R/dev" "$R/proc"
for f in usr/bin/plasmashell usr/bin/sddm usr/share/wayland-sessions/plasma.desktop usr/bin/firefox usr/bin/konsole \
         usr/bin/dolphin usr/bin/paru usr/bin/calamares usr/bin/ollama usr/bin/flatpak usr/bin/grub-install usr/bin/os-prober \
         etc/calamares/settings.conf etc/calamares/branding/halo/branding.desc usr/lib/halo/calamares-cleanup \
         usr/share/applications/install-halo.desktop usr/share/plymouth/themes/halo/halo.plymouth \
         usr/share/wallpapers/SAINTOS/contents/images/1920x1080.png usr/share/halo/build-info.json \
         usr/share/kglobalaccel/saint-overlay.desktop usr/share/kwin/scripts/saint-shell/contents/code/main.js \
         usr/lib/systemd/system/saint-ollama-model.service usr/bin/kbuildsycoca6 usr/bin/notify-send; do
  check "image has /$f" test -e "$R/$f"
done
check "Discover isn't installed (SAINT's Store replaces it)" test ! -e "$R/usr/bin/plasma-discover"
S="$R/etc/systemd/system"
[ "$(readlink "$S/display-manager.service")" = /usr/lib/systemd/system/sddm.service ] && pass "SDDM is the display manager" || fail "display-manager.service isn't SDDM"
[ "$(readlink "$S/default.target")" = /usr/lib/systemd/system/graphical.target ] && pass "boots to graphical.target" || fail "default.target isn't graphical"
for u in multi-user.target.wants/NetworkManager.service multi-user.target.wants/halo-live.service \
         multi-user.target.wants/halo-pacman-init.service graphical.target.wants/saintos-boot-report.service \
         multi-user.target.wants/ollama.service multi-user.target.wants/saint-ollama-model.service; do
  check "enabled: $u" test -L "$S/$u"
done
check "boot doesn't wait for the network" test ! -e "$S/network-online.target.wants/NetworkManager-wait-online.service"
[ "$(readlink "$S/systemd-firstboot.service")" = /dev/null ] && pass "no console questions at boot (systemd-firstboot masked)" \
  || fail "systemd-firstboot would stop the boot to ask questions"
check "a timezone is set (/etc/localtime)" test -L "$R/etc/localtime"
grep -q '^X-KDE-Shortcuts=Alt+`' "$R/usr/share/kglobalaccel/saint-overlay.desktop" && pass "Alt+\` opens SAINT's overlay" || fail "overlay shortcut missing"
grep -q '^Walk Through Windows of Current Application=none,' "$R/etc/xdg/kglobalshortcutsrc" && pass "KWin doesn't take Alt+\`" || fail "KWin's window switcher still owns Alt+\`"
[ -s "$R/opt/saint/app/data/tts/kokoro-onnx/kokoro-v1.0.onnx" ] && pass "Kokoro voice ships in the image" || fail "Kokoro model missing from /opt/saint"
grep -q 'applications:saint.desktop' "$R/usr/share/plasma/shells/org.kde.plasma.desktop/contents/updates/saintos-defaults.js" \
  && pass "SAINT pinned once on the taskbar" || fail "SAINT isn't pinned"
grep -q '^ConditionKernelCommandLine=archisobasedir' "$S/halo-live.service" && pass "live setup only runs on the live ISO" || fail "halo-live.service has no live-only condition"
# Nothing live-only may be baked into the image: the installer copies it to disk verbatim.
check "image has no live user" bash -c "! grep -q '^liveuser:' '$R/etc/passwd'"
check "image has no autologin config" test ! -e "$R/etc/sddm.conf.d/zz-halo-live.conf"
check "image has no NOPASSWD sudoers file" test ! -e "$R/etc/sudoers.d/10-halo-live"

echo
if [ "$fails" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "$fails CHECK(S) FAILED"; fi
[ "$fails" -eq 0 ]
