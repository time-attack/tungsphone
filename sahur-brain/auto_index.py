"""
auto_index.py — AUTONOMOUS, SAFE UI crawler for the iPhone.

Nobody has to drive. It opens each app and walks its NAVIGATION (bottom tabs +
one layer of safe content rows), reading and indexing EVERY new screen into Moss.
After a few hours the phone is fully grounded, so at runtime "tap the X" is an
instant Moss lookup with ~no latency.

SAFETY (this taps your phone with nobody watching, so it is deliberately timid):
  * READ-BIASED. A hard DENYLIST blocks anything destructive / sending / buying /
    calling / posting / sign-in / system-toggle. It is NEVER tapped.
  * It NEVER types text and never taps a text field.
  * Risky apps (Messages, Mail, Phone, App Store, Wallet, Settings, banking) are
    excluded from the default set — add them explicitly only if you want to.
  * Bounded: per-app screen cap, taps-per-screen cap, and a global time budget.
  * Returns to the Home screen between apps; if a tap escapes into an unexpected
    app, it bails back to Home instead of wandering.

    python auto_index.py                       # crawl the default safe app set
    python auto_index.py --minutes 180         # run up to 3 hours
    python auto_index.py --apps Spotify,Notes,Maps
    python auto_index.py --tabs-only           # safest: only bottom tabs
    python auto_index.py --dry-run             # plan + index, but DON'T tap anything

Read-only flag aside, this is real automation — start it on a phone you're OK with
it poking, with the screen unlocked.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

os.environ.setdefault("SAHUR_VISUAL", "0")   # no sprite-walk lead delay while crawling

from dotenv import load_dotenv
load_dotenv(".env")

from actions import Actions, _is_interactive
from device import DeviceClient

# ---- safety -----------------------------------------------------------------

# If an element's label matches ANY of these, it is NEVER tapped.
DENY = re.compile(
    r"\b("
    r"delete|remove|trash|erase|clear all|delete all|remove all|"
    r"send|post|publish|share|retweet|repost|upload|tweet|go live|"
    r"buy|purchase|subscribe|unsubscribe|renew|pay|checkout|check out|order|add to cart|"
    r"install|update|redownload|"
    r"call|facetime|video call|dial|"
    r"sign ?out|log ?out|sign ?in|log ?in|sign up|delete account|"
    r"reset|restore|factory|erase all|"
    r"block|report|mute|unfollow|unfriend|unmatch|leave|"
    r"turn off|turn on|enable|disable|"
    r"confirm|submit|agree|allow|accept|deny|continue with|"
    r"compose|reply|forward|"
    r"create|new (playlist|note|event|reminder|contact|message|chat|folder|list|tag|album)|"
    r"add (friend|list|account|event|reminder|contact|card|to)|"
    r"follow back|connect|request"
    r")\b",
    re.I,
)

# keyboard keys / media controls we should never treat as nav and never tap blindly
_KBD = {"space", "return", "shift", "delete", "backspace", "emoji", "dictate", "dictation",
        "123", "abc", ".?123", "next keyboard", "microphone"}
_MEDIA = {"play", "pause", "next track", "previous track", "skip", "skip back",
          "skip forward", "track position", "shuffle", "repeat"}


def _is_noise(lab: str) -> bool:
    l = lab.strip().lower()
    return l in _KBD or l in _MEDIA or (len(l) == 1 and l.isalnum())


def _keyboard_up(els) -> bool:
    return sum(1 for e in els if _is_noise(_label(e))) >= 5

# Apps that are safe to wander unattended (mostly read/browse surfaces).
DEFAULT_APPS = [
    "Spotify", "TikTok", "Instagram", "Notes", "Calendar", "Reminders",
    "Maps", "Photos", "Apple Music", "Podcasts", "Health", "Clock", "Safari",
]

# Never auto-open these (state changes, money, comms) unless the user lists them.
RISKY_BUNDLES = {
    "com.apple.MobileSMS", "com.apple.mobilemail", "com.apple.mobilephone",
    "com.apple.AppStore", "com.apple.Passbook", "com.apple.Preferences",
    "com.apple.facetime",
}


def _label(e) -> str:
    return (e.label or e.value or e.identifier or "").strip()


def _safe(e) -> bool:
    lab = _label(e)
    return len(lab) > 1 and not DENY.search(lab)


# ---- crawler ----------------------------------------------------------------

class Crawler:
    def __init__(self, a: Actions, per_app=20, taps_per_screen=6, settle=0.7,
                 tabs_only=False, dry=False):
        self.a = a
        self.m = a.mcp
        self.per_app = per_app
        self.tps = taps_per_screen
        self.settle = settle
        self.tabs_only = tabs_only
        self.dry = dry
        self.visited: set[str] = set()
        self.screens = 0
        self.docs = 0
        w, h = a._screen_size() or (0, 0)
        self.w, self.h = (w or 390), (h or 844)

    def _read(self):
        try:
            return self.a._read_elements(tries=3, delay=0.4)
        except Exception:
            return []

    def _sig(self, els, bundle):
        cands = [e for e in els if _label(e)]
        return self.a.moss._sig(cands, bundle) if cands else ""

    def _index(self, bundle):
        """Read + index the current screen. Returns (elements, signature)."""
        els = self._read()
        if not els:
            return [], ""
        sig = self._sig(els, bundle)
        if sig and sig not in self.visited:
            self.visited.add(sig)
            n = self.a.moss.index_blocking(els, bundle)
            if n > 0:
                self.screens += 1
                self.docs += n
                print(f"    + {bundle.split('.')[-1]}: +{n} elems  ({self.screens} screens / {self.docs} docs)")
        return els, sig

    def _dismiss_keyboard(self, els):
        """If a text keyboard is up (e.g. an app opened into an edit field), close it
        so we crawl the real UI and never type. Taps Done/Cancel/Back, else swipes down."""
        if not els or not _keyboard_up(els):
            return els
        if self.dry:
            print("      (dry) keyboard up — would dismiss")
            return els
        for e in els:
            if _label(e).lower() in ("done", "cancel", "close", "back"):
                self.m.tap(*e.center); time.sleep(self.settle)
                return self._read()
        try:
            self.m.swipe(self.w // 2, int(self.h * 0.45), self.w // 2, int(self.h * 0.95))
        except Exception:
            pass
        time.sleep(self.settle)
        return self._read()

    def _go_back(self):
        """Return to the previous screen: tap a Back/Done/Close/Cancel control if
        present, else swipe in from the left edge (the iOS back gesture)."""
        for e in self._read():
            lab = _label(e).lower()
            if lab in ("back", "done", "close", "cancel", "< back") or lab.startswith("back "):
                if not self.dry:
                    self.m.tap(*e.center)
                    time.sleep(self.settle)
                return
        if not self.dry:
            try:
                self.m.swipe(2, self.h // 2, int(self.w * 0.75), self.h // 2)
            except Exception:
                pass
            time.sleep(self.settle)

    def _tap(self, e) -> bool:
        if self.dry:
            print(f"      (dry) would tap {_label(e)!r} @ {e.center}")
            return False
        self.m.tap(*e.center)
        time.sleep(self.settle)
        return True

    def crawl_app(self, app: str, deadline: float):
        print(f"\n== {app} ==")
        try:
            self.a.open_app(app, "open")
        except Exception as ex:
            print(f"   open failed: {ex}")
            return
        time.sleep(1.2)
        bundle = self.a._frontmost_bundle()
        if bundle in RISKY_BUNDLES:
            print(f"   skipping risky app {bundle}")
            return
        start_screens = self.screens
        els = self._dismiss_keyboard(self._read())
        els, _ = self._index(bundle) if els else ([], "")
        els = els or self._read()
        if not els:
            return

        # real tab-bar items: interactive, in the VERY bottom (>90%), SHORT label
        # (<=16 chars, <=2 words), not a keyboard key / media control / now-playing row.
        tabs, seen = [], set()
        for e in els:
            lab = _label(e)
            if (_is_interactive(e) and e.center[1] > self.h * 0.90 and _safe(e)
                    and 1 < len(lab) <= 16 and len(lab.split()) <= 2 and not _is_noise(lab)):
                key = lab.lower()
                if key not in seen:
                    seen.add(key)
                    tabs.append(e)

        for e in tabs:
            if time.time() > deadline or (self.screens - start_screens) >= self.per_app:
                break
            print(f"   tab: {_label(e)}")
            if not self._tap(e):
                continue
            if self.a._frontmost_bundle() != bundle:   # escaped the app -> bail home
                self.a.press_home(); time.sleep(0.6)
                self.a.open_app(app, "open"); time.sleep(1.0)
                continue
            tab_els, _ = self._index(bundle)
            if not self.tabs_only:
                self._explore_rows(bundle, tab_els, deadline, start_screens)

        self.a.press_home()
        time.sleep(0.6)

    def _explore_rows(self, bundle, els, deadline, start_screens):
        """Tap a few safe CONTENT rows one level deep, index, then go back."""
        rows = [e for e in els
                if _is_interactive(e) and self.h * 0.10 < e.center[1] < self.h * 0.82 and _safe(e)
                and not _is_noise(_label(e)) and 1 < len(_label(e)) <= 42]
        for e in rows[: self.tps]:
            if time.time() > deadline or (self.screens - start_screens) >= self.per_app:
                return
            before = self._sig(self._read(), bundle)
            print(f"     row: {_label(e)}")
            if not self._tap(e):
                continue
            if self.a._frontmost_bundle() != bundle:   # opened another app -> recover
                self.a.press_home(); time.sleep(0.6)
                self.a.open_app(app=bundle_to_name(bundle), intent="open"); time.sleep(1.0)
                return
            after_els = self._read()
            after = self._sig(after_els, bundle)
            if after and after != before:
                self._index(bundle)
                self._go_back()


def bundle_to_name(bundle: str) -> str:
    # best-effort reverse lookup so we can reopen after an accidental escape
    import deeplinks
    for app in getattr(deeplinks, "IOS_APPS", []):
        if app.bundle_id == bundle:
            return app.name
    return bundle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", help="comma-separated app names (default: safe set)")
    ap.add_argument("--minutes", type=float, default=120.0, help="global time budget")
    ap.add_argument("--per-app", type=int, default=20, help="max new screens per app")
    ap.add_argument("--taps-per-screen", type=int, default=6)
    ap.add_argument("--tabs-only", action="store_true", help="safest: only tap bottom tabs")
    ap.add_argument("--dry-run", action="store_true", help="plan + index but never tap")
    args = ap.parse_args()

    m = DeviceClient()
    try:
        m.health()
    except Exception as e:
        sys.exit(f"device control server unreachable: {e} (run iproxy 8090 8090)")
    a = Actions(m)
    if not a.moss.enabled:
        sys.exit("Moss disabled — set MOSS_PROJECT_ID/MOSS_PROJECT_KEY in .env")
    a.moss.warm()

    apps = [s.strip() for s in args.apps.split(",")] if args.apps else DEFAULT_APPS
    deadline = time.time() + args.minutes * 60
    c = Crawler(a, per_app=args.per_app, taps_per_screen=args.taps_per_screen,
                tabs_only=args.tabs_only, dry=args.dry_run)

    print(f"🤖 AUTO-INDEX — {len(apps)} apps, up to {args.minutes:.0f} min"
          f"{' [DRY RUN]' if args.dry_run else ''}{' [tabs-only]' if args.tabs_only else ''}")
    print(f"   apps: {', '.join(apps)}\n   denylist active; read-biased; unlock the phone.\n")
    try:
        for app in apps:
            if time.time() > deadline:
                print("\n⏰ time budget reached."); break
            c.crawl_app(app, deadline)
    except KeyboardInterrupt:
        print("\n\nstopped by user.")
    finally:
        try:
            a.press_home()
        except Exception:
            pass
        print(f"\n✅ done — {c.screens} new screens / {c.docs} elements indexed into Moss '{a.moss.index}'.")


if __name__ == "__main__":
    main()
