"""
conversation.py — durable, rolling DIALOGUE memory so Sahur is ONE continuous
conversation, not a brand-new chat per request.

artifacts.py remembers RESULTS (the links it collected). This remembers the TALK:
what you asked and what Sahur answered, turn by turn. That is what lets a follow-up
resolve against the conversation instead of falling on the floor —

    you:   what did my girlfriend text me?
    sahur: she said "running late, order without me"
    you:   reply ok love you          <- "reply" to WHO? the thread we just read
    you:   no, the OTHER video         <- "the other" relative to what we just sent
    you:   do that again               <- "that" = the last thing we actually did
    you:   send it to her instead      <- "her" / "it" = people+things already named

Without this, every utterance reached the planner with NO idea a prior turn ever
happened, so anything referring back ("again", "her", "instead", "the other one")
had nothing to anchor to — the "each request is a new chat but it really isn't"
problem. Each completed turn is appended here and the recent transcript is fed into
the planner (and the read-answer step) on the NEXT turn.

Window: a short session (SESSION_TTL). After a long gap we treat it as a new
conversation rather than dragging in stale references. Only the last MAX_TURNS are
surfaced so the planner prompt stays cheap; the file is capped at _STORE_CAP.

NOTE: this is the DIALOGUE; artifacts.py is the RESULTS; Moss is on-screen UI
elements for tap grounding. Three different memories that all got called "memory".
"""

from __future__ import annotations

import json
import os
import time

_DIR = os.path.join(os.path.dirname(__file__), "records")
_FILE = os.path.join(_DIR, "conversation.json")

# A spoken session: turns within this window belong to the same conversation. After
# a longer gap, "those", "her", "again" almost certainly mean something new, so we
# stop surfacing the old thread rather than acting on a stale reference.
SESSION_TTL = 30 * 60
# How many recent turns to feed the planner. Small on purpose — the last few turns
# carry essentially all the referential context ("it", "her", "that") a follow-up needs.
MAX_TURNS = 8
# Hard cap on what we keep on disk, so the file can't grow without bound.
_STORE_CAP = 60


def _load() -> list:
    try:
        with open(_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def record(user_text: str, reply: str, *, interpretation: str = "") -> None:
    """Append one completed turn (what the user said + what Sahur did/answered) so the
    NEXT turn can refer back to it. Best-effort — never raises into the agent loop."""
    user_text = (user_text or "").strip()
    reply = (reply or "").strip()
    if not user_text and not reply:
        return
    try:
        turns = _load()
        turns.append({
            "user": user_text,
            "reply": reply,
            "interpretation": (interpretation or "").strip(),
            "ts": time.time(),
        })
        turns = turns[-_STORE_CAP:]
        os.makedirs(_DIR, exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(turns, f, indent=2)
    except Exception:
        pass


def recent(max_turns: int = MAX_TURNS, max_age: float = SESSION_TTL) -> list:
    """The last `max_turns` turns that fall within the current session window."""
    now = time.time()
    fresh = []
    for t in _load():
        try:
            if not isinstance(t, dict):
                continue
            if now - float(t.get("ts", 0)) <= max_age:
                fresh.append(t)
        except Exception:
            continue
    return fresh[-max_turns:]


def transcript(max_turns: int = MAX_TURNS, max_age: float = SESSION_TTL) -> str:
    """Render the recent dialogue as a compact transcript for prompting. Empty string
    when there's nothing fresh (a genuinely new conversation), so callers can cheaply
    skip the block. Oldest first, newest last — the order a model reads naturally.

        you: find 10 fruit tiktoks over 50k likes and grab the links
        sahur: done — collected 10 links about "fruit"
        you: now send those to my girlfriend
    """
    lines: list[str] = []
    for t in recent(max_turns, max_age):
        u = (t.get("user") or "").strip()
        r = (t.get("reply") or "").strip()
        if u:
            lines.append(f"you: {u}")
        if r:
            lines.append(f"sahur: {r}")
    return "\n".join(lines)


def clear() -> None:
    """Drop the whole transcript (e.g. on an explicit 'start over')."""
    try:
        if os.path.exists(_FILE):
            os.remove(_FILE)
    except Exception:
        pass
