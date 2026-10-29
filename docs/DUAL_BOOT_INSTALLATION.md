# Dual-boot installation: Fedora KDE next to Windows

A human checklist. **No SAINT script partitions, formats, resizes, changes boot entries, Secure Boot
or TPM.** You do each step yourself, and you can stop at any checkpoint.

> **Which ISO?** The SAINTOS ISO (`dist/SAINTOS-x86_64.iso`) is Fedora KDE's own installer and live
> system with SAINT added, so every step below is the same for it and for the official Fedora KDE ISO.
> **Use the SAINTOS ISO on this laptop only after it has passed the VM tests in
> [SAINTOS_RELEASE_STATUS.md](SAINTOS_RELEASE_STATUS.md)** (live boot, install to a virtual disk,
> installed boot) **and you've read that page's known limitations.** Until then, or if in doubt, use
> the official Fedora KDE ISO and add SAINT afterwards with `packaging/linux/setup_fedora.sh`.

## Try it in a VM first (no risk to the laptop)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\linux\vm\boot_test.ps1 -Iso dist\SAINTOS-x86_64.iso -Keep
```

This makes a throwaway VirtualBox VM with its own empty virtual disk, boots the ISO, and reports
PASS only when SAINTOS itself says the desktop is up and SAINT's self-test passed. To look around,
open the `SAINTOS-test` VM in VirtualBox. "Install to Hard Drive" inside the VM installs onto the
**virtual** disk only.

## 0. What the preflight found on this laptop (2026-10-28, from Windows)

`python tools/preflight.py --json preflight.json`, read-only:

| | |
|---|---|
| Machine | Lenovo 21FBSCWA00 · i7-13700HX (24 threads) · 31.7 GB RAM |
| GPU | NVIDIA RTX A1000 6 GB Laptop (driver 596.58) + Intel UHD |
| Disk | **one** internal disk: Samsung MZVL21T0HDLU 1 TB NVMe, GPT |
| Partitions | 1 EFI 0.3 GB · 2 MSR · 3 **C: NTFS 1021.8 GB (622 GB free)** · 4 Recovery 2.1 GB |
| Unallocated | **none** — the four partitions fill the disk |
| Linux partitions | **none found on the internal disk** |
| BitLocker / device encryption | **C: is encrypted, "waiting for activation"** (Shell property `System.Volume.BitLockerProtection` = 8, read 2026-10-29). Shrink C: only from Windows; save the recovery key once protection is active (step 1) |
| Boot mode / Secure Boot | **UEFI, Secure Boot on** (`%firmware_type%`, `HKLM\...\SecureBoot\State\UEFISecureBootEnabled=1`, read 2026-10-29). Leave it on |
| Fast Startup | **on** (`HiberbootEnabled=1`) — turn it off in step 1.4 |

**So Fedora is not installed yet.** The plan is to install it on this internal disk next to Windows,
following the steps below. The installer ISO (Fedora KDE 44, `Fedora-KDE-Desktop-Live-44-1.7.x86_64.iso`,
3.1 GB, from `dl.fedoraproject.org`) was downloaded to `Downloads\Fedora-KDE-44\`, and its SHA-256
was checked against Fedora's `CHECKSUM` file. Before step 3, also check that file's GPG signature
against Fedora's published keys (https://fedoraproject.org/security).

The "583 GB free" is space *inside* C:. It isn't unallocated disk space until Windows shrinks C:.

## 1. Prepare Windows (nothing is changed on disk yet)

1. **Back up** anything you can't lose to an external drive or cloud. A school laptop may also be
   managed: check with IT whether installing a second OS is allowed and whether the disk is
   managed/encrypted by policy.
2. **BitLocker / Device encryption.** Admin PowerShell: `manage-bde -status C:`, or Settings →
   Privacy & security → Device encryption. If it's on, **save the recovery key** (Microsoft account →
   Devices → BitLocker keys, or `manage-bde -protectors -get C:`) somewhere off this laptop. Firmware
   or boot changes can trigger a recovery-key prompt.
3. **Boot mode / Secure Boot.** `msinfo32` → *BIOS Mode* should read **UEFI**; note *Secure Boot State*.
   Leave Secure Boot as it is: Fedora boots with it on (signed shim). The NVIDIA driver then needs
   MOK enrollment (see LINUX_PORT_PLAN.md).
4. **Turn off Fast Startup** (Control Panel → Power Options → Choose what the power buttons do →
   uncheck *Turn on fast startup*). It leaves NTFS in a hibernated state that Linux must not write to.
5. **Check the recovery path:** Settings → System → Recovery shows the recovery environment is
   available (`reagentc /info` in an admin prompt shows *Enabled*). Don't move or delete partition 4.

✅ Checkpoint: backup done, recovery key saved (if encrypted), UEFI confirmed, Fast Startup off.

## 2. Make room (Windows Disk Management)

1. `Win+X` → **Disk Management** → right-click **C:** → **Shrink Volume…**
2. Enter an amount. Suggestion: **200,000 MB (~195 GB)** for Fedora + local models. 120 GB is a
   workable minimum for SAINT with one or two local models. Windows may offer less than the free space
   because of unmovable files (pagefile, hibernation, restore points). Don't use third-party tools
   to force it.
3. You now have a black **Unallocated** region after C:. **Don't create or format a partition in it**:
   the Fedora installer will.

Don't use `diskpart clean`, don't delete partitions, don't touch EFI or Recovery.

✅ Checkpoint: Disk Management shows C: smaller, Unallocated space, EFI/MSR/Recovery unchanged.
Reboot Windows once to confirm it still starts normally.

## 3. Install Fedora KDE

1. Check the ISO first: `Get-FileHash dist\SAINTOS-x86_64.iso -Algorithm SHA256` must match
   `dist\SHA256SUMS` (or the official Fedora CHECKSUM file for the Fedora ISO). Then write it to a USB
   stick with Fedora Media Writer → *Select .iso file*. **This erases the stick.**
2. Boot the stick (Lenovo: **F12** boot menu at power-on). Pick the UEFI entry for the USB.
3. In the live session, run `lsblk` and confirm you see `nvme0n1` with the four Windows partitions
   and the free space.
4. Start the installer. Choose the internal NVMe disk and the option that installs **into free
   space only** / *Use free space* (wording varies by installer version). **Read the summary before
   confirming:** it must say it creates new partitions in the free space, **reuses** the existing EFI
   system partition (`nvme0n1p1`) **without formatting it**, and leaves `nvme0n1p3` (Windows) and
   `nvme0n1p4` (Recovery) untouched. If the summary says "erase", "delete" or "reclaim" anything
   Windows uses, **go back**.
5. Optional: enable disk encryption (LUKS) for the Fedora partitions.
6. Install, reboot, remove the stick.

✅ Checkpoint: the GRUB menu lists Fedora and **Windows Boot Manager**. Boot Windows from it once
and confirm it starts. Boot Fedora and confirm it starts.

If Windows is missing from GRUB, don't run boot-repair tools. In Fedora run
`sudo grub2-mkconfig -o /boot/grub2/grub.cfg` (this only rewrites Fedora's menu file). The firmware
boot menu (F12) still lists *Windows Boot Manager* either way.

## 4. After installing

From Fedora:

```bash
sudo dnf upgrade --refresh
git clone https://github.com/IONX-Studios/SAINT.git ~/SAINT && cd ~/SAINT
git switch saintos/milestone-a-audit
python3 tools/preflight.py --json ~/preflight-fedora.json > ~/preflight-fedora.md   # read-only
packaging/linux/setup_fedora.sh
packaging/linux/run_tests.sh
```

On SAINTOS, SAINT is already installed in `/opt/saint` (skip `setup_fedora.sh`), and
`saintos-diagnose` collects the same report. Then check by hand and note results in `docs/MANUAL_TESTING.md`: Wi-Fi, Bluetooth, speakers and
mic, webcam, touchpad gestures, brightness keys, suspend/resume (lid close), external monitor (HDMI
and USB-C), and that Windows still boots from GRUB afterwards.

## Recovery

| Problem | What to do |
|---|---|
| Laptop boots straight into Windows | F12 → pick *Fedora*. In firmware setup, put *Fedora* first in boot order (this only changes the order). |
| BitLocker asks for the recovery key | Enter the key saved in step 1. Expected once after boot changes. |
| GRUB broken / Fedora won't boot | F12 → *Windows Boot Manager* still works. Boot the Fedora USB to repair Fedora. |
| SAINTOS boots to a black screen | GRUB → Troubleshooting → *basic graphics mode*; NVIDIA laptops may need the RPM Fusion driver after install. |
| Want Fedora gone | In Windows Disk Management delete **only** the Fedora partitions and extend C:. Remove the *Fedora* entry from the EFI partition later with care, or leave it (harmless). Get help before touching the EFI partition. |
