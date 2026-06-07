#!/usr/bin/env bash
# run-mac.sh — Tung Tung Tung Sahur on the Mac. ONE process, ONE file, no server.
#
#   ./scripts/run-mac.sh
#
# First run: macOS asks for Accessibility (and Microphone) for your terminal —
# grant them in System Settings → Privacy & Security, then run this again.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/sahur-brain"
[ -x ./.venv/bin/python ] || { echo "venv missing — run the conductor 'setup' script first"; exit 1; }

# The only Mac-specific deps: pyobjc (for the floating buddy). Installed once.
if ! ./.venv/bin/python -c "import AppKit, Quartz, ApplicationServices" 2>/dev/null; then
  echo "▶ Installing the floating-buddy deps (pyobjc)…"
  ./.venv/bin/pip install -q pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
fi

export SAHUR_ASSETS="${SAHUR_ASSETS:-$HERE/assets}"
echo "▶ Sahur on Mac — floating orb. Press it, talk; he screenshots + clicks around."
exec ./.venv/bin/python sahur.py
