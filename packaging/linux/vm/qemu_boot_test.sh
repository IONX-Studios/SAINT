#!/usr/bin/env bash
# Boot a SAINTOS ISO under QEMU/KVM with UEFI (OVMF) on a Linux host, headless, and wait for the
# image's serial boot report (see packaging/linux/rootfs/usr/libexec/saintos/boot-report).
#
#   packaging/linux/vm/qemu_boot_test.sh dist/SAINTOS-x86_64.iso [timeout-seconds]
#
# PASS needs SAINTOS-BOOT-OK, SAINTOS-DESKTOP-OK and SAINTOS-SAINT-OK on the serial port. Uses a
# throwaway qcow2 disk and copies of the OVMF variables; touches no real disk. Evidence goes to
# dist/qemu-boot/ (serial.log, screenshot.ppm, result.json).
set -uo pipefail
ISO="${1:?usage: qemu_boot_test.sh ISO [timeout]}"
TIMEOUT="${2:-1500}"
OUT="$(dirname "$ISO")/qemu-boot"; mkdir -p "$OUT"
CODE=""; VARS=""
for c in /usr/share/edk2/ovmf/OVMF_CODE.fd /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd; do
  [ -f "$c" ] && { CODE="$c"; VARS="${c/CODE/VARS}"; break; }
done
[ -n "$CODE" ] && [ -f "$VARS" ] || { echo "OVMF firmware not found (dnf install edk2-ovmf / apt install ovmf)" >&2; exit 2; }
command -v qemu-system-x86_64 >/dev/null || { echo "qemu-system-x86_64 missing" >&2; exit 2; }
ACCEL=tcg; [ -w /dev/kvm ] && ACCEL=kvm
cp "$VARS" "$OUT/vars.fd"
qemu-img create -q -f qcow2 "$OUT/disk.qcow2" 40G
: > "$OUT/serial.log"
qemu-system-x86_64 -machine q35,accel=$ACCEL -cpu max -smp 4 -m 6144 \
  -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$OUT/vars.fd" \
  -drive file="$OUT/disk.qcow2",if=virtio -cdrom "$ISO" -boot d \
  -vga virtio -display none -serial file:"$OUT/serial.log" -monitor unix:"$OUT/mon.sock",server,nowait \
  -nic user,model=virtio-net-pci &
QPID=$!
start=$SECONDS; result=TIMEOUT
while [ $((SECONDS - start)) -lt "$TIMEOUT" ]; do
  sleep 10
  kill -0 $QPID 2>/dev/null || { result=QEMU_EXITED; break; }
  grep -q 'SAINTOS-SAINT-FAIL\|SAINTOS-DESKTOP-TIMEOUT' "$OUT/serial.log" && { result=FAIL; break; }
  grep -q SAINTOS-BOOT-OK "$OUT/serial.log" && grep -q SAINTOS-DESKTOP-OK "$OUT/serial.log" \
    && grep -q SAINTOS-SAINT-OK "$OUT/serial.log" && { result=PASS; break; }
done
echo "screendump $OUT/screenshot.ppm" | socat - UNIX-CONNECT:"$OUT/mon.sock" >/dev/null 2>&1 || true
sleep 1
kill $QPID 2>/dev/null; wait $QPID 2>/dev/null
grep -a 'SAINTOS-' "$OUT/serial.log" || true
printf '{"result":"%s","accel":"%s","seconds":%d}\n' "$result" "$ACCEL" $((SECONDS - start)) > "$OUT/result.json"
echo "QEMU BOOT TEST $result ($ACCEL, $((SECONDS - start))s) — $OUT"
[ "$result" = PASS ]
