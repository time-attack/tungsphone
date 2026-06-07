#!/usr/bin/env bash
# deploy-tweak.sh — build PhonePhonePhoneSahur, install the .deb onto the
# USB-connected iPhone over an iproxy SSH tunnel, and respring.
#
#   ./scripts/deploy-tweak.sh
#
# Env (all optional, sensible defaults):
#   THEOS          Theos path                (default: $HOME/theos)
#   DEVELOPER_DIR  Xcode 16.4 toolchain      (default: /Applications/Xcode-16.4.0.app/Contents/Developer)
#                  — this exact generation is required for arm64e that iOS 15.1.1 can authenticate.
#   IPHONE_HOST    ssh host                  (default: 127.0.0.1)
#   IPHONE_PORT    local forwarded ssh port  (default: 2222 -> device :22 via iproxy)
#   IPHONE_USER    ssh user                  (default: mobile, escalates via sudo)
#   IPHONE_PASS    ssh/sudo password         (default: alpine — override for a non-default setup)
set -euo pipefail

# Build THIS working copy (the script lives in <repo>/scripts), not the shared
# Conductor root — that may be checked out on another branch without these changes.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TWEAK_DIR="$ROOT/PhonePhonePhoneSahur"

export THEOS="${THEOS:-$HOME/theos}"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode-16.4.0.app/Contents/Developer}"
IPHONE_HOST="${IPHONE_HOST:-127.0.0.1}"
IPHONE_PORT="${IPHONE_PORT:-2222}"
IPHONE_USER="${IPHONE_USER:-mobile}"
export SSHPASS="${IPHONE_PASS:-alpine}"

# ---- preflight ----
command -v sshpass >/dev/null || { echo "need sshpass (brew install hudochenkov/sshpass/sshpass)"; exit 1; }
command -v iproxy   >/dev/null || { echo "need iproxy (brew install libimobiledevice)"; exit 1; }
[ -d "$THEOS" ]         || { echo "THEOS not found at $THEOS (set THEOS=...)"; exit 1; }
[ -d "$DEVELOPER_DIR" ] || { echo "Xcode 16.4 not found at $DEVELOPER_DIR (set DEVELOPER_DIR=...)"; exit 1; }

# ---- build (fat arm64 + arm64e) ----
echo "==> building tweak with $(basename "$(dirname "$(dirname "$DEVELOPER_DIR")")")…"
make -C "$TWEAK_DIR" package THEOS_PACKAGE_SCHEME=rootless

DEB="$(ls -t "$TWEAK_DIR"/packages/*.deb 2>/dev/null | head -1)"
[ -n "$DEB" ] || { echo "no .deb produced in $TWEAK_DIR/packages"; exit 1; }
echo "==> built $(basename "$DEB")"

# ---- ensure the USB ssh tunnel (iproxy local:PORT -> device:22) ----
if ! nc -z -G1 "$IPHONE_HOST" "$IPHONE_PORT" 2>/dev/null; then
  echo "==> starting iproxy $IPHONE_PORT 22…"
  iproxy "$IPHONE_PORT" 22 >/dev/null 2>&1 &
  sleep 2
fi

# Force PASSWORD auth only — otherwise ssh offers local identity keys first and
# hits the device's MaxAuthTries ("Too many authentication failures") before the password.
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=8 -o PubkeyAuthentication=no
          -o PreferredAuthentications=password -o IdentitiesOnly=yes -p "$IPHONE_PORT")

# ---- copy + install + respring ----
echo "==> copying to device…"
sshpass -e scp -O -o StrictHostKeyChecking=no -o PubkeyAuthentication=no \
  -o PreferredAuthentications=password -o IdentitiesOnly=yes -P "$IPHONE_PORT" \
  "$DEB" "$IPHONE_USER@$IPHONE_HOST:/tmp/sahur.deb"
echo "==> installing + respringing…"
sshpass -e ssh "${SSH_OPTS[@]}" "$IPHONE_USER@$IPHONE_HOST" \
  "echo '$SSHPASS' | sudo -S -p '' dpkg -i /tmp/sahur.deb && echo '$SSHPASS' | sudo -S -p '' sh -c 'sbreload 2>/dev/null || killall -9 SpringBoard'"
echo "==> done — Sahur reinstalled + resprung."
