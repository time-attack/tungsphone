#!/usr/bin/env bash
# auto-index.sh — AUTONOMOUS, SAFE crawler that maps the iPhone into Moss with
# nobody watching. Read-biased: never sends/buys/deletes/calls, never types.
#
#   ./scripts/auto-index.sh                  # default safe apps, up to 2h
#   ./scripts/auto-index.sh --minutes 180
#   ./scripts/auto-index.sh --tabs-only      # safest
#   ./scripts/auto-index.sh --dry-run        # plan + index, tap nothing
#   ./scripts/auto-index.sh --apps Spotify,Notes,Maps
#
# Unlock the phone and leave it on a charger; pass extra flags straight through.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/sahur-brain"
source .venv/bin/activate
pgrep -f "iproxy 8090 8090" >/dev/null || ( iproxy 8090 8090 >/dev/null 2>&1 & )
sleep 1
exec python auto_index.py "$@"
