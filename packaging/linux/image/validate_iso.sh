#!/usr/bin/env bash
# Structural checks on a SAINTOS ISO (read-only; the image is mounted read-only via loop devices).
#
#   packaging/linux/image/validate_iso.sh dist/SAINTOS-x86_64.iso
#
# Checks: ISO 9660 metadata and volume id · UEFI boot (El Torito EFI image, BOOTX64.EFI, grub.cfg
# entries) · live kernel + initrd · LiveOS root filesystem · inside the root filesystem: SAINTOS
# identity, SAINT app + Python env + imports, launchers, services, branding, Plymouth theme.
# Prints one PASS/FAIL line per check; exits 1 if any check failed.
set -uo pipefail
ISO="${1:?usage: validate_iso.sh ISO}"
[ -s "$ISO" ] || { echo "FAIL iso: $ISO missing or empty"; exit 1; }
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"
fails=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; fails=$((fails + 1)); }
check() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi; }

MNT="$(mktemp -d /tmp/saintos-iso.XXXXXX)"; ROOT="$(mktemp -d /tmp/saintos-root.XXXXXX)"
cleanup() { $SUDO umount "$ROOT" 2>/dev/null; $SUDO umount "$MNT" 2>/dev/null; rmdir "$ROOT" "$MNT" 2>/dev/null; }
trap cleanup EXIT

echo "# validate $(basename "$ISO") ($(stat -c %s "$ISO") bytes)"
info="$(xorriso -indev "$ISO" -report_el_torito plain 2>&1)"
volid="$(xorriso -indev "$ISO" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p')"
[[ "$volid" == SAINTOS-* ]] && pass "volume id: $volid" || fail "volume id is '$volid' (expected SAINTOS-*)"
grep -qi 'El Torito.*UEFI\|platform_id.*0xef\|boot img :.*UEFI' <<<"$info" && pass "El Torito UEFI boot image" \
  || fail "no UEFI El Torito boot image"

$SUDO mount -o loop,ro "$ISO" "$MNT" || { fail "mount ISO"; exit 1; }
check "EFI/BOOT/BOOTX64.EFI" test -s "$MNT/EFI/BOOT/BOOTX64.EFI"
GRUB="$(ls "$MNT"/EFI/BOOT/grub.cfg "$MNT"/boot/grub2/grub.cfg 2>/dev/null | head -n1)"
if [ -n "$GRUB" ]; then
  pass "grub.cfg ($GRUB)"
  grep -q 'menuentry "Start SAINTOS' "$GRUB" && pass "grub: 'Start SAINTOS' entry" || fail "grub: no 'Start SAINTOS' entry"
  grep -q 'rd.live.image' "$GRUB" && pass "grub: live boot options" || fail "grub: rd.live.image missing"
  grep -q "root=live:CDLABEL=$volid" "$GRUB" && pass "grub: root=live:CDLABEL=$volid" \
    || fail "grub: live root label doesn't match the volume id"
else
  fail "grub.cfg not found"
fi
KERNEL="$(ls "$MNT"/boot/x86_64/loader/linux "$MNT"/images/pxeboot/vmlinuz 2>/dev/null | head -n1)"
INITRD="$(ls "$MNT"/boot/x86_64/loader/initrd "$MNT"/images/pxeboot/initrd.img 2>/dev/null | head -n1)"
[ -n "$KERNEL" ] && [ -s "$KERNEL" ] && pass "kernel ($KERNEL)" || fail "kernel not found"
[ -n "$INITRD" ] && [ -s "$INITRD" ] && pass "initrd ($INITRD)" || fail "initrd not found"
# Every kernel/initrd a GRUB entry names must exist on the ISO (paths with GRUB variables are skipped).
if [ -n "$GRUB" ]; then
  refs="$(grep -E '^\s*(linux|linuxefi|initrd|initrdefi)\s' "$GRUB" | awk '{print $2}' | sed 's/^([^)]*)//' | grep -v '\$' | sort -u)"
  [ -n "$refs" ] || fail "grub: no linux/initrd lines"
  for r in $refs; do
    [ -s "$MNT/$r" ] && pass "grub reference $r exists" || fail "grub references $r, which isn't on the ISO"
  done
fi
# Legacy BIOS: an El Torito BIOS entry and GRUB's i386-pc modules.
grep -qiE 'El Torito boot img :.*BIOS' <<<"$info" && pass "El Torito BIOS boot image" || fail "no BIOS El Torito boot image"
ls -d "$MNT"/boot/grub2/i386-pc "$MNT"/boot/x86_64/loader/grub2/i386-pc >/dev/null 2>&1 && pass "GRUB i386-pc (BIOS) modules" \
  || fail "no GRUB i386-pc directory (BIOS boot)"
if [ -n "$INITRD" ] && command -v lsinitrd >/dev/null; then
  $SUDO lsinitrd "$INITRD" 2>/dev/null | grep -q 'dmsquash-live' && pass "initrd: dmsquash-live module" \
    || fail "initrd: no dmsquash-live (won't find the live root)"
  $SUDO lsinitrd "$INITRD" 2>/dev/null | grep -q 'plymouth/themes/saintos' && pass "initrd: SAINTOS boot splash" \
    || fail "initrd: SAINTOS Plymouth theme not included"
fi
LIVE="$(ls "$MNT"/LiveOS/squashfs.img 2>/dev/null | head -n1)"
[ -n "$LIVE" ] && pass "LiveOS root image ($(stat -c %s "$LIVE") bytes)" || { fail "LiveOS/squashfs.img missing"; echo "FAILED: $fails"; exit 1; }

# The root filesystem: erofs (Fedora 42+) or squashfs with an ext4 image inside (older).
if $SUDO mount -o loop,ro "$LIVE" "$ROOT" 2>/dev/null; then
  if [ -f "$ROOT/LiveOS/rootfs.img" ]; then
    INNER="$ROOT/LiveOS/rootfs.img"; ROOT2="$(mktemp -d /tmp/saintos-root2.XXXXXX)"
    $SUDO mount -o loop,ro "$INNER" "$ROOT2" && ROOTFS="$ROOT2"
  else
    ROOTFS="$ROOT"
  fi
  pass "mount live root filesystem"
else
  fail "mount live root filesystem"; echo "FAILED: $fails"; exit 1
fi
R="$ROOTFS"
grep -q '^NAME="SAINTOS"' "$R/etc/os-release" && pass "os-release: $(sed -n 's/^PRETTY_NAME=//p' "$R/etc/os-release")" \
  || fail "os-release isn't SAINTOS"
check "SAINT app (/opt/saint/app/app.py)" test -s "$R/opt/saint/app/app.py"
check "SAINT Python env (/opt/saint/venv/bin/python)" test -e "$R/opt/saint/venv/bin/python"
check "no wheelhouse left in the image" test ! -e "$R/opt/saint/wheelhouse"
check "PySide6 installed" bash -c "ls -d '$R'/opt/saint/venv/lib/python3.12/site-packages/PySide6 >/dev/null"
check "onnxruntime installed" bash -c "ls -d '$R'/opt/saint/venv/lib/python3.12/site-packages/onnxruntime >/dev/null"
check "faster_whisper installed" bash -c "ls -d '$R'/opt/saint/venv/lib/python3.12/site-packages/faster_whisper >/dev/null"
for f in usr/bin/saint usr/bin/saintos-control usr/bin/saintos-diagnose usr/libexec/saintos/boot-report \
         usr/libexec/saintos/firstrun; do
  check "executable /$f" test -x "$R/$f"
done
for f in usr/share/applications/saint.desktop usr/share/applications/saintos-control.desktop \
         usr/lib/systemd/user/saint.service usr/lib/systemd/system/saintos-boot-report.service \
         etc/xdg/autostart/saintos-firstrun.desktop etc/xdg/kdeglobals \
         usr/share/plasma/shells/org.kde.plasma.desktop/contents/updates/saintos-defaults.js \
         usr/share/wallpapers/SAINTOS/metadata.json usr/share/wallpapers/SAINTOS/contents/images/1920x1080.png \
         usr/share/icons/hicolor/256x256/apps/saintos.png usr/share/icons/hicolor/512x512/apps/saint.png \
         usr/share/plymouth/themes/saintos/saintos.plymouth usr/share/saintos/build-info.json; do
  check "/$f" test -s "$R/$f"
done
check "boot report enabled" test -L "$R/etc/systemd/system/graphical.target.wants/saintos-boot-report.service"
check "Anaconda live installer present" test -e "$R/usr/bin/liveinst"
check "KDE Plasma present" test -e "$R/usr/bin/plasmashell"
if command -v desktop-file-validate >/dev/null; then
  for f in saint.desktop saintos-control.desktop; do
    check "desktop-file-validate $f" desktop-file-validate "$R/usr/share/applications/$f"
  done
fi
grep -q 'Theme=saintos' "$R/etc/plymouth/plymouthd.conf" 2>/dev/null && pass "Plymouth default theme: saintos" \
  || fail "Plymouth default theme isn't saintos"
[ -n "${ROOT2:-}" ] && { $SUDO umount "$ROOT2"; rmdir "$ROOT2"; }

echo
if [ "$fails" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "FAILED: $fails check(s)"; fi
[ "$fails" -eq 0 ]
