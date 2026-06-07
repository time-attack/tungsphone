"""
autoindex.py — run the autonomous indexer (Moss-only).

Leave it running for a few hours with you away; it explores your Mac app-by-app and
streams everything it finds into Moss. Later the floating Sahur queries that index for
an instant, no-exploration answer.

    python autoindex.py --minutes 120                 # crawl the default safe app set
    python autoindex.py --apps "Notes,Music,Maps"     # specific apps
    python autoindex.py --all --minutes 240           # every installed app
    python autoindex.py --dry --apps Calculator       # read launch screens only (no clicking)
    python autoindex.py --query "play my rock playlist"   # test a runtime lookup

Moss-only: nothing is written to disk. If your Moss op-quota is exhausted the writes
will say so explicitly (no silent local fallback).
"""

from __future__ import annotations

import argparse
import glob
import os
import time

import crawler
from moss_index import MossIndex

_SAFE_DEFAULT = ["Calculator", "Notes", "Reminders", "Calendar", "Maps", "Music", "Photos",
                 "Weather", "Clock", "Stocks", "Contacts", "Books", "Freeform", "Preview",
                 "TextEdit", "Home", "Voice Memos", "System Settings"]
_APP_DIRS = ["/Applications", "/Applications/Utilities", "/System/Applications",
             "/System/Applications/Utilities", os.path.expanduser("~/Applications")]


def _installed():
    names = set()
    for d in _APP_DIRS:
        for p in glob.glob(os.path.join(d, "*.app")):
            names.add(os.path.splitext(os.path.basename(p))[0])
    return sorted(names)


def _ensure_ax():
    try:
        from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                         kAXTrustedCheckOptionPrompt)
        return bool(AXIsProcessTrustedWithOptions(
            {kAXTrustedCheckOptionPrompt: os.environ.get("SAHUR_NO_AX_PROMPT") != "1"}))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--apps", type=str, default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--per-app", type=float, default=0, help="seconds per app (else split evenly)")
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--max-screens", type=int, default=40)
    ap.add_argument("--dry", action="store_true", help="index launch screen only; no clicking")
    ap.add_argument("--query", type=str, default="", help="test a runtime lookup and exit")
    args = ap.parse_args()

    moss = MossIndex()
    if not moss.available():
        print("✗ No MOSS_PROJECT_ID / MOSS_PROJECT_KEY in sahur-brain/.env — Moss is the only store.")
        return
    print(f"Moss index: '{moss.name}'  (model {moss.model})")

    # ---- query (runtime lookup) ----
    if args.query:
        print(f"\nquery: {args.query!r}")
        for r in moss.query(args.query, top_k=5):
            if "error" in r:
                print(f"  ✗ Moss error: {r['error']}"); break
            md = r.get("metadata", {})
            print(f"  • [{r.get('score')}] {r['text'][:80]}  app={md.get('app')} path={md.get('path')}")
        return

    # ---- crawl ----
    if not _ensure_ax():
        print("⚠️  Grant Accessibility to your terminal (System Settings → Privacy & Security → "
              "Accessibility), then rerun. The crawler needs it to read + click the UI.")
    apps = ([a.strip() for a in args.apps.split(",") if a.strip()] if args.apps
            else (_installed() if args.all else _SAFE_DEFAULT))
    apps = [a for a in apps if any(os.path.exists(os.path.join(d, a + ".app")) for d in _APP_DIRS)]
    if not apps:
        print("No matching installed apps to crawl."); return

    budget = args.minutes * 60
    per_app = args.per_app or max(20, budget / max(1, len(apps)))
    print(f"Crawling {len(apps)} app(s), ~{per_app:.0f}s each, budget {args.minutes:.0f} min"
          + (" [DRY: launch screens only]" if args.dry else "") + "\n")

    t0 = time.time()
    summary = []
    for app in apps:
        if time.time() - t0 > budget:
            print("⏱  budget reached — stopping."); break
        try:
            if args.dry:
                pid = crawler.launch(app)
                if not pid:
                    print(f"  {app}: couldn't launch"); continue
                els = crawler.read_app(pid)
                names = [e["name"] for e in els if e["name"]]
                _id, _names, doc = crawler._make_doc(app, [], els)
                ok, msg = moss.add([doc])
                print(f"  {app}: {len(names)} controls → Moss {'OK' if ok else 'FAILED — ' + msg[:80]}")
                summary.append({"app": app, "screens": 1, "pushed": int(ok)})
            else:
                summary.append(crawler.explore_app(app, moss, budget_s=per_app,
                                                   max_screens=args.max_screens))
        except Exception as e:
            print(f"  {app}: error {e}")

    print("\n──────── done ────────")
    print(f"apps: {len(summary)} | screens indexed: {sum(s.get('screens',0) for s in summary)}")
    print(f"Moss: {moss.stats['docs']} docs in {moss.stats['batches']} ops, {moss.stats['errors']} errors")
    if moss.stats["last_error"]:
        print(f"last Moss error: {moss.stats['last_error']}")


if __name__ == "__main__":
    main()
