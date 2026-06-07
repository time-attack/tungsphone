#!/usr/bin/env bash
# auto-index-mac.sh — READ-ONLY Mac indexer. Follows the frontmost app and indexes
# its menu commands (+ shortcuts) and window elements into Moss. Never clicks.
#
#   ./scripts/auto-index-mac.sh                 # follow frontmost, index forever
#   ./scripts/auto-index-mac.sh --once          # just the current app
#   ./scripts/auto-index-mac.sh --apps Safari,Notes,Mail,Maps
#
# First run: grant Accessibility to your terminal (System Settings → Privacy &
# Security → Accessibility), then run again.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/sahur-brain"
source .venv/bin/activate
# the Mac AX deps (pyobjc) — install once if missing
python -c "import AppKit, ApplicationServices" 2>/dev/null || \
  pip install -q pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices
exec python auto_index_mac.py "$@"
