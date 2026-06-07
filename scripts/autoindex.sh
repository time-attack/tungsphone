#!/usr/bin/env bash
# autoindex.sh — run the autonomous Mac indexer (Moss-only).
#
#   ./scripts/autoindex.sh --minutes 120                # crawl the default safe app set
#   ./scripts/autoindex.sh --all --minutes 240          # every installed app
#   ./scripts/autoindex.sh --apps "Notes,Music,Maps"    # specific apps
#   ./scripts/autoindex.sh --dry --apps Calculator      # read launch screens only (no clicking)
#   ./scripts/autoindex.sh --query "play my rock playlist"   # test a runtime lookup
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/autoindex"
exec "$HERE/sahur-brain/.venv/bin/python" autoindex.py "$@"
