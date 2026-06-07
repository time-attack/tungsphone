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
- "read": open an app and READ what is on screen to ANSWER a question, WITHOUT changing
  anything (no typing, no sending, no deleting). Use this whenever the user is ASKING for
  information that lives on the phone: "what did my girlfriend text me today", "read my
  messages", "any unread texts", "what's on my calendar tomorrow", "what's the top song".
  The goal text carries the exact question. args: {"app": <app name the answer lives in>}.
- "ui": drive the phone's UI to CHANGE something — open an app and tap/type/swipe to make
  it happen. Use for any ACTION: send/compose a message, create a note and type into it,
  set a timer, play music, navigate somewhere. args: {} (the goal text drives it)."""


_SYS = """You are the PLANNER for an on-device phone agent. You convert a user's spoken
request into a short ordered plan of verifiable sub-goals. You do NOT operate the phone;
you only decompose intent.

Rules:
- Decompose into the FEWEST steps that fully satisfy the request. Most requests are 1-2
  steps. Never pad, never invent steps the user didn't ask for.
- FIRST decide the kind of each step: is the user ASKING for information ("what / who /
  when / did / read / show me / any …?") or COMMANDING a change ("send / play / set /
  make / open / post")? A question that can be answered by looking at a screen is a
  "read" step — NOT "ui". Only use "ui" when something on the phone must actually change.
- A request can mix kinds: "check what she said and reply 'ok'" = a "read" step THEN a
  "ui" step. But "read my messages" alone is a SINGLE "read" step — do not add a reply.
- Each step names a capability ("find_videos" | "read" | "ui"), a natural-language goal
  (carry the user's actual question/text into it), and (optionally) artifacts it
  produces/consumes.
- DIFFERENT APPS = DIFFERENT STEPS. If the request touches two apps (e.g. find a video on
  TikTok THEN send it in Messages), that is AT LEAST two steps — never collapse cross-app
  work into one. A find_videos step for TikTok and a separate ui step for Messages.
- Pass results between steps via artifacts. e.g. step 1 find_videos PRODUCES "links"; a later
  "ui" step that saves them CONSUMES "links" (the executor will type them into the note).
- Infer the destination/app from the user's words; texts/messages = Messages, songs =
  Spotify, events/dates = Calendar, photos = Photos. Do NOT hardcode tap sequences — the
  executor grounds taps itself.
- For "find N videos with X+ likes" style requests, DEFAULT the video app to "TikTok"
  unless the user explicitly names another (Instagram, YouTube).
- NEW SEARCH vs REUSE. Two distinct cases:
  A) EXPLICIT FIND = ALWAYS NEW SEARCH. When the user says "find me", "get me", "go on
     [app] and find/search/get", "search for", "look for" — that is a COMMAND to go find
     something fresh RIGHT NOW, even if a similar-sounding artifact is already in memory.
     ALWAYS plan a find_videos step. Do NOT skip it because memory has something similar.
     "Go on TikTok find me a cute video about love" = open TikTok, search, collect a new
     link — NEVER skip TikTok and reuse an old link.
  B) BACKWARD REFERENCE = REUSE. When there is NO explicit find/search verb and the user
     just refers BACK to a prior result with a pronoun or "the" — ("the video", "it",
     "that one", "those", "send it", "the links", "paste those") — and a matching artifact
     already exists, reuse it: DO NOT add a find_videos step, just set "consumes" to the
     existing key. "Send the video to mom" (no find verb, "the video" = the one we have).
  When in doubt, treat it as case A (do the search). False reuse is far worse than a
  redundant search.
- THIS IS ONE CONTINUOUS CONVERSATION, not isolated requests. You may be given a "Recent
  conversation:" transcript of the last few turns. USE IT to resolve anything the current
  request leaves implicit — pronouns and ellipsis ("her", "him", "them", "it", "that",
  "the other one", "the second one"), follow-ups ("do that again", "again but louder",
  "now send it", "reply ok to that", "no, the other video"), and people/apps/topics named
  earlier but not repeated now. Carry the resolved meaning into each step's "goal" in plain
  words (e.g. if "her" was established as the user's girlfriend, write "send it to my
  girlfriend"). If a request would be meaningless on its own but makes sense as a follow-up
  to the transcript, interpret it as that follow-up. Only treat it as a brand-new topic when
  the transcript clearly doesn't relate.
- Output STRICT JSON only, no prose, in this shape:
{"interpretation": "<one plain-English sentence of what the user actually wants>",
 "steps": [
   {"id": 1, "capability": "find_videos|read|ui", "goal": "<imperative goal or the question>",
    "args": {<capability args>}, "produces": "<artifact key or null>",
    "consumes": ["<artifact key>", ...]}
 ]}
Read examples:
goal "what did my girlfriend text me today" ->
 {"interpretation":"Read today's messages from the user's girlfriend and report them",
  "steps":[{"id":1,"capability":"read","goal":"read the messages from my girlfriend and tell me what she sent today","args":{"app":"Messages"},"produces":null,"consumes":[]}]}
goal "any unread texts?" ->
 {"interpretation":"Check for unread text messages","steps":[{"id":1,"capability":"read","goal":"check Messages for any unread conversations and tell me who texted","args":{"app":"Messages"},"produces":null,"consumes":[]}]}
Reuse example (Already in memory: "links": 1 item(s) about "love" (collected just now)):
goal "find the video and send it to my girlfriend" ->
 {"interpretation":"Send the video already found (about love) to the user's girlfriend in Messages",
  "steps":[{"id":1,"capability":"ui","goal":"send the collected video link to my girlfriend in Messages","args":{},"produces":null,"consumes":["links"]}]}

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
        cap = cap if cap in ("find_videos", "read", "ui") else "ui"
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


def make_plan(client, model: str, command: str, memory_desc: str = "",
              history: str = "") -> dict:
    """Ask the brain to decompose `command` into a verifiable plan. Returns a dict with
    `interpretation` and `steps`. On any failure returns a single free-form UI step
    (so the caller degrades to the old single-loop behaviour, never to nothing).

    `memory_desc` is a short inventory of RESULTS already in memory (from a prior turn) so
    the planner can REUSE them instead of re-finding what the user refers back to.
    `history` is the recent DIALOGUE transcript so the planner can resolve follow-ups and
    references ("her", "that one", "again", "now send it") against what was actually said —
    this is what makes a session ONE conversation instead of disconnected one-shots."""
    fallback = {"interpretation": command,
                "steps": [{"id": 1, "capability": "ui", "goal": command, "args": {},
                           "produces": None, "consumes": []}]}
    blocks: list[str] = []
    if history.strip():
        blocks.append(f"Recent conversation (oldest first):\n{history.strip()}")
    if memory_desc.strip():
        blocks.append(f"Already in memory: {memory_desc.strip()}")
    blocks.append(f"Request: {command}")
    user_content = "\n\n".join(blocks) if len(blocks) > 1 else command
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user_content}],
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
