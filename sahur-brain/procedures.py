"""
procedures.py — saved "how-to" macros the agent can call by name.

A procedure is a named recipe: ordered do_sequence steps + trigger phrases (+ an
optional app). They're stored in procedures.json so new ones can be added/learned at
runtime. The voice router checks these BEFORE the LLM, so e.g. "copy this tiktok's
link" just runs ["share", "copy link"] on the current video — no thinking needed.

  on_current=True  -> run on whatever app/screen is already open (don't relaunch)
  app="TikTok"     -> open that app first (used when the procedure starts cold)
"""

from __future__ import annotations

import json
import os

_FILE = os.path.join(os.path.dirname(__file__), "procedures.json")

# Built-in procedures (seed). procedures.json overrides/extends this once written.
_SEED = [
    {
        "name": "copy_tiktok_link",
        "app": "TikTok",
        "on_current": True,   # copy the link of the video that's already on screen
        "triggers": [
            "copy the link", "copy link", "copy this link", "copy the tiktok link",
            "copy this tiktok", "copy the video link", "copy video link", "grab the link",
            "get the link", "share the link", "copy that link", "save the link",
        ],
        # TikTok: tap the Share arrow -> the Share sheet -> "Copy link"
        "steps": ["share", "copy link"],
        "reply": "link copied",
    },
]


def _load() -> list:
    if os.path.exists(_FILE):
        try:
            data = json.load(open(_FILE))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return [dict(p) for p in _SEED]


def _save(procs: list) -> None:
    try:
        json.dump(procs, open(_FILE, "w"), indent=2)
    except Exception:
        pass


PROCEDURES = _load()


def match(text: str):
    """Return the first procedure whose trigger phrase appears in `text`, else None."""
    t = (text or "").lower()
    for p in PROCEDURES:
        if any(tr in t for tr in p.get("triggers", [])):
            return p
    return None


def add(name: str, steps: list, triggers: list, app: str = "", on_current: bool = True,
        reply: str = "done") -> dict:
    """Save a new procedure (and persist it) so the agent can call it later."""
    proc = {"name": name, "app": app, "on_current": on_current,
            "triggers": [s.lower() for s in triggers], "steps": steps, "reply": reply}
    PROCEDURES[:] = [p for p in PROCEDURES if p.get("name") != name] + [proc]
    _save(PROCEDURES)
    return proc


def as_route(p: dict):
    """Adapt a procedure to the (label, steps, app, reply) tuple the router returns.
    on_current -> app="" so do_sequence runs on the current screen without relaunching."""
    app = "" if p.get("on_current") else p.get("app", "")
    return (f"proc:{p['name']}", list(p["steps"]), app, p.get("reply", "done"))
