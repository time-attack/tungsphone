#!/usr/bin/env bash
# index-session.sh — RECORD MODE for Moss UI grounding.
#
# Unlock the phone, then open the apps you want grounded (Reminders, Calendar,
# Notes, Maps…) and tap around. Every NEW screen gets read + indexed into Moss.
# Read-only: it never taps anything. Ctrl-C to stop.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/sahur-brain"
source .venv/bin/activate
# ensure the device control server USB tunnel is up
pgrep -f "iproxy 8090 8090" >/dev/null || ( iproxy 8090 8090 >/dev/null 2>&1 & )
sleep 1
echo "================================================================"
echo "  Sahur INDEX SESSION — unlock your phone, then open & tap through:"
echo "    • Reminders   • Calendar   • Notes   • Maps"
echo "  Each new screen is indexed into Moss. Press Ctrl-C when done."
echo "================================================================"
exec python moss_record.py
