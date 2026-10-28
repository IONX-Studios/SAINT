#!/usr/bin/env bash
# Install SAINTOS from its ISO onto the DISPOSABLE test VM's virtual disk, then boot the installed
# system and check it. Host side (Git Bash on Windows, or Linux), after:
#   boot_test.ps1 -Iso dist/SAINTOS-x86_64.iso -Keep        (the live session is up in SAINTOS-test)
#
#   packaging/linux/vm/install_test.sh [VM=SAINTOS-test]
#
# 1. gives the live session SSH (a read-only transient share + a short typed command)
# 2. runs Anaconda non-interactively with install-test.ks (its %pre refuses anything except one empty
#    VirtualBox disk), which powers the VM off when done
# 3. ejects the ISO and boots the installed disk: boot_test.ps1 -FromDisk waits for the boot report
# 4. over SSH as the kickstart's test user: SAINT's self-test, the Control Center, the user service
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"
VM="${1:-SAINTOS-test}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
VB="${VBOXMANAGE:-VBoxManage}"; command -v "$VB" >/dev/null || VB="/c/Program Files/Oracle/VirtualBox/VBoxManage.exe"
KEY="${SAINTOS_VM_KEY:-$HOME/.ssh/saintos_vm}"
OUT="$REPO/dist/boot-test-$VM"; mkdir -p "$OUT"
SERVE="$OUT/share"; mkdir -p "$SERVE"
ssh_vm() { ssh -i "$KEY" -p 2224 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes \
             -o ConnectTimeout=5 -o ServerAliveInterval=30 "$@" 2>/dev/null; }
wait_ssh() {   # user, seconds
  local end=$((SECONDS + $2)); until ssh_vm "$1@127.0.0.1" true; do [ $SECONDS -lt $end ] || return 1; sleep 3; done
}
[ -f "$KEY.pub" ] || ssh-keygen -q -t ed25519 -N "" -C saintos-vm -f "$KEY"

echo "== 1. SSH into the live session"
cat > "$SERVE/b.sh" <<'EOF'
#!/usr/bin/bash
set -eu
[ "$(systemd-detect-virt)" = oracle ] || exit 1
U=liveuser; H=$(getent passwd $U | cut -d: -f6)
install -d -m 0700 -o $U -g $U "$H/.ssh"
cat "$(dirname "$0")/key.pub" >> "$H/.ssh/authorized_keys"
chown $U:$U "$H/.ssh/authorized_keys"; chmod 600 "$H/.ssh/authorized_keys"
rpm -q openssh-server >/dev/null || dnf install -y openssh-server
systemctl start sshd
EOF
cp "$KEY.pub" "$SERVE/key.pub"
sed "s#@SSH_KEY@#$(cat "$KEY.pub")#" "$HERE/install-test.ks" > "$SERVE/ks.cfg"
"$VB" sharedfolder remove "$VM" --name s --transient 2>/dev/null || true
"$VB" sharedfolder add "$VM" --name s --hostpath "$(cd "$SERVE" && pwd -W 2>/dev/null || pwd)" --transient --readonly
"$VB" controlvm "$VM" keyboardputscancode 1d 38 14 94 b8 9d          # Ctrl+Alt+T: Konsole
sleep 8
"$HERE/vm_type.sh" "$VM" "sudo mkdir -p /mnt/s; sudo mount -t vboxsf s /mnt/s; sudo bash /mnt/s/b.sh"
wait_ssh liveuser 120 || { echo "live session SSH didn't come up (see $OUT/final.png)"; exit 1; }
echo "live SSH ok"

echo "== 2. Install to the virtual disk (Anaconda + install-test.ks)"
ssh_vm liveuser@127.0.0.1 'lsblk -d -o NAME,SIZE,MODEL'
# liveinst refuses kickstarts on live media ("not supported ... will continue interactively"), so
# Anaconda is called directly in text, non-interactive mode with the live system as the source. This
# is a test harness for the disposable VM, not a supported way to install; people use the GUI.
ssh_vm liveuser@127.0.0.1 'sudo cp /mnt/s/ks.cfg /tmp/ks.cfg && sudo anaconda --liveinst --text --noninteractive --kickstart /tmp/ks.cfg' \
  > "$OUT/install.log" 2>&1 || true
end=$((SECONDS + 3600))
while "$VB" showvminfo "$VM" --machinereadable | grep -q 'VMState="running"'; do
  [ $SECONDS -lt $end ] || { echo "install didn't finish within an hour"; exit 1; }
  sleep 15
done
echo "VM powered off after install"

echo "== 3. Boot the installed system"
powershell.exe -ExecutionPolicy Bypass -File "$(cd "$HERE" && pwd -W 2>/dev/null || pwd)/boot_test.ps1" -Name "$VM" -FromDisk -Keep

echo "== 4. Installed-system checks"
wait_ssh saint 180 || { echo "installed system SSH didn't come up"; exit 1; }
ssh_vm saint@127.0.0.1 'set -x
  cat /etc/os-release | head -3
  findmnt -no SOURCE,FSTYPE /
  rpm -q kernel | tail -1
  test -x /usr/bin/saint && test -x /opt/saint/venv/bin/python
  cd /opt/saint/app && QT_QPA_PLATFORM=offscreen SAINT_SELFTEST_NO_VOICE=1 SAINT_DATA_DIR=$(mktemp -d) \
    /opt/saint/venv/bin/python app.py --selftest /tmp/selftest.json; echo selftest_exit=$?
  /opt/saint/venv/bin/python -c "import json; d=json.load(open(\"/tmp/selftest.json\")); print(\"passed\", d[\"passed\"]); [print(c[\"ok\"], c[\"name\"], str(c[\"detail\"])[:120]) for c in d[\"checks\"]]"
  cd /opt/saint/app && QT_QPA_PLATFORM=offscreen timeout 20 /opt/saint/venv/bin/python -c "from ui.saintos_center import build_window; from PySide6.QtWidgets import QApplication; a=QApplication([]); w=build_window(); print(\"control center pages\", w._stack.count())"
  systemctl --user start saint.service; sleep 20; systemctl --user is-active saint.service
  journalctl --user -u saint.service -n 30 --no-pager | tail -15
  systemctl --user stop saint.service
  cat /run/saintos-selftest.json 2>/dev/null | head -5' > "$OUT/installed-checks.log" 2>&1
cat "$OUT/installed-checks.log"
echo "Install test log: $OUT"
