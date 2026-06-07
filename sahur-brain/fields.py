"""
fields.py — GENERIC value reading. App-agnostic: find the element that semantically
matches a field ("likes", "views", "shares"...) and parse its number in ANY format:

    "1.3M"            -> 1_300_000      (M abbreviation)
    "152.1K"          -> 152_100        (K abbreviation)
    "2.1B"            -> 2_100_000_000  (B abbreviation)
    "152,099" / "152099"               -> 152_099   (raw)
    "one hundred fifty-two thousand ninety-nine"  -> 152_099  (spelled out)

Nothing here is app-coded: read_field(els, "likes") works wherever a "like"-labelled
element exists; the FIELD is a semantic keyword (groundable via Moss), the parser is
universal.
"""

from __future__ import annotations

import re

from tiktok import _words_to_int   # spelled-out -> int

_ABBR = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_count(text: str) -> int:
    """Parse a count in any human format from a label. 0 if none found."""
    t = (text or "").lower()
    # 1) abbreviated FIRST so "1.3m" isn't read as 1 or 13
    m = re.search(r"(\d[\d,]*\.?\d*)\s*([kmb])\b", t)
    if m:
        try:
            return int(float(m.group(1).replace(",", "")) * _ABBR[m.group(2)])
        except (ValueError, KeyError):
            pass
    # 2) spelled out ("one hundred fifty-two thousand ...")
    sp = _words_to_int(t)
    if sp:
        return sp
    # 3) raw, with optional commas (require >=3 digits to skip stray small numbers)
    m = re.search(r"(\d[\d,]{2,}\d|\d{3,})", t)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = re.search(r"\b(\d+)\b", t)            # last resort: any small integer
    return int(m.group(1)) if m else 0


# words that mean "this is NOT the likes element" (so we don't grab the wrong count)
_FIELD_EXCLUDE = ("comment", "share", "favorite", "favourite", "save", "mention",
                  "emoji", "add ", "bookmark", "repost")


def read_field(els, field: str = "likes") -> int:
    """Find the element whose label semantically matches `field` and return its count.
    App-agnostic: e.g. field='likes' matches 'Like video. … likes' or a '1.3M' overlay."""
    kw = field.lower().rstrip("s")                # "likes" -> "like"
    best = 0
    for e in els:
        lab = (e.label or e.value or "").strip().lower()
        if kw in lab and not any(x in lab for x in _FIELD_EXCLUDE):
            best = max(best, parse_count(lab))
    return best
