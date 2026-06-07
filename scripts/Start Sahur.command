#!/bin/bash
# Start Sahur — double-click this in Finder to launch the floating assistant.
# (Close the Terminal window to quit him.)
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR/sahur-brain" || exit 1
[ -x ./.venv/bin/python ] || { echo "venv missing — run the conductor 'setup' script first"; read -n1; exit 1; }
# ensure the floating-buddy deps once
./.venv/bin/python -c "import AppKit, Quartz, ApplicationServices" 2>/dev/null || \
  ./.venv/bin/pip install -q pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
export SAHUR_ASSETS="$DIR/assets"
echo "🪵 Starting Tung Tung Tung Sahur…  (close this window to quit)"
exec ./.venv/bin/python sahur.py
