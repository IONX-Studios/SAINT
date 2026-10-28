#!/usr/bin/env bash
# Build the SAINTOS live installer ISO (x86_64) on a Fedora host.
#
#   packaging/linux/image/build_iso.sh [--workdir DIR] [--dist DIR] [--allow-dirty] [--keep-work]
#
# What it does (each step fails early with a clear message):
#   1. checks the host: Fedora, x86_64, root via sudo, kiwi + tools, free space
#   2. records the source: SAINT commit (and whether the tree had local changes)
#   3. fetches Fedora's official KIWI descriptions at the commit pinned in UPSTREAM
#   4. stages the SAINTOS layer into a copy of them: SAINT (/opt/saint/app), an offline wheelhouse
#      for its Python packages, the rootfs overlay, generated branding, build info
#   5. runs KIWI with the SAINTOS-Live profile (= Fedora KDE-Desktop-Live + SAINTOS)
#   6. writes dist/: SAINTOS-x86_64.iso, SHA256SUMS, BUILD_INFO.json, RELEASE_NOTES.md, packages list
#   7. runs validate_iso.sh on the result
#
# Never touches any disk device: KIWI builds into files under --workdir (loop devices only for its
# own image files). Re-running starts from a fresh workspace; the source tree is never modified.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
WORK="${SAINTOS_WORKDIR:-/var/tmp/saintos-build}"
DIST="$REPO/dist"
ALLOW_DIRTY=0
KEEP_WORK=0
MIN_FREE_GB=30

while [ $# -gt 0 ]; do
  case "$1" in
    --workdir) WORK="$2"; shift 2 ;;
    --dist) DIST="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --keep-work) KEEP_WORK=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "build_iso: unknown option $1" >&2; exit 2 ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'build_iso: ERROR: %s\n' "$*" >&2; exit 1; }

eval "$(grep -E '^(url|branch|commit|fedora_release)=[A-Za-z0-9:/._-]+$' "$HERE/UPSTREAM")"
[ -n "${commit:-}" ] && [ -n "${url:-}" ] || die "packaging/linux/image/UPSTREAM is missing url/commit"
SAINTOS_VERSION="$(cat "$HERE/VERSION")"

#--------------------------------------------------------------------------
log "1/7 Host checks"
[ "$(uname -m)" = x86_64 ] || die "SAINTOS ISOs are built on x86_64 (this is $(uname -m))."
. /etc/os-release
[ "${ID:-}" = fedora ] || die "build on Fedora (found ${ID:-unknown}); KIWI's Fedora descriptions expect Fedora's dnf5 and keys."
[ "${VERSION_ID:-0}" -ge "$fedora_release" ] 2>/dev/null || echo "note: host is Fedora ${VERSION_ID}, image is Fedora $fedora_release"
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"
[ -z "$SUDO" ] || sudo true < /dev/null 2>/dev/null || die "KIWI needs root: run as root or with sudo that doesn't prompt (NOPASSWD, or a live session)."
missing=()
for t in kiwi-ng git python3.12 xorriso sha256sum rpm; do command -v "$t" >/dev/null || missing+=("$t"); done
[ -f /usr/share/distribution-gpg-keys/fedora/RPM-GPG-KEY-fedora-${fedora_release}-primary ] || missing+=("distribution-gpg-keys")
if [ ${#missing[@]} -gt 0 ]; then
  die "missing: ${missing[*]}
  Install: sudo dnf install -y kiwi kiwi-systemdeps distribution-gpg-keys git python3.12 xorriso"
fi
mkdir -p "$WORK"
# pip and temporary files go to the workspace too (a live session's root has only a small overlay).
export TMPDIR="$WORK/tmp" PIP_CACHE_DIR="$WORK/pip-cache"; mkdir -p "$TMPDIR" "$PIP_CACHE_DIR"
free_gb=$(df -P --block-size=1G "$WORK" | awk 'NR==2 {print $4}')
[ "$free_gb" -ge "$MIN_FREE_GB" ] || die "$WORK has ${free_gb} GB free; the build needs at least ${MIN_FREE_GB} GB."
echo "host: $PRETTY_NAME · $(nproc) CPUs · $(free -g | awk '/Mem:/ {print $2}') GB RAM · ${free_gb} GB free in $WORK"

#--------------------------------------------------------------------------
log "2/7 Source"
cd "$REPO"
git config --global --add safe.directory "$REPO" 2>/dev/null || true
COMMIT="$(git rev-parse HEAD)"
DIRTY="$(git status --porcelain --untracked-files=no | wc -l)"
TREE="$COMMIT"
if [ "$DIRTY" -gt 0 ]; then
  [ "$ALLOW_DIRTY" -eq 1 ] || die "the working tree has $DIRTY modified tracked file(s); commit them or pass --allow-dirty"
  TREE="$(git stash create)"          # a commit of the working tree; changes nothing in the checkout
  echo "building tracked working-tree changes on top of $COMMIT (stash $TREE)"
fi
echo "SAINT $COMMIT$([ "$DIRTY" -gt 0 ] && echo " + $DIRTY local change(s)") · SAINTOS $SAINTOS_VERSION"

#--------------------------------------------------------------------------
log "3/7 Fedora KIWI descriptions ($branch @ ${commit:0:12})"
$SUDO rm -rf "$WORK/desc" "$WORK/out"
mkdir -p "$WORK"
if [ ! -d "$WORK/upstream/.git" ]; then
  git clone --branch "$branch" "$url" "$WORK/upstream"
fi
git -C "$WORK/upstream" fetch --quiet origin "$branch"
git -C "$WORK/upstream" -c advice.detachedHead=false checkout --quiet "$commit"
[ "$(git -C "$WORK/upstream" rev-parse HEAD)" = "$commit" ] || die "upstream checkout isn't $commit"
cp -a "$WORK/upstream" "$WORK/desc"
rm -rf "$WORK/desc/.git"
D="$WORK/desc"

#--------------------------------------------------------------------------
log "4/7 Stage the SAINTOS layer"
# 4a. SAINT itself (what's committed — tests, the iOS app and dev tools stay out of the image)
mkdir -p "$D/root/opt/saint/app"
git archive "$TREE" | tar -x -C "$D/root/opt/saint/app" \
  --exclude='mobile' --exclude='tests' --exclude='tools/dev' --exclude='.github' --exclude='packaging/windows'

# 4b. Python wheelhouse + exact lock, built here so the image install is offline and recorded
WH="$WORK/wheelhouse"
rm -rf "$WH" "$WORK/lockvenv"; mkdir -p "$WH"
python3.12 -m pip download --quiet --no-deps -d "$WH" \
  "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
python3.12 -m pip wheel --quiet -w "$WH" --find-links "$WH" -r "$HERE/saint-runtime.txt"
python3.12 -m venv "$WORK/lockvenv"
"$WORK/lockvenv/bin/python" -m pip install --quiet --no-index --find-links "$WH" -r "$HERE/saint-runtime.txt"
"$WORK/lockvenv/bin/python" -m pip freeze --all --exclude pip --exclude setuptools --exclude wheel \
  | sed 's/ @ file:.*//' > "$WH/saint-runtime.lock"
grep -qiE '^pyside6[-_]essentials==' "$WH/saint-runtime.lock" || die "lock file has no PySide6-Essentials"
! grep -qiE '^(torch|nvidia[-_]|spacy[-_]curated[-_]transformers)' "$WH/saint-runtime.lock"   || die "the lock pulls PyTorch/CUDA ($(grep -ciE '^(torch|nvidia[-_])' "$WH/saint-runtime.lock") packages); check saint-runtime.txt"
# Fail here (minutes) rather than inside KIWI (much later) if SAINT's voice/UI stack can't import.
QT_QPA_PLATFORM=offscreen "$WORK/lockvenv/bin/python" -c   "import PySide6.QtWidgets, numpy, onnxruntime, faster_whisper, kokoro_onnx, misaki.en, en_core_web_sm, keyring, secretstorage, jeepney"   || die "SAINT's Python runtime doesn't import in the lock venv"
mkdir -p "$D/root/opt/saint"
cp -a "$WH" "$D/root/opt/saint/wheelhouse"
echo "$(wc -l < "$WH/saint-runtime.lock") Python packages locked"

# 4c. rootfs overlay, branding, image script, build info
cp -a "$REPO/packaging/linux/rootfs/." "$D/root/"
"$WORK/lockvenv/bin/python" "$REPO/packaging/linux/branding/make_branding.py" "$D/root/usr/share/saintos/branding"
install -Dm0755 "$HERE/configure-saintos.sh" "$D/root/usr/libexec/saintos/configure-image.sh"
echo "$SAINTOS_VERSION" > "$D/root/usr/share/saintos/VERSION"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3.12 - "$D/root/usr/share/saintos/build-info.json" <<PY
import json, sys
json.dump({"saintos_version": "$SAINTOS_VERSION", "fedora_release": "$fedora_release",
           "source_commit": "$COMMIT", "source_tree": "$TREE", "local_changes": $DIRTY,
           "upstream_kiwi_descriptions": {"url": "$url", "branch": "$branch", "commit": "$commit"},
           "kiwi_profile": "SAINTOS-Live", "built_at": "$BUILT_AT", "build_host": "$PRETTY_NAME"},
          open(sys.argv[1], "w"), indent=2)
PY

# 4d. hook the layer into the descriptions (a copy; upstream files are otherwise untouched)
mkdir -p "$D/saintos"
cp "$HERE/saintos.xml" "$D/saintos/saintos.xml"
python3.12 - "$D/Fedora.kiwi" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s = s.replace('<image schemaversion="7.4" name="Fedora">',
              '<image schemaversion="7.4" name="SAINTOS" displayname="SAINTOS">', 1)
s = s.replace('\t<include from="this://./components/liveinstall.xml"/>',
              '\t<include from="this://./components/liveinstall.xml"/>\n\t<include from="this://./saintos/saintos.xml"/>', 1)
assert 'name="SAINTOS"' in s and "saintos/saintos.xml" in s, "Fedora.kiwi layout changed upstream"
open(p, "w", encoding="utf-8").write(s)
PY
python3.12 - "$D/config.sh" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
hook = ('\n#======================================\n# SAINTOS layer (packaging/linux/image)\n'
        '#--------------------------------------\n'
        'if [[ "$kiwi_profiles" == *"SAINTOS"* ]]; then\n\t/usr/libexec/saintos/configure-image.sh\nfi\n\n')
assert s.rstrip().endswith("exit 0"), "config.sh no longer ends with exit 0; review the hook"
i = s.rstrip().rfind("exit 0")
open(p, "w", encoding="utf-8").write(s[:i] + hook + s[i:])
PY
echo "description staged in $D"

#--------------------------------------------------------------------------
log "5/7 KIWI build (SAINTOS-Live) — this takes a while"
VOLID="SAINTOS-${fedora_release}-x86_64"
set +e
$SUDO kiwi-ng --shared-cache-dir "$WORK/kiwi-cache" --type=iso --profile=SAINTOS-Live --kiwi-file=Fedora.kiwi --color-output system build \
  --description "$D" --target-dir "$WORK/out" \
  --set-type-attr "volid=$VOLID" --set-type-attr "application_id=SAINTOS" \
  --set-type-attr "publisher=SAINTOS (unofficial Fedora Remix)" 2>&1 | tee "$WORK/kiwi.log"
rc=${PIPESTATUS[0]}
set -e
[ "$rc" -eq 0 ] || die "KIWI failed (exit $rc); full log: $WORK/kiwi.log, KIWI's own: $WORK/out/build/image-root.log"

#--------------------------------------------------------------------------
log "6/7 Release files"
ISO_SRC="$(ls -1 "$WORK"/out/*.iso 2>/dev/null | head -n1)"
[ -n "$ISO_SRC" ] && [ -s "$ISO_SRC" ] || die "KIWI reported success but produced no ISO in $WORK/out"
mkdir -p "$DIST"
ISO="$DIST/SAINTOS-x86_64.iso"
$SUDO cp "$ISO_SRC" "$ISO"
$SUDO chown "$(id -u):$(id -g)" "$ISO"
cp "$WH/saint-runtime.lock" "$DIST/saint-runtime.lock"
PKGS="$(ls -1 "$WORK"/out/*.packages 2>/dev/null | head -n1)"
[ -n "$PKGS" ] && cp "$PKGS" "$DIST/SAINTOS-x86_64.packages"
( cd "$DIST" && sha256sum SAINTOS-x86_64.iso > SHA256SUMS )
SHA="$(cut -d' ' -f1 "$DIST/SHA256SUMS")"
SIZE="$(stat -c %s "$ISO")"
python3.12 - "$DIST/BUILD_INFO.json" "$D/root/usr/share/saintos/build-info.json" <<PY
import json, sys
info = json.load(open(sys.argv[2]))
info.update({"iso": "SAINTOS-x86_64.iso", "iso_bytes": $SIZE, "sha256": "$SHA", "volume_id": "$VOLID",
             "kiwi_version": "$(kiwi-ng --version 2>/dev/null | head -n1)",
             "packages_file": "SAINTOS-x86_64.packages", "python_lock": "saint-runtime.lock"})
json.dump(info, open(sys.argv[1], "w"), indent=2)
PY
sed -e "s/@VERSION@/$SAINTOS_VERSION/g" -e "s/@FEDORA@/$fedora_release/g" -e "s/@COMMIT@/$COMMIT/g" \
    -e "s/@SHA256@/$SHA/g" -e "s/@BUILT_AT@/$BUILT_AT/g" "$HERE/RELEASE_NOTES.md.in" > "$DIST/RELEASE_NOTES.md"

#--------------------------------------------------------------------------
log "7/7 Validate"
"$HERE/validate_iso.sh" "$ISO" | tee "$DIST/VALIDATION.txt"

[ "$KEEP_WORK" -eq 1 ] || $SUDO rm -rf "$WORK/out/build"
log "Done"
echo "ISO:     $ISO ($(numfmt --to=iec "$SIZE"))"
echo "SHA-256: $SHA"
echo "Source:  $COMMIT"
