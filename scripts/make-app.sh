#!/usr/bin/env bash
# make-app.sh — build a double-clickable Sahur.app launcher (no terminal window).
# Drag it to your Dock or /Applications, or just Spotlight "Sahur".
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$HERE/Sahur.app}"
[ -x "$HERE/sahur-brain/.venv/bin/python" ] || { echo "venv missing — run the conductor 'setup' script first"; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# the launcher: cd into the repo and run the app
cat > "$APP/Contents/MacOS/Sahur" <<EOF
#!/bin/bash
cd "$HERE/sahur-brain"
export SAHUR_ASSETS="$HERE/assets"
exec ./.venv/bin/python sahur.py
EOF
chmod +x "$APP/Contents/MacOS/Sahur"

# icon from the Sahur sprite (best-effort)
ICON=""
if command -v iconutil >/dev/null 2>&1 && [ -f "$HERE/assets/sahur.png" ]; then
  SET="$(mktemp -d)/Sahur.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s        "$HERE/assets/sahur.png" --out "$SET/icon_${s}x${s}.png"    >/dev/null 2>&1 || true
    sips -z $((s*2)) $((s*2)) "$HERE/assets/sahur.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null 2>&1 || true
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/Sahur.icns" >/dev/null 2>&1 && ICON="Sahur" || true
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Sahur</string>
  <key>CFBundleDisplayName</key><string>Tung Tung Tung Sahur</string>
  <key>CFBundleIdentifier</key><string>com.matianlaw.sahur</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>Sahur</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  ${ICON:+<key>CFBundleIconFile</key><string>$ICON</string>}
  <key>NSMicrophoneUsageDescription</key><string>Sahur listens for your voice commands.</string>
</dict></plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || true
touch "$APP"
echo "✅ Built $APP"
echo "   • Double-click it, drag it to your Dock, or Spotlight 'Sahur'."
echo "   • First launch: grant Accessibility + Microphone if asked, then relaunch."
