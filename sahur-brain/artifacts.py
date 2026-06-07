"""
artifacts.py — durable cross-TURN memory for the orchestrator's blackboard.

The orchestrator carries artifacts (collected links, etc.) between steps WITHIN one
request on an in-memory blackboard. But a voice user almost always splits the work
across turns:

    turn 1: "find me 10 fruit tiktoks over 50k likes and grab the links"
    turn 2: "ok now paste those into a new note"

Each turn is a fresh run_goal with a FRESH blackboard, so by turn 2 the links were
already gone — the save step had nothing to type and (worse) reported a false success
after merely opening Notes ("I pasted them!" — it pasted nothing). That is the exact
failure this store fixes: every artifact a step PRODUCES is saved here, and a later
turn that REFERENCES it ("those links", "them") reloads it.

NOTE: Moss is NOT this store. Moss indexes on-screen UI elements for tap grounding;
this is the results memory. Two different things that both got called "memory".
"""

from __future__ import annotations

import json
import os
import time

_DIR = os.path.join(os.path.dirname(__file__), "records")
_FILE = os.path.join(_DIR, "artifacts.json")
# "those links" almost always means the ones from a moment ago. After an hour, assume a
# new request rather than silently typing yesterday's links into a note.
_TTL = 60 * 60


def _load() -> dict:
    try:
        with open(_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save(key: str, value, query: str = "") -> None:
    """Persist one produced artifact (e.g. 'links') so a later turn can still consume it."""
    if not key or value in (None, [], "", {}):
        return
    data = _load()
    data[key] = {"value": value, "query": query, "ts": time.time()}
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_fresh(max_age: float = _TTL) -> dict:
    """{key: value} for every artifact saved within max_age seconds — used to seed a new
    turn's blackboard so 'paste those links' works even though it is a separate request."""
    now = time.time()
    out: dict = {}
    for k, rec in _load().items():
        try:
            if not isinstance(rec, dict):
                continue
            val = rec.get("value")
            if val in (None, [], "", {}):
                continue
            if now - float(rec.get("ts", 0)) <= max_age:
                out[k] = val
        except Exception:
            continue
    return out


def describe_fresh(max_age: float = _TTL) -> str:
    """A short human-readable inventory of the results already in memory, fed to the PLANNER
    so it can REUSE them instead of redundantly re-finding what the user is referring back to.
    Empty string when nothing fresh is stored. e.g.
        "links": 1 item(s) about "love" (collected just now)"""
    now = time.time()
    parts: list[str] = []
    for k, rec in _load().items():
        try:
            if not isinstance(rec, dict):
                continue
            val = rec.get("value")
            if val in (None, [], "", {}):
                continue
            age = now - float(rec.get("ts", 0))
            if age > max_age:
                continue
            n = len(val) if isinstance(val, (list, dict)) else 1
            mins = int(age // 60)
            when = "just now" if mins < 1 else f"{mins} min ago"
            desc = f'"{k}": {n} item(s)'
            q = (rec.get("query") or "").strip()
            if q:
                desc += f' about "{q}"'
            desc += f" (collected {when})"
            parts.append(desc)
        except Exception:
            continue
    return "; ".join(parts)
