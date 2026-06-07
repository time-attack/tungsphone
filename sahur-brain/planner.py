"""
planner.py — turn a natural-language request into a verifiable, ordered PLAN.

This is what makes Sahur a real agent instead of a reflex. Instead of mapping a
sentence onto one hardcoded path, the brain first DECOMPOSES the request into a small
set of sub-goals, each with: the surface it touches, a success check, and the
artifacts it consumes/produces. The orchestrator then runs each sub-goal as a focused
sub-agent and passes artifacts (e.g. collected links) between them.

NO per-app logic lives here. The planner reasons only over the GENERIC capability
catalog + the user's words, so it works for any app/task the device can express —
"find 10 fruit videos over 50k likes and save them to a note", "text mom I'm late",
"play my gym playlist then start a 20-min timer". If planning ever fails, the caller
falls back to a single free-form UI step, so we degrade to the old behaviour, never to
nothing.
"""

from __future__ import annotations

import json
import re

import deeplinks


# The generic capabilities a step can be executed with. The planner picks one per
# sub-goal; the orchestrator knows how to run each. This is the whole "tool surface"
# the planner reasons about — deliberately tiny and app-agnostic.
CAPABILITIES = """\
- "find_videos": collect N videos on a video app (TikTok/Instagram/YouTube) matching a
  topic and a minimum like count, and gather their share links. Produces artifact "links".
  args: {"query": <topic>, "min_likes": <int>, "count": <int>, "app": <app name>}
- "ui": drive the phone's UI directly to accomplish the goal — open an app, read the
  screen, tap/type/swipe through the real elements. Use this for ANYTHING that isn't
  exactly find_videos: composing a message, creating a note and typing text into it,
  setting a timer, navigating, playing music, etc. args: {} (the goal text drives it)."""


_SYS = """You are the PLANNER for an on-device phone agent. You convert a user's spoken
request into a short ordered plan of verifiable sub-goals. You do NOT operate the phone;
you only decompose intent.

Rules:
- Decompose into the FEWEST steps that fully satisfy the request. Most requests are 1-2
  steps. Never pad.
- Each step names a capability ("find_videos" or "ui"), a natural-language goal, a
  concrete success check, and (optionally) artifacts it produces/consumes.
- Pass results between steps via artifacts. e.g. step 1 find_videos PRODUCES "links"; a later
  "ui" step that saves them CONSUMES "links" (the executor will type them into the note).
- Infer the destination/app from the user's words. Do NOT invent steps the user didn't
  ask for. Do NOT hardcode app-specific tap sequences — that's the executor's job.
- For "find N videos with X+ likes" style requests, DEFAULT the video app to "TikTok"
  unless the user explicitly names another (Instagram, YouTube). Short-form "videos/reels"
  with a like threshold = TikTok.
- Output STRICT JSON only, no prose, in this shape:
{"interpretation": "<one plain-English sentence of what the user actually wants>",
 "steps": [
   {"id": 1, "capability": "find_videos|ui", "goal": "<imperative goal>",
    "args": {<capability args>}, "produces": "<artifact key or null>",
    "consumes": ["<artifact key>", ...]}
 ]}

Available capabilities:
%s

Known apps it can open: %s""" % (CAPABILITIES, ", ".join(a.name for a in deeplinks.APPS))


def _coerce(d: dict) -> dict:
    """Normalise a parsed plan: ints, defaults, artifact lists, drop junk steps."""
    steps_out = []
    for i, s in enumerate(d.get("steps", []) or [], start=1):
        if not isinstance(s, dict):
            continue
        cap = str(s.get("capability", "ui")).strip().lower()
        cap = cap if cap in ("find_videos", "ui") else "ui"
        goal = str(s.get("goal", "")).strip()
        if not goal:
            continue
        args = s.get("args") if isinstance(s.get("args"), dict) else {}
        if cap == "find_videos":
            args = {
                "query": str(args.get("query", "")).strip() or goal,
                "min_likes": int(args.get("min_likes") or 100000),
                "count": max(1, min(int(args.get("count") or 5), 30)),
                "app": str(args.get("app", "TikTok")).strip() or "TikTok",
            }
        produces = s.get("produces")
        produces = str(produces).strip() if produces and str(produces).lower() != "null" else None
        consumes = s.get("consumes") or []
        if isinstance(consumes, str):
            consumes = [consumes]
        consumes = [str(c).strip() for c in consumes if str(c).strip()]
        steps_out.append({"id": i, "capability": cap, "goal": goal, "args": args,
                          "produces": produces, "consumes": consumes})
    return {"interpretation": str(d.get("interpretation", "")).strip(), "steps": steps_out}


def _first_json(text: str) -> dict:
    """Extract the FIRST balanced {...} object (robust to trailing prose / extra objects that
    make a greedy regex produce 'Extra data' JSON errors)."""
    s = text or ""
    i = s.find("{")
    if i < 0:
        return {}
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[i:j + 1])
                except Exception:
                    return {}
    return {}


def make_plan(client, model: str, command: str) -> dict:
    """Ask the brain to decompose `command` into a verifiable plan. Returns a dict with
    `interpretation` and `steps`. On any failure returns a single free-form UI step
    (so the caller degrades to the old single-loop behaviour, never to nothing)."""
    fallback = {"interpretation": command,
                "steps": [{"id": 1, "capability": "ui", "goal": command, "args": {},
                           "produces": None, "consumes": []}]}
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": command}],
            temperature=0,
            max_tokens=500,
        )
        d = _first_json(r.choices[0].message.content or "")
        if not d:
            return fallback
        plan = _coerce(d)
        return plan if plan["steps"] else fallback
    except Exception as e:
        print(f"[planner] {e}")
        return fallback
