# Testing SAINTOS in a virtual machine

Always test a new ISO in a VM before it goes near real hardware. Everything here uses **virtual
disks only**. Nothing writes to a physical disk.

## Quick start (Windows + VirtualBox, automated)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\linux\vm\boot_test.ps1 -Iso dist\SAINTOS-x86_64.iso
```

This creates a disposable VM `SAINTOS-test` (UEFI, 4 vCPU, 6 GB RAM, empty 40 GB virtual disk, NAT,
serial port → file), boots the ISO's default entry and waits for the image's own boot report on the
serial port:

```
SAINTOS-BOOT-OK 0.1.0 live                      graphical.target reached
SAINTOS-DESKTOP-OK user=liveuser session=2 type=wayland   Plasma session active
SAINTOS-SAINT-OK                                SAINT's offline self-test passed inside the OS
```

`PASS` needs all three. Evidence goes to `dist\boot-test-<name>\`: `serial.log`, a screenshot every
minute, `final.png`, `result-live.json`. The VM is powered off afterwards (`-Keep` leaves it
running so you can look around). Remove it with
`VBoxManage unregistervm SAINTOS-test --delete`.

Options: `-Firmware bios` (legacy BIOS boot), `-Name` (run several side by side), `-TimeoutMin`,
`-FromDisk` (boot the installed disk after an install test).

What the boot report does **not** prove: that the SAINT window opens in the session (its self-test
runs offscreen), audio, or that a voice model downloads. Check those by hand (below).

## Manual VM setup (VirtualBox, VMware, Hyper-V, virt-manager)

| Setting | Value |
|---|---|
| Type | Linux / Fedora (64-bit) |
| CPUs / RAM | 2+ vCPU / 4 GB minimum (6 GB+ recommended; Plasma + SAINT's voice stack) |
| Firmware | UEFI or BIOS (both are on the ISO) |
| Disk | none needed for a live session; 20–40 GB virtual disk to test installation |
| Optical | `SAINTOS-x86_64.iso` |
| Display | VirtualBox: VMSVGA, 128 MB. QEMU: `-vga virtio` |
| Network | NAT |
| Secure Boot | works as on Fedora (shim + signed GRUB/kernel); not separately tested yet |

Hyper-V: Generation 2, **turn Secure Boot template to "Microsoft UEFI Certificate Authority"** or
switch Secure Boot off.

### QEMU/KVM (Linux host)

```bash
packaging/linux/vm/qemu_boot_test.sh dist/SAINTOS-x86_64.iso          # automated, UEFI (OVMF)
```

or interactively:

```bash
qemu-system-x86_64 -machine q35,accel=kvm -cpu host -smp 2 -m 4096 -cdrom dist/SAINTOS-x86_64.iso -boot d -vga virtio -nic user,model=virtio-net-pci -bios /usr/share/edk2/ovmf/OVMF_CODE.fd
```

Drop `-bios ...` for legacy BIOS boot.

## Manual checklist (live session)

1. **Boot menu** shows `Start SAINTOS`, and the GRUB title says SAINTOS. Pick the default entry.
2. **Splash**: SAINTOS Plymouth theme (dark, SAINT-orange), not the Fedora logo.
3. **Desktop**: Plasma logs in automatically as `liveuser` with the SAINTOS wallpaper and dark theme.
   The SAINTOS Control Center opens once on its Welcome page.
4. **Identity**: in Konsole, `cat /etc/os-release` shows `NAME="SAINTOS"`, and `hostnamectl` /
   `fastfetch` report SAINTOS.
5. **SAINT**: launch from the app menu (*SAINT*) or Alt+Space → `saint`. The SAINT window/tray appears.
   `saint --selftest /tmp/st.json; cat /tmp/st.json` gives the full self-test.
   `journalctl --user -b | grep -i saint` and `~/.local/share/SAINT/logs/` hold its logs.
6. **Network**: `ping -c1 fedoraproject.org`; the network applet shows a wired connection.
7. **Audio**: Settings → Sound shows an output device (VirtualBox: enable audio in the VM settings).
8. **Diagnostics**: `saintos-diagnose` prints a read-only report.
9. **Installer** (optional): *Install SAINTOS* on the desktop. Install only onto the VM's virtual disk.

## Installation test (virtual disk only)

`packaging/linux/vm/install_test.sh` runs Anaconda with `install-test.ks` against the test VM's
virtual disk, then `boot_test.ps1 -FromDisk` boots the installed system and waits for
`SAINTOS-BOOT-OK ... installed`. The kickstart refuses any disk that isn't a VirtualBox/QEMU virtual disk.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Black screen after GRUB | Troubleshooting → *basic graphics mode*; on VirtualBox use VMSVGA, not VBoxVGA |
| Stuck at "Starting ... dracut" / drops to an emergency shell | Live root not found: the ISO label must match `root=live:CDLABEL=SAINTOS-44-x86_64` (check `validate_iso.sh`); re-download/copy the ISO and check `SHA256SUMS` |
| `SAINTOS-DESKTOP-TIMEOUT` | Plasma didn't start: give the VM more RAM (4 GB+), check `journalctl -b -u sddm` |
| `SAINTOS-SAINT-FAIL <reason>` | The tail of SAINT's self-test is in the marker; full JSON at `/run/saintos-selftest.json` |
| Nothing on the serial log | VirtualBox: the VM needs UART1 = 0x3F8/IRQ4 in file mode (`boot_test.ps1` sets it) |
| VirtualBox fails to start the VM with Hyper-V on | VirtualBox 7 runs on Hyper-V ("turtle" icon) but slowly. Boots take several minutes; raise `-TimeoutMin` |

## Results

See [SAINTOS_CURRENT_STATE.md](SAINTOS_CURRENT_STATE.md#3-current-status) for the latest recorded
boot test results.
