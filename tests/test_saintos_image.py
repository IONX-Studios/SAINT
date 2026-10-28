"""
SAINTOS image definition (packaging/linux/image, rootfs, branding): static checks that run on any
OS. They catch a broken XML include, a desktop entry KDE would reject, a unit file typo or a
script that would fail on CRLF line endings — before a 40-minute image build does.
"""

import configparser
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "packaging" / "linux" / "image"
FS = ROOT / "packaging" / "linux" / "rootfs"


def test_kiwi_layer_is_valid_and_builds_on_fedora_kde_live():
    tree = ET.parse(IMG / "saintos.xml")
    prof = tree.find("./profiles/profile[@name='SAINTOS-Live']")
    assert prof is not None
    assert [r.get("profile") for r in prof.findall("requires")] == ["KDE-Desktop-Live"]
    pkgs = [p.get("name") for p in tree.iter("package")]
    assert len(pkgs) == len(set(pkgs)), "duplicate package"
    assert "python3.12" in pkgs and "portaudio" in pkgs and "tesseract" in pkgs
    for packages in tree.iter("packages"):
        assert packages.get("profiles") == "SAINTOS-Live"


def test_upstream_is_pinned_to_a_full_commit():
    text = (IMG / "UPSTREAM").read_text(encoding="utf-8")
    assert re.search(r"^commit=[0-9a-f]{40}$", text, re.M)
    assert re.search(r"^url=https://forge\.fedoraproject\.org/", text, re.M)
    assert re.search(r"^fedora_release=\d+$", text, re.M)


def test_runtime_requirements_exclude_windows_and_torch():
    reqs = [l.split("#")[0].strip().lower() for l in (IMG / "saint-runtime.txt").read_text().splitlines()]
    reqs = [r for r in reqs if r]
    for banned in ("pywin32", "uiautomation", "pycaw", "winrt", "torch", "pyinstaller", "pytest", "pyautogui"):
        assert not any(r.startswith(banned) for r in reqs), banned
    assert any(r.startswith("pyside6-essentials") for r in reqs)
    assert "misaki[en]" not in " ".join(reqs), "misaki[en] pulls PyTorch + CUDA via spacy-curated-transformers"


@pytest.mark.parametrize("name", ["saint.desktop", "saintos-control.desktop"])
def test_desktop_entries(name):
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read(FS / "usr" / "share" / "applications" / name, encoding="utf-8")
    e = cp["Desktop Entry"]
    assert e["Type"] == "Application" and e["Name"] and e["Icon"]
    assert e["Exec"].split()[0].startswith("/usr/bin/")
    assert (FS / e["Exec"].split()[0].lstrip("/")).exists(), "Exec points at a file the image doesn't ship"
    assert e["Categories"].endswith(";")


def test_units_reference_shipped_files():
    for unit in (FS / "usr" / "lib" / "systemd").rglob("*.service"):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        cp.read(unit, encoding="utf-8")
        exe = cp["Service"]["ExecStart"].split()[0]
        assert (FS / exe.lstrip("/")).exists(), f"{unit.name}: {exe} isn't in the rootfs"
        assert "WantedBy" in cp["Install"]
    user = (FS / "usr/lib/systemd/user/saint.service").read_text()
    assert "User=" not in user and "sudo" not in user                       # user service, no root


def test_scripts_are_lf_with_shebang():
    scripts = [IMG / "build_iso.sh", IMG / "validate_iso.sh", IMG / "configure-saintos.sh"]
    scripts += [p for p in (FS / "usr" / "bin").iterdir()] + [p for p in (FS / "usr" / "libexec" / "saintos").iterdir()]
    for s in scripts:
        data = s.read_bytes()
        assert data.startswith(b"#!"), s.name
        assert b"\r\n" not in data, f"{s.name} has CRLF line endings"


def test_gitattributes_keep_image_files_lf():
    attrs = (ROOT / ".gitattributes").read_text()
    assert "packaging/linux/rootfs/** text eol=lf" in attrs and "packaging/linux/image/** text eol=lf" in attrs


def test_wallpaper_metadata_and_plasma_script():
    meta = json.loads((FS / "usr/share/wallpapers/SAINTOS/metadata.json").read_text())
    assert meta["KPlugin"]["Id"] == "SAINTOS"
    js = (FS / "usr/share/plasma/shells/org.kde.plasma.desktop/contents/updates/saintos-defaults.js").read_text()
    assert "/usr/share/wallpapers/SAINTOS/" in js and "saint.desktop" in js


def test_configure_script_never_partitions_or_touches_boot():
    text = (IMG / "configure-saintos.sh").read_text()
    for word in ("mkfs", "parted", "fdisk", "wipefs", "grub2-install", "efibootmgr", "dd if="):
        assert word not in text, word
    assert "--no-index" in text, "the image installs Python packages offline from the staged wheelhouse"
    assert 'NAME="SAINTOS"' in text and "Fedora Remix" in text


def test_install_kickstart_refuses_real_hardware():
    ks = (ROOT / "packaging/linux/vm/install-test.ks").read_text()
    pre = ks.split("%pre", 1)[1].split("%end", 1)[0]
    assert "systemd-detect-virt" in pre and "VBOX HARDDISK" in pre and "--erroronfail" in ks.split("%pre", 1)[1]


def test_branding_generator_runs(tmp_path):
    from PIL import Image
    import importlib.util
    spec = importlib.util.spec_from_file_location("mb", ROOT / "packaging/linux/branding/make_branding.py")
    mb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mb)
    mb.WALLPAPER_SIZES = [(640, 360)]
    assert mb.main(["x", str(tmp_path)]) == 0
    assert Image.open(tmp_path / "wallpaper" / "SAINTOS-640x360.png").size == (640, 360)
    assert Image.open(tmp_path / "logo" / "saintos-logo-256.png").mode == "RGBA"
    assert (tmp_path / "plymouth" / "watermark.png").exists()


def test_boot_report_selftest_is_offline():
    text = (FS / "usr/libexec/saintos/boot-report").read_text()
    assert "SAINT_SELFTEST_NO_VOICE=1" in text, "the boot test must not download voice models"
    assert "SAINT_DATA_DIR=" in text and "timeout " in text


def test_configure_doesnt_reuse_os_release_variable_names():
    # `. /usr/lib/os-release` defines VERSION, NAME, ID...; the SAINTOS version must survive it.
    text = (IMG / "configure-saintos.sh").read_text()
    before = text.split(". /usr/lib/os-release", 1)[0]
    assert not re.search(r"^\s*(VERSION|NAME|ID|VERSION_ID)=", before, re.M)


def test_os_release_fields_from_fedora_are_optional():
    # Fedora 44 dropped PLATFORM_ID; under `set -u` a missing field must not stop the build.
    text = (IMG / "configure-saintos.sh").read_text()
    for field in ("PLATFORM_ID", "CPE_NAME", "SUPPORT_END"):
        uses = re.findall(r"\$\{" + field + r"([^}]*)\}", text)
        assert uses and all(u.startswith(":-") for u in uses), field
