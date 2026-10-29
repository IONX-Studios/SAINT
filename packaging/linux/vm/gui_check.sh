#!/usr/bin/env bash
# After boot_test.ps1 -Keep has reached the desktop: open Konsole in the VM's Plasma session with the
# keyboard, show the OS identity and network, then start the real SAINT app in that session. Saves
# screenshots; read them to judge the result (this script doesn't decide PASS/FAIL).
#
#   packaging/linux/vm/gui_check.sh SAINTOS-test dist/boot-test-SAINTOS-test
#
# Typed text has no Shift characters (Shift sticks under Hyper-V; see vm_type.sh).
set -eu
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"
VB="${VBOXMANAGE:-VBoxManage}"; command -v "$VB" >/dev/null || VB="/c/Program Files/Oracle/VirtualBox/VBoxManage.exe"
vm="$1"; out="$2"; mkdir -p "$out"
here="$(cd "$(dirname "$0")" && pwd)"
shot() { "$VB" controlvm "$vm" screenshotpng "$(cygpath -w "$out/$1" 2>/dev/null || echo "$out/$1")"; echo "saved $out/$1"; }

shot gui-0-desktop.png
"$VB" controlvm "$vm" keyboardputscancode 38 39 b9 b8         # Alt+Space: KRunner
sleep 3
"$here/vm_type.sh" "$vm" "konsole"
sleep 8
"$here/vm_type.sh" "$vm" "clear; grep -i -e name -e version /etc/os-release; hostname; ping -c2 -w5 fedoraproject.org"
sleep 10
shot gui-1-identity-network.png
"$here/vm_type.sh" "$vm" "saint"
sleep "${SAINT_WAIT:-45}"
shot gui-2-saint.png
