"""
sahurctl.py — show/hide the SpringBoard sprite by flipping its control file.

    python sahurctl.py show
    python sahurctl.py hide

The tweak does NOTHING at boot; it only builds the overlay when this writes "1".
If the overlay ever misbehaves, set it to "0" (or just respring) — boot stays clean,
so it can never bootloop.
"""

import sys, time
from dotenv import load_dotenv
from device import DeviceClient

load_dotenv(".env")
SHOW_FILE = "/var/mobile/Library/Caches/sahur_show.txt"


def set_show(m, on: bool):
    # Nonce protocol: the tweak only acts when the command CHANGES, so a value
    # left in the file across a reboot is ignored (no auto-show => no bootloop).
    val = "1" if on else "0"
    nonce = str(int(time.time() * 1000))
    return m.run_command(f'printf "%s %s" {val} {nonce} > {SHOW_FILE} && echo "set {val} {nonce}"', timeout=10)


def main():
    arg = (sys.argv[1] if len(sys.argv) > 1 else "show").lower()
    on = arg in ("show", "on", "1")
    m = DeviceClient()
    print("health:", m.health())
    print(set_show(m, on))


if __name__ == "__main__":
    main()
