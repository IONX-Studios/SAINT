#!/usr/bin/env bash
# Build the Halo live/installer ISO (x86_64) on Arch Linux. Normally run inside a throwaway Arch
# container by build_in_container.sh, so any Linux host with podman or docker can build it.
#
#   packaging/arch/build_iso.sh [--workdir DIR] [--dist DIR] [--allow-dirty] [--live-mirrors] [--reuse-aur] [--no-validate]
#
#   1. host: Arch, x86_64, root; pacman pointed at the Arch Linux Archive snapshot in ARCH_SNAPSHOT
#      (--live-mirrors: today's mirrors instead, faster but not repeatable)
#   2. source: SAINT commit (+ tracked local changes with --allow-dirty) -> saint-src.tar
#   3. [halo] repo: saint, halo-branding, halo-calamares-config built with makepkg as an unprivileged
#      user; paru-bin and calamares (with its AUR dependencies) built from the AUR (--reuse-aur keeps
#      the ones the last build in this --workdir made: Calamares takes ~30 min to compile); repo-add
#   4. profile: packaging/arch/profile + pacman.conf ([halo] + snapshot) + enabled services
#   5. mkarchiso
#   6. dist/: Halo-x86_64.iso, SHA256SUMS, BUILD_INFO.json, package list, AUR commits
#   7. validate_iso.sh
#
# Never touches a disk device; everything is built as files under --workdir.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK="${HALO_WORKDIR:-/var/tmp/halo-build}"
DIST="$REPO/dist/halo"
ALLOW_DIRTY=0
VALIDATE=1
LIVE_MIRRORS=0
REUSE_AUR=0
MIN_FREE_GB=25
BUILDER=builder

while [ $# -gt 0 ]; do
  case "$1" in
    --workdir) WORK="$2"; shift 2 ;;
    --dist) DIST="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --live-mirrors) LIVE_MIRRORS=1; shift ;;
    --reuse-aur) REUSE_AUR=1; shift ;;
    --no-validate) VALIDATE=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "build_iso: unknown option $1" >&2; exit 2 ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'build_iso: ERROR: %s\n' "$*" >&2; exit 1; }
# The build user's home (paru's AUR clones) is in the workdir, so it outlives the container.
BHOME="$WORK/builder-home"
as_builder() { runuser -u "$BUILDER" -- env HOME="$BHOME" PKGDEST="$REPOD" "$@"; }
# Root reading the build user's clones: git refuses other users' repositories without safe.directory.
aur_commit() { git -c safe.directory='*' -C "$1" rev-parse HEAD 2>/dev/null || echo unrecorded; }

VERSION="$(tr -d '[:space:]' < "$HERE/VERSION")"
eval "$(grep -E '^snapshot=[0-9]{4}/[0-9]{2}/[0-9]{2}$' "$HERE/ARCH_SNAPSHOT")"
[ -n "${snapshot:-}" ] || die "packaging/arch/ARCH_SNAPSHOT has no snapshot=YYYY/MM/DD line"
if [ "$LIVE_MIRRORS" -eq 1 ]; then
  SERVER='https://geo.mirror.pkgbuild.com/$repo/os/$arch'
else
  SERVER="https://archive.archlinux.org/repos/$snapshot/\$repo/os/\$arch"
fi

#--------------------------------------------------------------------------
log "1/7 Host"
[ "$(uname -m)" = x86_64 ] || die "Halo ISOs are built on x86_64 (this is $(uname -m))."
. /etc/os-release
[ "${ID:-}" = arch ] || die "build on Arch Linux (found ${ID:-unknown}); use build_in_container.sh on other hosts."
[ "$(id -u)" -eq 0 ] || die "mkarchiso needs root."
mkdir -p "$WORK"
free_gb=$(df -P --block-size=1G "$WORK" | awk 'NR==2 {print $4}')
[ "$free_gb" -ge "$MIN_FREE_GB" ] || die "$WORK has ${free_gb} GB free; the build needs at least ${MIN_FREE_GB} GB."
# The build tools come from the same snapshot as the image, so the AUR packages are built against
# exactly the libraries the ISO ships (-Syyuu also downgrades a newer container to the snapshot).
echo "Server = $SERVER" > /etc/pacman.d/mirrorlist
pacman-key --init >/dev/null 2>&1 || true
pacman-key --populate archlinux >/dev/null 2>&1 || true
pacman -Syyuu --noconfirm --needed archiso base-devel git sudo uv python-pillow ttf-dejavu plymouth libisoburn
mkdir -p "$BHOME"
id "$BUILDER" >/dev/null 2>&1 || useradd -M -d "$BHOME" "$BUILDER"
chown -R "$BUILDER:" "$BHOME"
echo "$BUILDER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-halo-builder
chmod 0440 /etc/sudoers.d/90-halo-builder
echo "host: $PRETTY_NAME · $(nproc) CPUs · ${free_gb} GB free in $WORK · packages: $SERVER"

#--------------------------------------------------------------------------
log "2/7 Source"
cd "$REPO"
git config --global --add safe.directory "$REPO" 2>/dev/null || true
COMMIT="$(git rev-parse HEAD)"
DIRTY="$(git status --porcelain --untracked-files=no | wc -l)"
TREE="$COMMIT"
if [ "$DIRTY" -gt 0 ]; then
  [ "$ALLOW_DIRTY" -eq 1 ] || die "the working tree has $DIRTY modified tracked file(s); commit them or pass --allow-dirty"
  TREE="$(GIT_COMMITTER_NAME=halo-build GIT_COMMITTER_EMAIL=build@halo.invalid git stash create)"
  echo "building tracked working-tree changes on top of $COMMIT (stash $TREE)"
fi
echo "SAINT $COMMIT$([ "$DIRTY" -gt 0 ] && echo " + $DIRTY local change(s)") · Halo $VERSION · Arch snapshot $snapshot"

#--------------------------------------------------------------------------
log "3/7 [halo] package repository"
PKG="$WORK/pkgbuild"; REPOD="$WORK/repo"; AUR="$WORK/aur"
REUSE=0
if [ "$REUSE_AUR" -eq 1 ] && compgen -G "$REPOD/calamares-[0-9]*.pkg.tar.zst" >/dev/null \
   && compgen -G "$REPOD/paru-bin-[0-9]*.pkg.tar.zst" >/dev/null; then
  REUSE=1
fi
rm -rf "$PKG"
if [ "$REUSE" -eq 1 ]; then
  # Keep the AUR packages; ours are always rebuilt from this commit.
  find "$REPOD" -maxdepth 1 \( -name 'saint-*' -o -name 'halo-*' -o -name 'halo.*' \) -delete
  echo "reusing the last build's AUR packages: $(cd "$REPOD" && ls -1 calamares-[0-9]*.pkg.tar.zst paru-bin-[0-9]*.pkg.tar.zst | tr '\n' ' ')"
else
  rm -rf "$REPOD" "$AUR"
fi
mkdir -p "$PKG" "$REPOD" "$AUR"
# What goes into /opt/saint/app: the committed tree without the phone app, tests and build outputs.
git archive --format=tar -o "$PKG/saint-src.tar" "$TREE" -- . \
  ':(exclude)mobile' ':(exclude)tests' ':(exclude).github' ':(exclude)packaging/windows' ':(exclude)docs/screenshots'
for p in saint halo-branding halo-calamares-config; do
  cp -a "$HERE/pkgbuilds/$p" "$PKG/$p"
  sed -i "s/^pkgver=.*/pkgver=$VERSION/" "$PKG/$p/PKGBUILD"
done
cp "$PKG/saint-src.tar" "$PKG/saint/"; cp "$PKG/saint-src.tar" "$PKG/halo-branding/"
tar -cf "$PKG/halo-calamares-config/calamares.tar" -C "$PKG/halo-calamares-config/calamares" .
chown -R "$BUILDER:" "$PKG" "$REPOD" "$AUR"

# Ours. halo-calamares-config is only files; its dependency (calamares) is built below (-d).
(cd "$PKG/halo-branding" && as_builder makepkg -s --noconfirm --cleanbuild) || die "halo-branding didn't build"
(cd "$PKG/saint" && as_builder makepkg -s --noconfirm --cleanbuild) || die "saint didn't build"
(cd "$PKG/halo-calamares-config" && as_builder makepkg -d --noconfirm --cleanbuild) || die "halo-calamares-config didn't build"

# From the AUR: paru (prebuilt binary package), then calamares and whatever it needs from the AUR,
# built by paru. Every PKGBUILD's commit is recorded in dist/AUR_COMMITS.
if [ "$REUSE" -eq 0 ]; then
  rm -rf "$BHOME/.cache/paru"
  as_builder git clone --quiet https://aur.archlinux.org/paru-bin.git "$AUR/paru-bin"
  (cd "$AUR/paru-bin" && as_builder makepkg -si --noconfirm --cleanbuild) || die "paru-bin didn't build"
  as_builder paru -S --noconfirm --needed --skipreview calamares \
    || die "calamares (AUR) didn't build"
  {
    echo "paru-bin $(aur_commit "$AUR/paru-bin")"
    for d in "$BHOME/.cache/paru/clone"/*/; do
      [ -d "$d/.git" ] && echo "$(basename "$d") $(aur_commit "$d")"
    done
  } > "$WORK/AUR_COMMITS"
elif [ ! -s "$WORK/AUR_COMMITS" ]; then
  printf 'paru-bin unrecorded\ncalamares unrecorded\n' > "$WORK/AUR_COMMITS"
fi
# "name commit" on every line (an older build may have left a name without its commit).
awk 'NF { print $1, (NF > 1 ? $2 : "unrecorded") }' "$WORK/AUR_COMMITS" > "$WORK/AUR_COMMITS.tmp"
mv "$WORK/AUR_COMMITS.tmp" "$WORK/AUR_COMMITS"
cat "$WORK/AUR_COMMITS"

mapfile -t pkgs < <(find "$REPOD" -maxdepth 1 -name '*.pkg.tar.zst' ! -name '*-debug-*' | sort)
[ "${#pkgs[@]}" -ge 5 ] || die "[halo] repo has only ${#pkgs[@]} packages: ${pkgs[*]}"
repo-add --quiet "$REPOD/halo.db.tar.gz" "${pkgs[@]}"
printf '  %s\n' "${pkgs[@]##*/}"

#--------------------------------------------------------------------------
log "4/7 Profile"
P="$WORK/profile"
rm -rf "$P"
cp -a "$HERE/profile" "$P"
sed -i "s/@VERSION@/$VERSION/" "$P/profiledef.sh"
sed -e "s#@HALO_REPO@#$REPOD#" -e "s#@SERVER@#$SERVER#" "$HERE/profile/pacman.conf" > "$P/pacman.conf"
A="$P/airootfs"
S="$A/etc/systemd/system"
# Enabled services as plain links (the repo holds none: git on Windows doesn't keep symlinks).
mkdir -p "$S/multi-user.target.wants" "$S/graphical.target.wants" "$S/bluetooth.target.wants" "$S/network-online.target.wants"
ln -sf /usr/lib/systemd/system/graphical.target "$S/default.target"
ln -sf /usr/lib/systemd/system/sddm.service "$S/display-manager.service"
ln -sf /usr/lib/systemd/system/NetworkManager.service "$S/multi-user.target.wants/NetworkManager.service"
ln -sf /usr/lib/systemd/system/NetworkManager-dispatcher.service "$S/dbus-org.freedesktop.nm-dispatcher.service"
ln -sf /usr/lib/systemd/system/NetworkManager-wait-online.service "$S/network-online.target.wants/NetworkManager-wait-online.service"
ln -sf /usr/lib/systemd/system/bluetooth.service "$S/bluetooth.target.wants/bluetooth.service"
ln -sf /usr/lib/systemd/system/bluetooth.service "$S/dbus-org.bluez.service"
ln -sf /usr/lib/systemd/system/power-profiles-daemon.service "$S/graphical.target.wants/power-profiles-daemon.service"
ln -sf /etc/systemd/system/halo-live.service "$S/multi-user.target.wants/halo-live.service"
ln -sf /etc/systemd/system/halo-pacman-init.service "$S/multi-user.target.wants/halo-pacman-init.service"
ln -sf /usr/lib/systemd/system/saintos-boot-report.service "$S/graphical.target.wants/saintos-boot-report.service"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
install -d "$A/usr/share/halo"
python - "$A/usr/share/halo/build-info.json" <<PY
import json, sys
json.dump({"halo_version": "$VERSION", "arch_snapshot": "$snapshot", "live_mirrors": bool($LIVE_MIRRORS),
           "source_commit": "$COMMIT", "source_tree": "$TREE", "local_changes": $DIRTY,
           "aur": dict(l.split() for l in open("$WORK/AUR_COMMITS") if l.strip()),
           "built_at": "$BUILT_AT"}, open(sys.argv[1], "w"), indent=2)
PY

#--------------------------------------------------------------------------
log "5/7 mkarchiso — this takes a while"
rm -rf "$WORK/work" "$WORK/out"
set +e
mkarchiso -v -w "$WORK/work" -o "$WORK/out" "$P" 2>&1 | tee "$WORK/mkarchiso.log"
rc=${PIPESTATUS[0]}
set -e
[ "$rc" -eq 0 ] || die "mkarchiso failed (exit $rc); log: $WORK/mkarchiso.log"

#--------------------------------------------------------------------------
log "6/7 Release files"
ISO_SRC="$(ls -1 "$WORK"/out/*.iso 2>/dev/null | head -n1)"
[ -n "$ISO_SRC" ] && [ -s "$ISO_SRC" ] || die "mkarchiso produced no ISO in $WORK/out"
mkdir -p "$DIST"
ISO="$DIST/Halo-x86_64.iso"
cp "$ISO_SRC" "$ISO"
cp "$WORK/work/iso/halo/pkglist.x86_64.txt" "$DIST/Halo-x86_64.packages"
cp "$WORK/AUR_COMMITS" "$DIST/AUR_COMMITS"
(cd "$DIST" && sha256sum Halo-x86_64.iso > SHA256SUMS)
SHA="$(cut -d' ' -f1 "$DIST/SHA256SUMS")"
python - "$DIST/BUILD_INFO.json" "$A/usr/share/halo/build-info.json" <<PY
import json, sys
info = json.load(open(sys.argv[2]))
info.update({"iso": "Halo-x86_64.iso", "iso_bytes": $(stat -c %s "$ISO"), "sha256": "$SHA",
             "archiso": "$(pacman -Q archiso | cut -d' ' -f2)", "packages_file": "Halo-x86_64.packages"})
json.dump(info, open(sys.argv[1], "w"), indent=2)
PY
echo "$ISO ($(du -h "$ISO" | cut -f1)) sha256 $SHA"

#--------------------------------------------------------------------------
log "7/7 Validate"
if [ "$VALIDATE" -eq 1 ]; then
  "$HERE/validate_iso.sh" "$ISO" | tee "$DIST/VALIDATION.txt"
else
  echo "skipped (--no-validate)" | tee "$DIST/VALIDATION.txt"
fi
