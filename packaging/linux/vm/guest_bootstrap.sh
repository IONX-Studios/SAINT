#!/usr/bin/env bash
# One-time setup INSIDE the SAINTOS VirtualBox VM (never on a real install).
#
#   bash /media/sf_saint/packaging/linux/vm/guest_bootstrap.sh
#
# 1. gives your user access to the VirtualBox shared folder (the host's SAINT checkout)
# 2. enables sshd with key-only login, so the host can run setup and tests in the VM
#    (VirtualBox forwards host 127.0.0.1:2222 -> VM port 22; nothing else can reach it)
# 3. clones the shared checkout into ~/SAINT (a venv on a vboxsf mount is slow and breaks symlinks)
set -euo pipefail
SHARE=/media/sf_saint
[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user (it uses sudo where needed)." >&2; exit 1; }
systemd-detect-virt -q --vm || { echo "This script is only for the VM." >&2; exit 1; }

sudo usermod -aG vboxsf "$USER"
if [ ! -d "$SHARE" ]; then
  echo "The shared folder isn't mounted. Check that the guest additions are loaded (lsmod | grep vboxsf)"
  echo "then log out and back in (the vboxsf group applies to new sessions) and run this again."
  exit 1
fi

sudo dnf install -y --skip-unavailable openssh-server git
sudo install -d -m 0755 /etc/ssh/sshd_config.d
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' \
  | sudo tee /etc/ssh/sshd_config.d/50-saintos-vm.conf >/dev/null
sudo systemctl enable --now sshd

install -d -m 0700 ~/.ssh
KEY="$SHARE/packaging/linux/vm/dev_host_key.pub"
if [ -f "$KEY" ] && ! grep -qF "$(cat "$KEY")" ~/.ssh/authorized_keys 2>/dev/null; then
  cat "$KEY" >> ~/.ssh/authorized_keys
fi
chmod 600 ~/.ssh/authorized_keys

if [ ! -d ~/SAINT/.git ]; then
  git config --global --add safe.directory "$SHARE"      # the share is owned by root:vboxsf
  git clone "$SHARE" ~/SAINT
  git -C ~/SAINT checkout saintos/milestone-a-audit 2>/dev/null || true
fi
echo
echo "Done. VM user: $USER. Tell Claude the user name; it connects with ssh -p 2222 $USER@127.0.0.1."
