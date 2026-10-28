#!/usr/bin/env bash
# SAINT diagnostics on Linux — read-only. Writes build/diagnostics/<time>/ with:
#   preflight.md / preflight.json   hardware, storage, boot mode (tools/preflight.py)
#   capabilities.json               which desktop features SAINT can use (core/platform_caps.py)
#   selftest.json                   SAINT's own self-test (libraries, models, UI, agent)
#   service.txt                     the saint user service's state and recent journal
# Share the folder when reporting a problem; it contains no secrets or memory contents.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
DIR="build/diagnostics/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$DIR"
"$PY" tools/preflight.py --json "$DIR/preflight.json" > "$DIR/preflight.md" || echo "preflight failed" >&2
"$PY" -m core.platform_caps > "$DIR/capabilities.json" || echo "capability probe failed" >&2
QT_QPA_PLATFORM=offscreen SAINT_DATA_DIR="$(mktemp -d)" "$PY" app.py --selftest "$DIR/selftest.json" \
  > "$DIR/selftest.log" 2>&1 || echo "selftest reported failures (see selftest.json)" >&2
{ systemctl --user status saint.service --no-pager 2>&1; journalctl --user -u saint.service -n 200 --no-pager 2>&1; } \
  > "$DIR/service.txt"
echo "Diagnostics in $DIR"
