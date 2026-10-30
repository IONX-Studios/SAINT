#!/usr/bin/env bash
# Build the Halo ISO on any x86_64 Linux host with podman (or docker): runs build_iso.sh inside a
# throwaway, privileged Arch Linux container (mkarchiso needs root and mounts inside its chroot).
#
#   packaging/arch/build_in_container.sh [--workdir DIR] [build_iso.sh options...]
#
# The container's storage, the package cache and the build tree all live under --workdir (default
# /var/tmp/halo-build), so a live session's small RAM overlay is never used. Output: <repo>/dist/halo/.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK="${HALO_WORKDIR:-/var/tmp/halo-build}"
IMAGE="docker.io/library/archlinux:base-devel"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --workdir) WORK="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"
mkdir -p "$WORK/build" "$WORK/pacman-cache" "$REPO/dist/halo"
if command -v podman >/dev/null; then
  ENGINE=(podman --root "$WORK/containers" --runroot "$WORK/containers-run")
elif command -v docker >/dev/null; then
  ENGINE=(docker)
else
  echo "build_in_container: needs podman or docker" >&2
  exit 1
fi

$SUDO "${ENGINE[@]}" run --rm --privileged --network host \
  -v "$REPO:/src" \
  -v "$WORK/build:/work" \
  -v "$WORK/pacman-cache:/var/cache/pacman/pkg" \
  "$IMAGE" \
  bash /src/packaging/arch/build_iso.sh --workdir /work --dist /src/dist/halo "${ARGS[@]}"
