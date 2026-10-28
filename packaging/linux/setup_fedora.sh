#!/usr/bin/env bash
# SAINT — Fedora KDE development setup (the Linux counterpart of setup.bat).
#
#   packaging/linux/setup_fedora.sh            # asks before running dnf
#   packaging/linux/setup_fedora.sh --yes      # no questions (CI / reinstall)
#   packaging/linux/setup_fedora.sh --no-dnf   # Python side only
#   packaging/linux/setup_fedora.sh --cpu      # CPU PyTorch even if an NVIDIA GPU is found
#
# Safe to run again: it repairs .venv and skips what's already there.
# It never partitions, formats, or touches the bootloader, Secure Boot or Windows.
# The only privileged step is `sudo dnf install` of the packages in fedora-packages.txt.

set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"

YES=0; DNF=1; CPU=0
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --no-dnf) DNF=0 ;;
    --cpu) CPU=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

step() { printf '\n[%s] %s\n' "$1" "$2"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

step 1/7 "Checking the system"
if [ -r /etc/os-release ]; then . /etc/os-release; echo "OS: ${PRETTY_NAME:-unknown}"; fi
[ "${ID:-}" = "fedora" ] || warn "this script is written for Fedora; package names may differ on ${ID:-this system}."
echo "Session: ${XDG_CURRENT_DESKTOP:-unknown desktop} on ${XDG_SESSION_TYPE:-unknown}"
[ "$(id -u)" -ne 0 ] || { echo "Run this as your normal user, not root (it asks for sudo only for dnf)." >&2; exit 1; }

step 2/7 "System packages"
mapfile -t PKGS < <(grep -v '^\s*#' packaging/linux/fedora-packages.txt | awk 'NF{print $1}')
if [ "$DNF" -eq 1 ]; then
  MISSING=()
  for p in "${PKGS[@]}"; do rpm -q "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
  if [ "${#MISSING[@]}" -eq 0 ]; then
    echo "All system packages are installed."
  else
    echo "Missing: ${MISSING[*]}"
    if [ "$YES" -eq 1 ] || { read -r -p "Install them with sudo dnf? [y/N] " r && [[ "$r" =~ ^[Yy] ]]; }; then
      sudo dnf install -y --skip-unavailable "${MISSING[@]}"
    else
      warn "skipped; SAINT will report which features are unavailable (python -m core.platform_caps)."
    fi
  fi
else
  echo "Skipped (--no-dnf)."
fi

step 3/7 "Python"
PY=""
for c in python3.12 python3.13; do command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }; done
if [ -z "$PY" ]; then
  echo "SAINT needs Python 3.12 (or 3.13). Install it: sudo dnf install python3.12 python3.12-devel" >&2
  exit 1
fi
echo "Using $($PY --version) ($PY)"

step 4/7 "Virtual environment"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'; then
  [ -d .venv ] && [ ! -x .venv/bin/python ] && warn ".venv exists without bin/python (a Windows venv?) — moving it to .venv-windows"
  [ -d .venv ] && [ ! -x .venv/bin/python ] && mv .venv .venv-windows
  "$PY" -m venv .venv
fi
VPY="$ROOT/.venv/bin/python"
"$VPY" -m pip install --upgrade pip setuptools wheel

step 5/7 "PyTorch (for Kokoro from source; SAINT also runs Kokoro on onnxruntime without it)"
if "$VPY" -c 'import torch' >/dev/null 2>&1; then
  echo "PyTorch already installed: $("$VPY" -c 'import torch; print(torch.__version__, "cuda" if torch.cuda.is_available() else "cpu")')"
elif [ "$CPU" -eq 0 ] && command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA driver responds — installing CUDA 12.8 PyTorch."
  "$VPY" -m pip install --index-url https://download.pytorch.org/whl/cu128 torch torchaudio
else
  echo "No working NVIDIA driver (or --cpu) — installing CPU PyTorch."
  "$VPY" -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
fi

step 6/7 "SAINT's Python packages"
# No --upgrade: it would replace the CUDA torch wheel with PyPI's CPU one (see requirements.txt).
"$VPY" -m pip install -r requirements-linux.txt
"$VPY" -c "import requests, PySide6, PIL, psutil, numpy, sounddevice, soundfile, onnxruntime, faster_whisper, keyring, cryptography, zeroconf, segno; print('Core dependencies: OK')"
"$VPY" -c "import kokoro_onnx, misaki, en_core_web_sm, espeakng_loader; print('Kokoro voice packages: OK')" \
  || warn "Kokoro packages missing — SAINT falls back to espeak-ng."
if [ ! -f data/tts/kokoro-onnx/kokoro-v1.0.onnx ]; then
  echo "The Kokoro voice model is about 350 MB."
  if [ "$YES" -eq 1 ] || { read -r -p "Download it now? [y/N] " r && [[ "$r" =~ ^[Yy] ]]; }; then
    "$VPY" tools/get_kokoro_onnx.py || warn "Kokoro download failed; run: .venv/bin/python tools/get_kokoro_onnx.py"
  fi
fi

step 7/7 "Checks"
"$VPY" -m core.platform_caps | "$VPY" -c '
import json, sys
d = json.load(sys.stdin)
for c in d["capabilities"]:
    print(("  ok    " if c["available"] else "  MISSING ") + c["name"].ljust(14) + (c["provider"] or c["hint"]))'
command -v ollama >/dev/null 2>&1 && echo "Ollama: $(ollama --version 2>/dev/null)" \
  || echo "Ollama is not installed. Local chat needs it: see docs/LINUX_PORT_PLAN.md (no models are downloaded automatically)."
echo
echo "Run the tests:   packaging/linux/run_tests.sh"
echo "Start SAINT:     ./run.sh"
echo "Desktop entry:   packaging/linux/install_user.sh   (menu entry + optional autostart, per user)"
