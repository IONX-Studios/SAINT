#!/usr/bin/env bash
# Type text into a VirtualBox VM one character at a time, then Enter (host side, Git Bash or Linux).
# keyboardputstring in one go drops/sticks Shift under Hyper-V; keep typed text lowercase and short.
#   packaging/linux/vm/vm_type.sh VMNAME "sudo bash /mnt/s/b.sh"
set -eu
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"        # Git Bash: never rewrite /paths into C:/...
VB="${VBOXMANAGE:-VBoxManage}"; command -v "$VB" >/dev/null || VB="/c/Program Files/Oracle/VirtualBox/VBoxManage.exe"
vm="$1"; text="$2"

for ((i = 0; i < ${#text}; i++)); do
  "$VB" controlvm "$vm" keyboardputstring "${text:i:1}"
done
"$VB" controlvm "$vm" keyboardputscancode 1c 9c                 # Enter
