#!/usr/bin/bash
# Typed (via VBoxManage) into the Fedora live session of the SAINTOS *build* VM: enables SSH with
# the developer host's key so the build runs over `ssh -p 2223 liveuser@127.0.0.1`.
# Served to the VM from the host over VirtualBox NAT (10.0.2.2); never run on a real machine.
set -eu
systemd-detect-virt -q --vm || { echo "build VM only" >&2; exit 1; }
KEY="$(curl -fsS http://10.0.2.2:8765/dev_host_key.pub)"
case "$KEY" in ssh-ed25519\ *) ;; *) echo "bad key" >&2; exit 1 ;; esac
install -d -m 0700 ~/.ssh
grep -qF "$KEY" ~/.ssh/authorized_keys 2>/dev/null || echo "$KEY" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
rpm -q openssh-server >/dev/null || sudo dnf install -y openssh-server
sudo systemctl start sshd
echo SAINTOS-BUILD-VM-SSH-READY | sudo tee /dev/ttyS0 >/dev/null 2>&1 || true
echo "SSH ready"
