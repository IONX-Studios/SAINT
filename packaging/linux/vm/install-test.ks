# Automated SAINTOS install for the DISPOSABLE test VM only (packaging/linux/vm/install_test.sh).
# %pre refuses to continue unless this is a VirtualBox VM whose only disk is an empty
# "VBOX HARDDISK" — the kickstart wipes that disk, so it must never run anywhere else.
text
lang en_US.UTF-8
keyboard us
timezone UTC --utc
network --bootproto=dhcp --hostname=saintos-test
firstboot --disable
selinux --enforcing

%pre --erroronfail --log=/tmp/saintos-pre.log
set -e
[ "$(systemd-detect-virt)" = oracle ] || { echo "not a VirtualBox VM: refusing to install" >&2; exit 1; }
disks=$(lsblk -dn -o NAME,TYPE,MODEL | awk '$2=="disk"' )
echo "$disks"
[ "$(echo "$disks" | wc -l)" -eq 1 ] || { echo "expected exactly one disk" >&2; exit 1; }
echo "$disks" | grep -q 'VBOX HARDDISK' || { echo "the disk isn't a VirtualBox virtual disk" >&2; exit 1; }
d=$(echo "$disks" | awk '{print $1}')
[ -z "$(lsblk -n -o NAME "/dev/$d" | tail -n +2)" ] || { echo "/dev/$d has partitions: refusing" >&2; exit 1; }
%end

ignoredisk --only-use=sda
zerombr
clearpart --all --initlabel --drives=sda
autopart --type=btrfs
bootloader --boot-drive=sda
rootpw --lock
user --name=saint --groups=wheel --gecos="SAINTOS test" --lock
sshkey --username=saint "@SSH_KEY@"
shutdown

%post --erroronfail --log=/root/saintos-post.log
# Test VM conveniences: passwordless sudo for the test user, sshd on, desktop autologin so the
# boot report can see a graphical session.
echo 'saint ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-saintos-test
chmod 0440 /etc/sudoers.d/90-saintos-test
systemctl enable sshd
if [ -e /usr/lib/systemd/system/plasmalogin.service ]; then
  mkdir -p /etc/plasmalogin.conf.d
  printf '[Autologin]\nUser=saint\nSession=plasma\n' > /etc/plasmalogin.conf.d/90-saintos-test.conf
fi
mkdir -p /etc/sddm.conf.d
printf '[Autologin]\nUser=saint\nSession=plasma\n' > /etc/sddm.conf.d/90-saintos-test.conf
%end
