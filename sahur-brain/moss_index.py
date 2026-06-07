"""
moss_index.py — pre-index the phone's app surface into Moss (run once, offline).

Walks each app: home screen -> each bottom-tab -> a scroll, indexing every clickable
element into the persistent Moss index ("sahur-ui"). After this, at runtime every
"tap the X" is a fast Moss query (~3-9ms) instead of a 5s first-index. Re-run anytime
to refresh (already-seen screens are skipped).

    python moss_index.py                 # default demo apps
    python moss_index.py com.x com.y     # specific bundle ids

Needs device control server reachable (iproxy) + MOSS_PROJECT_ID/KEY in .env.
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("SAHUR_VISUAL", "0")   # crawl quietly (no sprite walking)

from dotenv import load_dotenv
load_dotenv(".env")

from actions import Actions
from device import DeviceClient

DEFAULT_APPS = {
    "com.spotify.client": "Spotify",
    "com.burbn.instagram": "Instagram",
    "com.zhiliaoapp.musically": "TikTok",
    "com.apple.Music": "Apple Music",
    "com.apple.Preferences": "Settings",
    "com.apple.Maps": "Maps",
    "com.apple.MobileSMS": "Messages",
    "com.apple.mobilesafari": "Safari",
}


def bottom_nav(els, screen_h):
    out, seen = [], set()
    for e in els:
        cx, cy = e.center
        label = (e.label or "").strip()
        if cy > screen_h * 0.86 and label and e.raw.get("clickable"):
            key = label.lower()
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


def crawl_app(a: Actions, m: DeviceClient, bundle: str, name: str, max_tabs: int = 6) -> int:
    print(f"== {name} ({bundle}) ==")
    res = a._launch_verified(bundle)
    if "opened" not in res:
        print(f"  skip: {res}")
        return 0
    time.sleep(2)
    total = 0
    els = a._read_elements()
    total += a.moss.index_blocking(els, bundle)
    H = (m.screen_info() or {}).get("height", 844)
    navs = bottom_nav(els, H)[:max_tabs]
    print(f"  home indexed ({len(els)} els); {len(navs)} tabs: {[ (e.label or '')[:14] for e in navs]}")
    for e in navs:
        cx, cy = e.center
        try:
            m.tap(cx, cy); time.sleep(1.6)
            total += a.moss.index_blocking(a._read_elements(), bundle)
            a.swipe("up", 0.6); time.sleep(1.0)
            total += a.moss.index_blocking(a._read_elements(), bundle)
            a._launch_verified(bundle); time.sleep(1.0)   # reset to app home for next tab
        except Exception as ex:
            print(f"   tab '{(e.label or '')[:14]}' err: {ex}")
    print(f"  -> {name}: ~{total} docs")
    return total


def main():
    m = DeviceClient()
    a = Actions(m)
    try:
        m.health()
    except Exception as e:
        sys.exit(f"device control server unreachable: {e} (run iproxy 8090 8090, start device control server)")
    if not a.moss.enabled:
        sys.exit("Moss disabled — set MOSS_PROJECT_ID/MOSS_PROJECT_KEY in .env")

    apps = {b: b for b in sys.argv[1:]} if len(sys.argv) > 1 else DEFAULT_APPS
    grand = 0
    m.press_home(); time.sleep(1)
    grand += a.moss.index_blocking(a._read_elements(), "com.apple.springboard")
    print(f"home screen indexed")
    for bundle, name in apps.items():
        try:
            grand += crawl_app(a, m, bundle, name)
        except Exception as ex:
            print(f"  {name} crawl err: {ex}")
    m.press_home()
    print(f"\nDONE — ~{grand} elements indexed into Moss '{a.moss.index}'. Runtime taps are now fast Moss queries.")


if __name__ == "__main__":
    main()
