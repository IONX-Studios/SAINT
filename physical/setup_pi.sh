#!/usr/bin/env bash
# setup_pi.sh - one-shot, re-runnable setup for SAINT on a Raspberry Pi (Raspberry Pi OS / Debian).
#
#   chmod +x setup_pi.sh && ./setup_pi.sh
#
# Installs system packages, pigpio (+ pigpiod service), a Python venv with all
# dependencies, Kokoro TTS model + voices, and the local LLM model.
# Safe to run again: finished steps are detected and skipped.
#
# Options (environment variables):
#   SKIP_LLM=1        don't build llama-cpp-python or download the LLM (saves ~30 min + 800 MB)
#   SKIP_APT=1        don't touch apt packages
#   KOKORO_FULL=1     download the full-precision Kokoro model (325 MB) instead of int8 (92 MB)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
KOKORO_DIR="$SCRIPT_DIR/models/kokoro"
LLM_DIR="$SCRIPT_DIR/models/ModelFiles"
KOKORO_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
LLM_URL="https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf"
LLM_FILE="$LLM_DIR/llama-3.2-1b-instruct-q4_k_m.gguf"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
    warn "Run this as your normal user (it uses sudo when needed), not as root."
    exit 1
fi

ARCH="$(uname -m)"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
say "SAINT setup in $SCRIPT_DIR ($MODEL, $ARCH)"
if [ "$ARCH" != "aarch64" ]; then
    warn "64-bit Raspberry Pi OS is strongly recommended: onnxruntime/Kokoro wheels are only published for aarch64."
fi
case "$MODEL" in
    *"Raspberry Pi 5"*) warn "pigpio does not support the Raspberry Pi 5 GPIO chip; servos will run in simulation there." ;;
esac

# ---------- 1. system packages ----------
if [ "${SKIP_APT:-0}" != "1" ]; then
    say "Installing system packages (sudo)"
    sudo apt-get update -y
    sudo apt-get install -y \
        python3 python3-venv python3-pip python3-dev python3-tk \
        portaudio19-dev libportaudio2 libsndfile1 \
        espeak-ng libespeak-ng1 flac alsa-utils \
        curl git build-essential cmake pkg-config
fi

# ---------- 2. pigpio + pigpiod ----------
if ! command -v pigpiod >/dev/null 2>&1; then
    say "Installing pigpio"
    if ! sudo apt-get install -y pigpio; then
        warn "pigpio not in apt; building from source (github.com/joan2937/pigpio)"
        tmp="$(mktemp -d)"
        git clone --depth 1 https://github.com/joan2937/pigpio.git "$tmp/pigpio"
        make -C "$tmp/pigpio" -j"$(nproc)"
        sudo make -C "$tmp/pigpio" install
        rm -rf "$tmp"
    fi
fi
if command -v pigpiod >/dev/null 2>&1; then
    if ! systemctl cat pigpiod.service >/dev/null 2>&1; then
        say "Creating pigpiod systemd service (localhost only)"
        sudo tee /etc/systemd/system/pigpiod.service >/dev/null <<EOF
[Unit]
Description=Pigpio daemon
After=network.target

[Service]
Type=forking
ExecStart=$(command -v pigpiod) -l
ExecStop=/bin/systemctl kill pigpiod

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
    fi
    say "Enabling pigpiod"
    sudo systemctl enable --now pigpiod || warn "could not start pigpiod (servos will be simulated until it runs)"
else
    warn "pigpiod not available; SAINT will run with simulated servos."
fi

# ---------- 3. Python venv + packages ----------
if [ ! -x "$VENV/bin/python" ]; then
    say "Creating virtual environment $VENV"
    python3 -m venv "$VENV"
fi
PY="$VENV/bin/python"
say "Installing Python packages"
"$PY" -m pip install --upgrade pip wheel setuptools
"$PY" -m pip install --upgrade \
    requests dateparser wikipedia flask \
    numpy sounddevice soundfile onnxruntime kokoro-onnx \
    SpeechRecognition pyttsx3 plyer pigpio pvrhino pytest

if [ "${SKIP_LLM:-0}" != "1" ]; then
    if ! "$PY" -c "import llama_cpp" >/dev/null 2>&1; then
        say "Building llama-cpp-python (this can take 20-40 minutes on a Pi)"
        "$PY" -m pip install llama-cpp-python || warn "llama-cpp-python failed to build; chat will use canned replies."
    fi
fi

# ---------- 4. models ----------
download() {  # download URL DEST: resumable, skipped when already complete
    local url="$1" dest="$2"
    if [ -s "$dest" ]; then
        echo "  have $(basename "$dest")"
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    echo "  downloading $(basename "$dest")"
    curl -L --fail --retry 3 -C - -o "$dest.part" "$url"
    mv "$dest.part" "$dest"
}

say "Downloading Kokoro TTS model + voices"
if [ "${KOKORO_FULL:-0}" = "1" ]; then KOKORO_ONNX="kokoro-v1.0.onnx"; else KOKORO_ONNX="kokoro-v1.0.int8.onnx"; fi
download "$KOKORO_BASE/$KOKORO_ONNX" "$KOKORO_DIR/$KOKORO_ONNX"
download "$KOKORO_BASE/voices-v1.0.bin" "$KOKORO_DIR/voices-v1.0.bin"

if [ "${SKIP_LLM:-0}" != "1" ]; then
    say "Downloading local LLM (Llama 3.2 1B Instruct Q4_K_M, ~800 MB)"
    download "$LLM_URL" "$LLM_FILE"
fi

# ---------- 5. quick checks ----------
say "Checking the install"
"$PY" - <<EOF || true
import importlib
for mod in ("requests", "flask", "numpy", "sounddevice", "onnxruntime", "kokoro_onnx", "speech_recognition", "pigpio", "llama_cpp"):
    try:
        importlib.import_module(mod)
        print(f"  ok       {mod}")
    except Exception as e:
        print(f"  missing  {mod}: {e}")
EOF
if command -v pigs >/dev/null 2>&1 && pigs t >/dev/null 2>&1; then echo "  ok       pigpiod is running"; else warn "pigpiod is not answering"; fi

KOKORO_ARG=""
[ "$KOKORO_ONNX" != "kokoro-v1.0.int8.onnx" ] && KOKORO_ARG="KOKORO_MODEL=$KOKORO_DIR/$KOKORO_ONNX "
cat <<EOF

$(printf '\033[1;32m')SAINT is set up.$(printf '\033[0m')

  cd "$SCRIPT_DIR"
  source .venv/bin/activate

  # 1) safety tests (no hardware)
  python -m pytest test_demo_safety.py -q

  # 2) simulation: full GUI, no pigpio commands
  ${KOKORO_ARG}python assistantMainPi.py --simulate

  # 3) real hardware (pigpiod running); add --no-demo-neck to keep the head still in Demo Mode
  ${KOKORO_ARG}python assistantMainPi.py

  Voice: KOKORO_VOICE=am_michael (try bm_george, af_heart, af_bella). Engine: SAINT_TTS=auto|kokoro|pyttsx3|espeak
EOF
