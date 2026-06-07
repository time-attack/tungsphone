"""intents.py — instant deterministic routing for the common voice commands.

The reasoning LLM (MiniMax-M2.7-highspeed) adds ~5-8s of <think> before it emits
its FIRST tool call. For the handful of high-frequency demo commands (play music,
search an app, scroll TikTok) we don't need the model at all: parse the intent
locally and return the do_sequence plan so it runs immediately.

Anything compound, ambiguous, or unrecognized returns None and falls through to the
full LLM brain (control_proof.run_once) — so we keep flexibility, just not the wait.
"""

from __future__ import annotations

import re

import procedures

# words -> canonical app name (longest/most-specific first)
_APP_WORDS = [
    ("spotify", "Spotify"),
    ("tik tok", "TikTok"), ("tiktok", "TikTok"),
    ("instagram", "Instagram"), ("insta", "Instagram"),
]

# "play some music" with no real query -> a sensible demo default
_VAGUE_MUSIC = {
    "", "music", "some music", "a song", "song", "songs", "something",
    "good music", "demo music", "background music", "tunes", "some tunes",
    "good demo music", "chill", "some chill music", "anything",
}

# conversational filler we strip before parsing
_FILLERS = [
    "can you", "could you", "would you", "please", "for me", "i'm bored",
    "im bored", "hey sahur", "hey", "okay", "ok ", "go and", "go ahead and",
    "i want to", "i want you to", "i wanna", "let's", "lets", "just go",
]

# markers of a COMPOUND / complex request -> never fast-route, send to the LLM
_COMPLEX = re.compile(
    r"\b(most liked|most popular|top \d+|first \d+|\d+ most|\d+ best|"
    r"ready|and then|after that|sort|filter|reply|comment|like (it|them|the)|"
    r"download|save (it|them)|send (it|them)|compare)\b"
)


def _strip_fillers(s: str) -> str:
    for f in _FILLERS:
        s = s.replace(f, " ")
    return re.sub(r"\s+", " ", s).strip(" ,.!?")


def _app_in(text: str):
    for w, name in _APP_WORDS:
        if w in text:
            return name
    return None


def _clean_query(q: str, drop: list[str]) -> str:
    for w in drop:
        q = re.sub(rf"\b{re.escape(w)}\b", " ", q)
    return re.sub(r"\s+", " ", q).strip(" ,.!?")


def route(text: str):
    """Return (label, steps, app, reply) for a recognized simple command, else None."""
    raw = (text or "").lower().strip()
    if not raw:
        return None
    # saved how-to macros first (e.g. "copy this tiktok's link" -> share, copy link)
    proc = procedures.match(raw)
    if proc:
        return procedures.as_route(proc)
    if _COMPLEX.search(raw):
        return None                      # complex -> let the LLM handle it
    t = _strip_fillers(raw)
    app = _app_in(t)

    # ---- play music on Spotify -------------------------------------------------
    m = re.search(r"\bplay\b(.*)", t)
    if m and app in (None, "Spotify") and "tiktok" not in t and "instagram" not in t:
        q = _clean_query(m.group(1), ["on spotify", "spotify", "some", "the", "a", "me", "my", "open"])
        if q in _VAGUE_MUSIC or len(q) < 2:
            q = "lofi beats"
        return ("music", ["search", f"type: {q}", "enter", "first result", "play"],
                "Spotify", f"playing {q}")

    # ---- TikTok ---------------------------------------------------------------
    if app == "TikTok":
        q = _clean_query(t, ["find", "search", "look for", "show me", "get me", "play", "put on",
                             "watch", "on tiktok", "tik tok", "tiktoks", "tiktok", "videos", "video",
                             "some", "me", "open", "of"])
        wants_play = re.search(r"\b(play|put on|watch|start)\b", t)
        wants_find = re.search(r"\b(find|search|look for|show me|get me|browse)\b", t)
        if (wants_play or wants_find) and q and len(q) >= 2:
            steps = ["search", f"type: {q}", "enter"]
            if wants_play and not wants_find:
                steps.append("first result")     # "play X" -> open the top video (autoplays)
            # "find/search X" -> STOP on the results grid so the like/view counts are visible
            return ("tt-search", steps, "TikTok", f"here's {q}")
        # next video on TikTok is swipe UP, not down
        return ("tt-scroll", ["swipe up", "swipe up", "swipe up"],
                "TikTok", "scrolling tiktok")

    # ---- Instagram ------------------------------------------------------------
    if app == "Instagram":
        if re.search(r"\b(dm|dms|message|messages|inbox)\b", t):
            return ("ig-dm", ["direct messages", "first conversation"],
                    "Instagram", "opening your DMs")
        m = re.search(r"\b(?:search|find|look for|show me)\b(.*)", t)
        if m:
            q = _clean_query(m.group(1), ["on instagram", "instagram", "insta", "for", "some"])
            if q and len(q) >= 2:
                return ("ig-search", ["search", f"type: {q}", "enter", "first result"],
                        "Instagram", f"searching {q}")

    return None
