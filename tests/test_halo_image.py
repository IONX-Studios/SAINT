"""
Halo image definition (packaging/arch): static checks that run on any OS, before a long mkarchiso
build. They catch a profile that would collide with pacman-owned files, live-only settings leaking
into installed systems, mismatched install paths between the ISO and the installer, CRLF scripts,
and Calamares settings that reference modules without configuration.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ARCH = ROOT / "packaging" / "arch"
PROFILE = ARCH / "profile"
AIROOTFS = PROFILE / "airootfs"
CAL = ARCH / "pkgbuilds" / "halo-calamares-config" / "calamares"


def _profiledef():
    return (PROFILE / "profiledef.sh").read_text(encoding="utf-8")


def _packages():
    lines = (PROFILE / "packages.x86_64").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")]


def test_profile_boots_bios_and_uefi_from_install_dir_halo():
    s = _profiledef()
    assert re.search(r"^install_dir=\"halo\"$", s, re.M)
    assert "'bios.syslinux'" in s and "'uefi.systemd-boot'" in s
    assert re.search(r'^iso_label="HALO_', s, re.M)
    assert "@VERSION@" in s                                   # filled in by build_iso.sh


def test_packages_list_has_the_desktop_saint_and_installer_but_not_discover():
    pkgs = _packages()
    assert len(pkgs) == len(set(pkgs)), "duplicate package"
    for p in ("base", "linux", "mkinitcpio-archiso", "syslinux", "plasma-desktop", "sddm", "plymouth", "saint",
              "halo-branding", "calamares", "halo-calamares-config", "paru-bin", "grub", "os-prober", "efibootmgr",
              "ollama", "rsync", "base-devel", "layer-shell-qt", "noto-fonts-emoji",
              "firefox", "dolphin", "konsole", "kate", "spectacle", "ark"):
        assert p in pkgs, p
    # Lean: no meta packages that pull Discover and dozens of apps, no server firmware.
    for p in ("discover", "plasma-discover", "plasma-meta", "plasma", "kde-applications-meta", "linux-firmware",
              "linux-firmware-mellanox", "linux-firmware-liquidio", "linux-firmware-nfp", "linux-firmware-qlogic"):
        assert p not in pkgs, p
    drop_in = (AIROOTFS / "etc" / "systemd" / "system" / "vboxservice.service.d" / "halo.conf").read_text(encoding="utf-8")
    assert "ConditionVirtualization=oracle" in drop_in
    conf = (AIROOTFS / "etc" / "mkinitcpio.conf.d" / "halo-live.conf").read_text(encoding="utf-8")
    assert 'COMPRESSION="zstd"' in conf


def test_boot_entries_use_the_install_dir_and_keep_the_iso_mounted():
    entries = list((PROFILE / "efiboot" / "loader" / "entries").glob("*.conf")) + [PROFILE / "syslinux" / "syslinux.cfg"]
    assert len(entries) == 3
    for e in entries:
        text = e.read_text(encoding="utf-8")
        assert "archisobasedir=%INSTALL_DIR%" in text and "archisosearchuuid=%ARCHISO_UUID%" in text, e.name
        # copytoram would unmount the ISO, and the installer copies the system from it.
        assert text.count("copytoram=n") == text.count("archisobasedir="), e.name
    assert "timeout 0" in (PROFILE / "efiboot" / "loader" / "loader.conf").read_text(encoding="utf-8")


def test_airootfs_never_ships_files_pacman_packages_own():
    """mkarchiso copies airootfs before installing packages; a file a package owns makes pacstrap fail
    ("exists in filesystem")."""
    owned = {"etc/os-release", "usr/lib/os-release", "etc/pacman.conf", "etc/pacman.d/mirrorlist",
             "etc/plymouth/plymouthd.conf", "etc/mkinitcpio.conf", "etc/sudoers", "etc/passwd", "etc/shadow",
             "etc/group", "etc/sddm.conf"}
    shipped = {p.relative_to(AIROOTFS).as_posix() for p in AIROOTFS.rglob("*") if p.is_file()}
    assert not shipped & owned
    assert not any(p.is_symlink() for p in ARCH.rglob("*")), "symlinks don't survive git on Windows"


def test_live_only_setup_is_guarded_and_never_baked_into_the_image():
    for unit in ("halo-live.service", "halo-pacman-init.service"):
        text = (AIROOTFS / "etc" / "systemd" / "system" / unit).read_text(encoding="utf-8")
        assert "ConditionKernelCommandLine=archisobasedir" in text, unit
    shipped = {p.relative_to(AIROOTFS).as_posix() for p in AIROOTFS.rglob("*") if p.is_file()}
    assert not any(s.startswith(("etc/sddm.conf.d", "etc/sudoers.d", "etc/polkit-1")) for s in shipped)
    setup = (AIROOTFS / "usr" / "local" / "lib" / "halo" / "live-setup").read_text(encoding="utf-8")
    assert "NOPASSWD" in setup and "[Autologin]" in setup and "Session=plasma" in setup
    assert '"/usr/local/lib/halo/live-setup"]="0:0:755"' in _profiledef()


def test_live_initramfs_preset_and_cleanup_agree():
    preset = (AIROOTFS / "etc" / "mkinitcpio.d" / "linux.preset").read_text(encoding="utf-8")
    conf = (AIROOTFS / "etc" / "mkinitcpio.conf.d" / "halo-live.conf").read_text(encoding="utf-8")
    assert "archiso_config='/etc/mkinitcpio.conf.d/halo-live.conf'" in preset
    hooks = re.search(r"HOOKS=\(([^)]*)\)", conf).group(1).split()
    assert hooks.index("plymouth") < hooks.index("archiso") and "archiso_loop_mnt" in hooks
    cleanup = (ARCH / "pkgbuilds" / "halo-calamares-config" / "halo-cleanup").read_text(encoding="utf-8")
    assert "rm -f /etc/mkinitcpio.conf.d/halo-live.conf" in cleanup
    assert "PRESETS=('default' 'fallback')" in cleanup
    assert "halo-live.service" in cleanup and "pacman-key --init" in cleanup


def test_installer_unpacks_from_where_the_iso_puts_the_system():
    unpack = (CAL / "modules" / "unpackfs.conf").read_text(encoding="utf-8")
    assert '"/run/archiso/bootmnt/halo/x86_64/airootfs.sfs"' in unpack
    assert '"/run/archiso/bootmnt/halo/boot/x86_64/vmlinuz-linux"' in unpack


def test_calamares_sequence_has_a_config_for_every_configurable_module():
    settings = (CAL / "settings.conf").read_text(encoding="utf-8")
    seq = re.findall(r"^  - ([\w@-]+)$", settings, re.M)
    assert seq[:6] == ["welcome", "locale", "keyboard", "partition", "users", "summary"]
    assert seq[-1] == "finished" and "shellprocess@halo-cleanup" in seq
    assert seq.index("shellprocess@halo-cleanup") < seq.index("initcpio") < seq.index("bootloader")
    # Secure Boot is set up after the boot loader, and the installer removes itself only after that.
    assert seq.index("bootloader") < seq.index("shellprocess@halo-secureboot") < seq.index("umount")
    sb = (ARCH / "pkgbuilds" / "halo-calamares-config" / "halo-secureboot").read_text(encoding="utf-8")
    assert sb.rstrip().endswith("exit 0") and "pacman -Rns" in sb and "pacman -Rns" not in (
        ARCH / "pkgbuilds" / "halo-calamares-config" / "halo-cleanup").read_text(encoding="utf-8")
    assert "mokutil --import" in sb and "--sbat /usr/share/grub/sbat.csv" in sb
    no_config = {"summary", "localecfg", "networkcfg", "hwclock", "umount"}
    for m in seq:
        if m in no_config:
            continue
        name = f"shellprocess-{m.split('@', 1)[1]}.conf" if m.startswith("shellprocess@") else f"{m}.conf"
        assert (CAL / "modules" / name).is_file(), name
    assert re.search(r"^branding: halo$", settings, re.M)
    assert (CAL / "branding" / "halo" / "branding.desc").is_file() and (CAL / "branding" / "halo" / "show.qml").is_file()


def test_calamares_yaml_parses():
    yaml = pytest.importorskip("yaml")
    for f in [CAL / "settings.conf", CAL / "branding" / "halo" / "branding.desc", *(CAL / "modules").glob("*.conf")]:
        assert isinstance(yaml.safe_load(f.read_text(encoding="utf-8")), dict), f.name


def test_pkgbuild_checksums_match_sources():
    for p in (ARCH / "pkgbuilds").glob("*/PKGBUILD"):
        text = p.read_text(encoding="utf-8")
        src = re.search(r"^source=\((.*?)\)", text, re.M | re.S).group(1).split()
        sums = re.search(r"^sha256sums=\((.*?)\)", text, re.M | re.S).group(1).split()
        assert len(src) == len(sums), p.parent.name
        assert re.search(r"^pkgver=0\.0\.0$", text, re.M), "build_iso.sh sets pkgver from packaging/arch/VERSION"
        local = [s for s in src if "://" not in s and "$" not in s and s not in ("saint-src.tar", "calamares.tar")]
        for s in local:
            assert (p.parent / s.strip('"')).is_file(), f"{p.parent.name}: {s}"


def test_halo_keeps_saints_branding():
    """The artwork is SAINT's (same generator, logo and colours as SAINTOS); only os-release says Halo."""
    pkgbuild = (ARCH / "pkgbuilds" / "halo-branding" / "PKGBUILD").read_text(encoding="utf-8")
    assert "make_branding.py art --name SAINT " in pkgbuild
    for icon in ("apps/saintos.png", "apps/halo.png"):
        assert icon in pkgbuild
    assert 'NAME="Halo"' in pkgbuild
    saint = (ARCH / "pkgbuilds" / "saint" / "PKGBUILD").read_text(encoding="utf-8")
    assert "SAINT.png" in saint and "apps/saint.png" in saint
    gen = (ROOT / "packaging" / "linux" / "branding" / "make_branding.py").read_text(encoding="utf-8")
    assert "ACCENT = (254, 170, 52)" in gen and 'ROOT / "SAINT.png"' in gen


def test_saint_package_keeps_the_tested_python_lock():
    lock = (ARCH / "pkgbuilds" / "saint" / "saint-runtime.lock").read_text(encoding="utf-8").lower()
    assert re.search(r"^pyside6[-_]essentials==", lock, re.M)
    assert re.search(r"^en[-_]core[-_]web[-_]sm==3\.8\.0$", lock, re.M)
    assert not re.search(r"^(torch|nvidia[-_])", lock, re.M)
    pkgbuild = (ARCH / "pkgbuilds" / "saint" / "PKGBUILD").read_text(encoding="utf-8")
    assert "uv python install 3.12" in pkgbuild and "/opt/saint/python/bin/python3.12" in pkgbuild


def test_scripts_have_lf_endings_and_parse():
    scripts = [p for p in ARCH.rglob("*") if p.is_file() and p.read_bytes()[:2] == b"#!"]
    scripts += list((ARCH / "pkgbuilds").glob("*/PKGBUILD")) + list((ARCH / "pkgbuilds").glob("*/*.install"))
    assert len(scripts) >= 10
    for s in scripts:
        assert b"\r" not in s.read_bytes(), f"{s.name} has CRLF line endings"
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash to parse the scripts with")
    for s in scripts:
        res = subprocess.run([bash, "-n", s.as_posix()], capture_output=True, text=True)
        assert res.returncode == 0, f"{s.name}: {res.stderr}"


def test_control_center_names_and_updates_halo(tmp_path, monkeypatch):
    from ui import saintos_center as sc
    rel = tmp_path / "os-release"
    rel.write_text('NAME="Halo"\nID=halo\nID_LIKE=arch\n', encoding="utf-8")
    monkeypatch.setattr(sc, "OS_RELEASE", rel)
    monkeypatch.setattr(sc.shutil, "which", lambda t: "/usr/bin/paru" if t == "paru" else None)
    assert sc.is_halo() and sc.product() == "Halo"
    assert "paru -Syu" in sc.update_command()[-1] and sc.update_command()[0] == "konsole"
    rel.write_text('NAME="SAINTOS"\nID=fedora\n', encoding="utf-8")
    assert not sc.is_halo() and sc.product() == "SAINTOS"
    assert "dnf upgrade" in sc.update_command()[-1]
