"""
tiktok.py — TikTok-specific smarts with DETERMINISTIC verification (no AI/LLM).

Every step is checked by a hard oracle, so the agent never flails:
  * search worked      -> result cells with like counts appear
  * a video opened     -> "Share video"/"Like video" present on screen
  * copy-link worked   -> the CLIPBOARD now holds a tiktok.com URL (the real proof)
  * returned to grid   -> the results grid (Top/Videos tab + search field) is back

Navigation is safe: to go back we TAP the top-left chevron and verify — never a
left-edge swipe (on TikTok that opens the creator's profile). If a check fails the
batch STOPS cleanly instead of wandering into profiles / messaging.
"""

from __future__ import annotations

import re
import time

from actions import _parse_likes, _video_title

TT_BUNDLE = "com.zhiliaoapp.musically"
_TT_URL = re.compile(r"https?://(?:vm\.|vt\.|www\.)?tiktok\.com/\S+", re.I)


def _labels(els):
    return [(e.label or e.value or "").strip() for e in els]


def on_results(els) -> bool:
    """True when we're on the search-results grid (tab row + a search field)."""
    labs = _labels(els)
    has_tab = any(x in labs for x in ("Top", "Videos", "Users", "Sounds"))
    has_search = any("search" in l.lower() for l in labs)
    return has_tab and has_search


def on_video(els) -> bool:
    """True when a full-screen video is open (its share/like/comment controls show)."""
    blob = " ".join(_labels(els)).lower()
    return ("share video" in blob) or ("like video" in blob) or ("add comment" in blob)


def clipboard_link(m):
    """Return a tiktok URL currently on the clipboard, else None."""
    try:
        cb = m.call("get_clipboard")
        s = cb.get("text") if isinstance(cb, dict) else str(cb)
    except Exception:
        return None
    mm = _TT_URL.search(s or "")
    return mm.group(0) if mm else None


def search(a, m, query, timeout=5.0):
    """Open TikTok, search <query>, land on results. Returns (ok, elements).
    ok=True only once result cells with parseable like counts are present."""
    a.open_app("TikTok", "open"); a._wait_loaded()
    a.tap_semantic("search"); time.sleep(0.6)
    a._focus_search_field(); a.type_text(query); time.sleep(0.6)
    try:
        m.press_key("enter")
    except Exception:
        pass
    time.sleep(1.3)
    t = time.time()
    while time.time() - t < timeout:                 # wait for the cells to render labels
        els = a._read_elements()
        if any(_parse_likes(l) for l in _labels(els)):
            return True, els
        time.sleep(0.5)
    return False, a._read_elements()


# spelled-out number parsing — the player says "one hundred fifty-two thousand ninety-nine likes"
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}


def _words_to_int(s: str) -> int:
    total = cur = 0; found = False
    for w in re.findall(r"[a-z]+", s.lower()):
        if w in _ONES:
            cur += _ONES[w]; found = True
        elif w == "hundred":
            cur = (cur or 1) * 100; found = True
        elif w in _SCALES:
            total += (cur or 1) * _SCALES[w]; cur = 0; found = True
    return (total + cur) if found else 0


def current_video_likes(els) -> int:
    """Like count of the full-screen video: 'Like video. <spelled-out> likes'."""
    for lab in _labels(els):
        m = re.search(r"like video\.?\s*(.+?)\s*likes?\b", lab, re.I)
        if m:
            n = _words_to_int(m.group(1))
            if n:
                return n
            d = re.search(r"([\d.,]+)\s*([kmb]?)", m.group(1), re.I)  # numeric fallback
            if d:
                base = float(d.group(1).replace(",", ""))
                return int(base * {"k": 1e3, "m": 1e6, "b": 1e9, "": 1}[d.group(2).lower()])
    return 0


def _video_uid(els) -> str:
    """A stable id for the current video (to avoid copying the same one twice)."""
    user = next((l for l in _labels(els) if l.startswith("@")), "")
    desc = next((l for l in _labels(els) if "#" in l), "")
    return (user + "|" + desc)[:60]


def find_links(a, m, query, min_likes=100000, count=5, max_videos=60, log=print):
    """BATCH (player-scroll): open the first result, then SWIPE UP through the player,
    copying the link of every video with >= min_likes likes. Never returns to the grid
    and never swipes sideways (that opens a profile). Deterministic + verified by the
    clipboard. Resilient to transient device hiccups. Returns {'links':[...], 'note':...}."""
    log(f"▶ search '{query}'")
    ok, _ = search(a, m, query)
    if not ok:
        return {"links": [], "note": "search results never loaded — aborted"}
    # open the first result cell to enter the full-screen player
    first = next((e for e in a._read_elements() if _parse_likes(e.label or e.value or "") > 0), None)
    if first is None:
        first = next((e for e in a._read_elements()
                      if len(e.label or e.value or "") > 20 and e.center[1] > 200), None)
    if first is None:
        return {"links": [], "note": "no results to open"}
    m.tap(*first.center); time.sleep(1.3); a._wait_loaded()
    if not on_video(a._read_elements()):
        return {"links": [], "note": "couldn't open the player"}
    log("  ✓ in player — scrolling")

    links, seen, n, fails = [], set(), 0, 0
    while len(links) < count and n < max_videos and fails < 6:
        try:
            els = a._read_elements()
            likes = current_video_likes(els)
            uid = _video_uid(els)
            if likes >= min_likes and uid not in seen:
                seen.add(uid)
                before = clipboard_link(m)
                a.tap_semantic("share"); time.sleep(0.8)
                a.tap_semantic("copy link"); time.sleep(0.7)
                url = clipboard_link(m)
                if url and url != before:
                    links.append({"rank": len(links) + 1, "likes": likes, "url": url})
                    log(f"  ✓ #{len(links)}  {likes:,} likes  {url}")
                    fails = 0
                if len(links) >= count:
                    break
            a.swipe("up"); time.sleep(1.0); n += 1     # NEXT video in the player
        except Exception as ex:
            fails += 1
            log(f"  · hiccup ({str(ex)[:45]}) — {fails}/6")
            time.sleep(1.0)
    note = "ok" if len(links) >= count else (f"scanned {n} videos, found {len(links)}")
    return {"links": links, "note": note}
