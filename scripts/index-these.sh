#!/usr/bin/env bash
# index-these.sh — crawl a chosen set of apps into Moss with a LIVE status dashboard.
#
#   ./scripts/index-these.sh                       # Spotify, Conductor, Terminal, Comet, Messages, Calendar
#   ./scripts/index-these.sh --minutes 40
#   ./scripts/index-these.sh --apps "Spotify,Maps,Notes" --minutes 15
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/autoindex"
exec "$HERE/sahur-brain/.venv/bin/python" index_live.py "$@"
