"""
devtest.py — verify the phone "hands" with NO LLM and NO MiniMax balance needed.

Hits device control server directly: health -> screen info -> frontmost app -> open Spotify via
deep link -> read a few UI elements -> a harmless tap-less screenshot.

    python devtest.py            # full smoke test
    python devtest.py tap 200 400
    python devtest.py open spotify search "rock playlist"
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

import deeplinks
from device import DeviceClient

load_dotenv(".env")


def smoke(mcp: DeviceClient):
    print("health:", mcp.health())
    print("screen:", mcp.screen_info())
    try:
        print("frontmost:", mcp.frontmost_app())
    except Exception as e:
        print("frontmost: (n/a)", e)
    url, bundle = deeplinks.resolve("Spotify", "search", "rock playlist")
    print(f"opening Spotify deep link: {url}")
    print("open_url ->", mcp.open_url(url))
    import time; time.sleep(2.5)
    els = mcp.ui_elements()
    print(f"read {len(els)} UI elements; first 8 interactive:")
    shown = 0
    for e in els:
        if shown >= 8:
            break
        print("  ", e.describe())
        shown += 1


def main():
    mcp = DeviceClient()
    try:
        mcp.health()
    except Exception as e:
        sys.exit(f"Can't reach device control server at {mcp.base_url}: {e}\n"
                 f"Fix DEVICE_BASE_URL in .env (use the phone's WiFi IP, e.g. http://192.168.x.x:8090) "
                 f"and make sure the device control server server is started on the phone.")
    args = sys.argv[1:]
    if not args:
        smoke(mcp)
    elif args[0] == "tap":
        print(mcp.tap(int(args[1]), int(args[2])))
    elif args[0] == "open":
        app = args[1]; intent = args[2] if len(args) > 2 else "open"; arg = " ".join(args[3:])
        url, bundle = deeplinks.resolve(app, intent, arg)
        print("url:", url, "bundle:", bundle)
        if url:
            print(mcp.open_url(url))
        elif bundle:
            print(mcp.launch_app(bundle))
    elif args[0] == "screenshot":
        b64 = mcp.screenshot_b64()
        print(f"screenshot ok, {len(b64)} b64 chars")
    else:
        print("usage: devtest.py [tap X Y | open APP INTENT ARG | screenshot]")


if __name__ == "__main__":
    main()
