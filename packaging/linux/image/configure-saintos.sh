#!/bin/bash
# Runs INSIDE the image during the KIWI build (called from the end of Fedora's config.sh when the
# SAINTOS-Live profile is active). Everything it needs was staged by build_iso.sh:
#   /opt/saint/app            SAINT (git archive of the built commit)
#   /opt/saint/wheelhouse     every Python wheel SAINT needs (installed offline, then removed)
#   /usr/share/saintos/...    branding, build info
# Fails the build on any error: a half-configured SAINTOS must never be shipped.
set -euxo pipefail

SAINT=/opt/saint
VERSION=$(cat /usr/share/saintos/VERSION)

#--------------------------------------------------------------------------
# SAINT: its own Python environment, offline from the staged wheelhouse
#--------------------------------------------------------------------------
/usr/bin/python3.12 -m venv "$SAINT/venv"
"$SAINT/venv/bin/python" -m pip install --no-index --find-links "$SAINT/wheelhouse" \
    --no-cache-dir -r "$SAINT/wheelhouse/saint-runtime.lock"
rm -rf "$SAINT/wheelhouse"
# Byte-compile once so the read-only image doesn't try at runtime.
"$SAINT/venv/bin/python" -m compileall -q "$SAINT/app" > /dev/null || true
# The image must be able to import SAINT's runtime, or the build fails here.
( cd "$SAINT/app" && QT_QPA_PLATFORM=offscreen "$SAINT/venv/bin/python" - <<'PY'
import importlib, sys
mods = ["PySide6.QtWidgets", "PySide6.QtSvg", "numpy", "requests", "psutil", "sounddevice", "soundfile",
        "onnxruntime", "faster_whisper", "kokoro_onnx", "keyring", "secretstorage", "jeepney", "segno",
        "zeroconf", "cryptography", "PIL", "core.paths", "core.platform_caps", "modules.desktop.linux.kwin",
        "ui.saintos_center"]
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:                 # report every failure, then fail
        bad.append(f"{m}: {type(e).__name__}: {e}")
if bad:
    print("SAINTOS image: SAINT runtime imports failed:\n  " + "\n  ".join(bad), file=sys.stderr)
    sys.exit(1)
import core.paths as p
assert p.system_install(), "SAINT in /opt must use the XDG data folder"
print("SAINTOS image: SAINT runtime imports OK")
PY
)
chmod 0755 /usr/bin/saint /usr/bin/saintos-control /usr/bin/saintos-diagnose /usr/libexec/saintos/*

#--------------------------------------------------------------------------
# Identity: SAINTOS, a Fedora Remix (Fedora trademark guidelines: not an official Fedora product)
#--------------------------------------------------------------------------
. /usr/lib/os-release
FEDORA_VERSION_ID="$VERSION_ID"
rm -f /etc/os-release
cat > /etc/os-release <<EOF
NAME="SAINTOS"
VERSION="${VERSION} (Fedora Remix ${FEDORA_VERSION_ID}, KDE Plasma)"
ID=fedora
ID_LIKE="fedora"
VERSION_ID=${FEDORA_VERSION_ID}
PLATFORM_ID="${PLATFORM_ID}"
PRETTY_NAME="SAINTOS ${VERSION} (Fedora Remix ${FEDORA_VERSION_ID})"
ANSI_COLOR="0;38;2;254;170;52"
LOGO=saintos
CPE_NAME="${CPE_NAME}"
HOME_URL="https://github.com/IONX-Studios/SAINT"
DOCUMENTATION_URL="https://github.com/IONX-Studios/SAINT/tree/saintos/milestone-a-audit/docs"
BUG_REPORT_URL="https://github.com/IONX-Studios/SAINT/issues"
VARIANT="SAINTOS"
VARIANT_ID=saintos
SAINTOS_VERSION="${VERSION}"
SUPPORT_END=${SUPPORT_END:-}
EOF
echo "SAINTOS ${VERSION} (Fedora Remix ${FEDORA_VERSION_ID}) \\n \\l" > /etc/issue
echo saintos > /etc/hostname

#--------------------------------------------------------------------------
# Artwork: icons, wallpaper, boot splash
#--------------------------------------------------------------------------
B=/usr/share/saintos/branding
for n in 16 32 48 64 128 256 512; do
    install -Dm0644 "$B/logo/saintos-logo-$n.png" "/usr/share/icons/hicolor/${n}x${n}/apps/saintos.png"
done
install -Dm0644 "$SAINT/app/SAINT.png" /usr/share/icons/hicolor/512x512/apps/saint.png
install -Dm0644 "$B/logo/saintos-logo-256.png" /usr/share/pixmaps/saintos.png
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
mkdir -p /usr/share/wallpapers/SAINTOS/contents/images
for f in "$B"/wallpaper/SAINTOS-*.png; do
    size=$(basename "$f" .png); size=${size#SAINTOS-}
    install -Dm0644 "$f" "/usr/share/wallpapers/SAINTOS/contents/images/${size}.png"
done
install -Dm0644 "$B/wallpaper/SAINTOS-1920x1080.png" /usr/share/wallpapers/SAINTOS/contents/screenshot.png

# Plymouth: Fedora's spinner theme with SAINTOS colours and wordmark (initrd is built after this script).
SPIN=/usr/share/plymouth/themes/spinner
THEME=/usr/share/plymouth/themes/saintos
mkdir -p "$THEME"
cp -a "$SPIN"/. "$THEME"/
mv "$THEME/spinner.plymouth" "$THEME/saintos.plymouth"
sed -i -e 's/^Name=.*/Name=SAINTOS/' -e 's/^Description=.*/Description=SAINTOS boot splash/' \
       -e "s#$SPIN#$THEME#g" -e 's/^BackgroundStartColor=.*/BackgroundStartColor=0x0a0b0d/' \
       -e 's/^BackgroundEndColor=.*/BackgroundEndColor=0x0a0b0d/' "$THEME/saintos.plymouth"
install -m0644 "$B/plymouth/watermark.png" "$THEME/watermark.png"
plymouth-set-default-theme saintos

#--------------------------------------------------------------------------
# Services
#--------------------------------------------------------------------------
systemctl preset saintos-boot-report.service
systemctl enable saintos-boot-report.service

echo "SAINTOS ${VERSION} configured."
