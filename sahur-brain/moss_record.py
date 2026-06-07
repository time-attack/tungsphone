"""
moss_record.py — RECORD MODE. You drive, Sahur indexes.

Run this, then just unlock your phone and walk through the apps you care about
(open Spotify, tap Search, scroll, open Instagram, etc.). Every NEW screen you land
on is read and indexed into Moss. Afterwards, at runtime, "tap the X" is an instant
Moss query — no live indexing needed.

    python moss_record.py        # records until you Ctrl-C

Read-only: it never taps anything. Needs device control server (iproxy) + MOSS_PROJECT_ID/KEY in .env.
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("SAHUR_VISUAL", "0")

from dotenv import load_dotenv
load_dotenv(".env")

from actions import Actions
from device import DeviceClient


def main():
    m = DeviceClient()
    a = Actions(m)
    try:
        m.health()
    except Exception as e:
        sys.exit(f"device control server unreachable: {e} (run iproxy 8090 8090, start device control server)")
    if not a.moss.enabled:
        sys.exit("Moss disabled — set MOSS_PROJECT_ID/MOSS_PROJECT_KEY in .env")

    print("📼 RECORD MODE — open apps and tap around on your phone.")
    print("   Every new screen gets indexed into Moss. Ctrl-C when done.\n")
    screens = 0
    docs = 0
    last_print = ""
    while True:
        try:
            try:
                els = a._read_elements(tries=2, delay=0.4)
            except Exception:
                els = []
            if els:
                app = a._frontmost_bundle()
                n = a.moss.index_blocking(els, app)   # 0 if this screen already indexed
                if n > 0:
                    screens += 1
                    docs += n
                    print(f"  + {app}: indexed {n} elements   (screens={screens}, total docs={docs})")
                else:
                    line = f"\r  watching {app} … (screens={screens}, docs={docs})   "
                    if line != last_print:
                        sys.stdout.write(line); sys.stdout.flush(); last_print = line
            time.sleep(1.1)
        except KeyboardInterrupt:
            print(f"\n\nStopped. {screens} screens / {docs} elements indexed into Moss '{a.moss.index}'.")
            print("Now run sahur_voice.py — lookups will be fast Moss queries.")
            break
        except Exception as e:
            print(f"\n  (record err: {e})")
            time.sleep(1.0)


if __name__ == "__main__":
    main()
