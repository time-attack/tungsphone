"""
index_live.py — crawl a specific set of apps with a LIVE status dashboard.

    python index_live.py                       # the default set
    python index_live.py --apps "Spotify,Maps" --minutes 20

In a real terminal it draws an in-place dashboard (app · status · screens · last
action · totals). When piped, it falls back to a plain line log.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import crawler
from moss_index import MossIndex

DEFAULT_APPS = ["Spotify", "Conductor", "Terminal", "Comet", "Messages", "Calendar"]
_ICON = {"queued": "…", "exploring": "⟳", "done": "✓", "failed": "✗"}


def _ensure_ax():
    try:
        from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                         kAXTrustedCheckOptionPrompt)
        return bool(AXIsProcessTrustedWithOptions(
            {kAXTrustedCheckOptionPrompt: os.environ.get("SAHUR_NO_AX_PROMPT") != "1"}))
    except Exception:
        return False


class Live:
    def __init__(self, apps, moss):
        self.apps = apps
        self.moss = moss
        self.state = {a: {"status": "queued", "screens": 0, "last": ""} for a in apps}
        self.current = None
        self.t0 = time.time()
        self.tty = sys.stdout.isatty()

    def on_status(self, msg: str):
        a = self.current
        if a:
            st, m = self.state[a], msg.strip()
            if m.startswith("• ["):
                st["screens"] += 1
                st["last"] = m
            elif m.startswith("✗") or "couldn't launch" in m:
                st["status"] = "failed"; st["last"] = m
            else:
                st["last"] = m
        if self.tty:
            self.render()
        elif a:
            print(f"[{a}] {msg.strip()}", flush=True)

    def render(self):
        if not self.tty:
            return
        el = int(time.time() - self.t0)
        out = ["🪵  Sahur Auto-Indexer — live",
               f"Moss index: {self.moss.name}     elapsed {el // 60}:{el % 60:02d}", "",
               f"  {'APP':14} {'STATUS':12} {'SCREENS':>7}   LAST ACTION"]
        for a in self.apps:
            s = self.state[a]
            out.append(f"  {a[:14]:14} {_ICON.get(s['status'],' ')} {s['status']:10} "
                       f"{s['screens']:>7}   {s['last'][:48]}")
        done = sum(1 for a in self.apps if self.state[a]["status"] in ("done", "failed"))
        scr = sum(self.state[a]["screens"] for a in self.apps)
        out += ["", f"  totals: {done}/{len(self.apps)} apps · {scr} screens · "
                f"{self.moss.stats['docs']} Moss docs · {self.moss.stats['errors']} errors"]
        sys.stdout.write("\033[2J\033[H" + "\n".join(out) + "\n")
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default=",".join(DEFAULT_APPS))
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--max-screens", type=int, default=40)
    args = ap.parse_args()

    apps = [a.strip() for a in args.apps.split(",") if a.strip()]
    moss = MossIndex()
    if not moss.available():
        print("✗ No MOSS creds in sahur-brain/.env"); return
    if not _ensure_ax():
        print("⚠️  Grant Accessibility to your terminal, then rerun.")

    live = Live(apps, moss)
    live.render()
    per_app = (args.minutes * 60) / max(1, len(apps))
    for a in apps:
        live.current = a
        live.state[a]["status"] = "exploring"
        live.render()
        try:
            r = crawler.explore_app(a, moss, budget_s=per_app,
                                    max_screens=args.max_screens, on_status=live.on_status)
            live.state[a]["status"] = "done" if r["screens"] > 0 else "failed"
        except Exception as e:
            live.state[a]["status"] = "failed"
            live.state[a]["last"] = str(e)[:48]
        live.render()

    el = int(time.time() - live.t0)
    print(f"\n✅ done in {el // 60}m{el % 60:02d}s — "
          f"{sum(live.state[a]['screens'] for a in apps)} screens · "
          f"{moss.stats['docs']} docs in Moss '{moss.name}' · {moss.stats['errors']} errors")
    if moss.stats["last_error"]:
        print(f"   last Moss error: {moss.stats['last_error']}")


if __name__ == "__main__":
    main()
