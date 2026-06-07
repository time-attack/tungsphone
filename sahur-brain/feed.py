"""
feed.py — GENERIC, app-agnostic "collect links from a feed" engine.

NOT hardcoded to any app. It grounds every control through Moss (tap_semantic) and
COMPOSES indexed sub-capabilities from the procedures store (e.g. the "copy link"
macro). The same engine drives TikTok, Reels, Shorts, etc. — you pass the app, the
search query, the like threshold and how many to collect. The per-app knowledge
("how do I copy a link here", "what does the like field look like") lives in the
indexed procedures / Moss grounding, not in this code.

  feed.collect_links(acts, mcp, app="TikTok", query="ai fruit", min_likes=100000, count=5)

Design (player-scroll, verified, never returns to a grid):
  open app -> tap 'search' (Moss) -> type -> enter -> open first result ->
  loop: read the like field; if >= threshold, run the indexed 'copy link' steps and
        verify via the CLIPBOARD; swipe up to the next item. Resilient to hiccups.
"""

from __future__ import annotations

import re
import time

import deeplinks
import procedures
import fields                       # generic, app-agnostic value reader (K/M/B/raw/spelled)
from actions import (_parse_likes, _is_sponsored_label, _screen_is_sponsored,
                     _is_dead_label, _is_dead_cell, _is_live_cell, _screen_is_dead)
from tiktok import on_video, on_results   # "in a player?" / "on the results grid?" checks

_URL = re.compile(r"https?://\S+")


def clipboard_url(m):
    try:
        cb = m.call("get_clipboard")
        s = cb.get("text") if isinstance(cb, dict) else str(cb)
    except Exception:
        return None
    mm = _URL.search(s or "")
    return mm.group(0) if mm else None


def _uid(els) -> str:
    labs = [(e.label or e.value or "").strip() for e in els]
    user = next((l for l in labs if l.startswith("@")), "")
    desc = next((l for l in labs if "#" in l), "")
    return (user + "|" + desc)[:60]


def _copy_link_steps() -> list:
    """The 'copy link' sub-capability, retrieved from the INDEXED procedures store
    (falls back to the generic share->copy if not present)."""
    p = procedures.match("copy link")
    return list(p["steps"]) if p else ["share", "copy link"]


def collect_links(a, m, app, query, min_likes=100000, count=5, max_videos=60, log=print):
    app_obj = deeplinks.find_app(app)
    name = app_obj.name if app_obj else app
    log(f"▶ {name}: collect {count} '{query}' links ≥ {min_likes:,} likes")

    # COLD START: kill the app first so we never inherit a warm/restored state (a
    # leftover player or an old search). That stale state is what made us scrape the
    # SAME videos every run without ever actually searching. (See memory: never trust
    # a warm/restored screen — it shows a false green.)
    if app_obj and getattr(app_obj, "bundle_id", None):
        try:
            m.kill_app(app_obj.bundle_id); time.sleep(1.0)
        except Exception:
            pass
    a.open_app(name, "open"); a._wait_loaded()

    # grounded search (retry until a search field shows)
    for _ in range(3):
        a.tap_semantic("search"); time.sleep(0.6)
        if any("search" in (e.label or e.value or "").lower() for e in a._read_elements()):
            break
    a._focus_search_field(); a.type_text(query); time.sleep(0.6)
    try:
        m.press_key("enter")
    except Exception:
        pass
    time.sleep(1.5)

    # VERIFY we actually reached a fresh search-RESULTS grid before opening anything.
    # Without this we'd happily open whatever cell was already on screen (stale state)
    # and report success — the exact false-green we just fixed.
    t0 = time.time()
    while not on_results(a._read_elements()) and time.time() - t0 < 5:
        time.sleep(0.5)
    if not on_results(a._read_elements()):
        return {"links": [], "note": f"search for '{query}' never reached a results grid — aborted"}

    # open the first result (a cell exposing a like count) -> enter the player
    first, t0 = None, time.time()
    while first is None and time.time() - t0 < 5:
        for e in a._read_elements():
            # POSITIVE allowlist: only open a cell that PROVES it's a real, loaded video
            # (real engagement / @handle / #tag). A greyed/blacked-out/'null' tile carries
            # none of that, so it can never be picked — this is the hard guarantee we never
            # tap a dead tile again. Sponsored tiles are excluded too.
            if _is_sponsored_label(e.label or e.value or ""):
                continue
            if _is_live_cell(e):
                first = e; break
        if first is None:
            time.sleep(0.5)
    if first is None:
        return {"links": [], "note": "no results loaded"}
    m.tap(*first.center); time.sleep(1.3); a._wait_loaded()
    if not on_video(a._read_elements()):
        return {"links": [], "note": "player didn't open"}
    log("  ✓ in player")

    copy_steps = _copy_link_steps()
    links, seen, n, fails = [], set(), 0, 0
    while len(links) < count and n < max_videos and fails < 6:
        try:
            els = a._read_elements()
            if _screen_is_sponsored(els):    # this player item is a paid ad — skip it
                log("  · skipping sponsored video")
                a.swipe("up"); time.sleep(1.0); n += 1
                continue
            if _screen_is_dead(els):         # failed/'null'/blacked-out video — skip it
                log("  · skipping null/blacked-out video")
                a.swipe("up"); time.sleep(1.0); n += 1
                continue
            likes = fields.read_field(els, "likes")   # generic reader (K/M/B/raw/spelled)
            uid = _uid(els)
            if likes >= min_likes and uid not in seen:
                seen.add(uid)
                before = clipboard_url(m)
                for s in copy_steps:                 # composed indexed sub-capability
                    a.tap_semantic(s); time.sleep(0.8)
                url = clipboard_url(m)
                if url and url != before:
                    links.append({"rank": len(links) + 1, "likes": likes, "url": url})
                    log(f"  ✓ #{len(links)}  {likes:,} likes  {url}")
                    fails = 0
                if len(links) >= count:
                    break
            a.swipe("up"); time.sleep(1.0); n += 1   # next item in the player
        except Exception as ex:
            fails += 1
            log(f"  · hiccup ({str(ex)[:45]}) — {fails}/6")
            time.sleep(1.0)
    note = "ok" if len(links) >= count else f"scanned {n}, found {len(links)}"
    return {"links": links, "note": note}
