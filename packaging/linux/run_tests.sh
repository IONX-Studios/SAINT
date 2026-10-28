#!/usr/bin/env bash
# Run SAINT's test suite on Linux and keep the output (build/test-results/).
#
#   packaging/linux/run_tests.sh                 # everything
#   packaging/linux/run_tests.sh tests/test_linux_platform.py -q
#
# The suite mocks the microphone, models, Spotify and the desktop (conftest.py);
# QT_QPA_PLATFORM=offscreen lets the UI smoke tests run without a display.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
[ -x "$PY" ] || { echo "No .venv — run packaging/linux/setup_fedora.sh first." >&2; exit 1; }
mkdir -p build/test-results
OUT="build/test-results/pytest-$(date +%Y%m%d-%H%M%S).txt"
{
  echo "# $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown OS}") / ${XDG_SESSION_TYPE:-no session}"
  "$PY" --version
  git rev-parse --short HEAD 2>/dev/null
} > "$OUT"
QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" "$PY" -m pytest -rfE --tb=short -p no:randomly "${@:--q}" 2>&1 | tee -a "$OUT"
code=${PIPESTATUS[0]}
echo "Saved: $OUT (exit $code)"
exit "$code"
