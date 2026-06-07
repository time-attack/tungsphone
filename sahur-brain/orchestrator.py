"""
orchestrator.py — the agent's executive: plan -> run each sub-goal as a focused
sub-agent -> pass artifacts between them -> verify -> recover -> summarise.

This replaces the old flat "one reflex loop + a regex special-case" brain. Flow:

    make_plan(command)                      # decompose into verifiable sub-goals
      for each step:
        execute_step(...)                   # a SUB-AGENT scoped to just that goal
           - find_videos -> deterministic feed engine (produces the "links" artifact)
           - ui          -> a goal-scoped ReAct loop, Moss-grounded, proves DONE
        carry produced artifacts onto the blackboard so later steps can consume them
        on failure: ONE bounded re-plan of the remaining work, else stop honestly
      -> a short spoken-ready summary of what actually happened

Each sub-agent gets ONLY its sub-goal + the artifacts it needs, so its context stays
small and it can't conflate steps (that conflation is exactly what burned us before).
No per-app code: the executor grounds every tap through the same generic tools the
rest of the system uses.
"""

from __future__ import annotations

import datetime
import json
import re
import time

import actions as A
import artifacts
import conversation
import feed
import planner

_EXEC_STEPS = 16          # max tool calls a single sub-agent may take
_REPAIRS = 1              # how many times we may re-plan the remainder after a failure

# Words that refer BACK to a prior result ("those links", "them") instead of carrying
# literal text to type. When a step references prior results we pull them from the
# durable artifact store; if they're gone we FAIL HONESTLY rather than open Notes and
# claim we pasted nothing.
_REF_RE = re.compile(
    r"\b(those|them|these|that list|the (?:links?|videos?|tiktoks?|reels?|clips?|results?|ones?))\b",
    re.I)


def _references_prior(text: str) -> bool:
    return bool(_REF_RE.search(text or ""))


# Explicit find/search commands — the user wants a NEW search, not a reuse.
_EXPLICIT_FIND_RE = re.compile(
    r"\b(find me|get me|go on \w+.{0,20}find|go on \w+.{0,20}search|search for|look for|"
    r"go (?:on|to) (?:tiktok|instagram|youtube))\b", re.I)


def _wants_new_search(text: str) -> bool:
    return bool(_EXPLICIT_FIND_RE.search(text or ""))


# A "send to <person>" goal: a compose-and-send into a chat thread. Routed to the generic
# messaging capability (open thread -> focus compose -> type -> tap Send -> verify), because
# the blind do_sequence path focused the wrong field, typed after Send, and never sent.
_SEND_RE = re.compile(r"\b(send|text|message|share|forward)\b", re.I)
# recipient = what comes after 'to'/'with', minus a trailing 'in/on/via <app>'.
_RECIP_TO = re.compile(r"\bto\s+(?P<who>.+?)(?:\s+(?:in|on|via|using|through)\s+\S+.*)?$", re.I)
_RECIP_WITH = re.compile(r"\bwith\s+(?P<who>.+?)(?:\s+(?:in|on|via|using|through)\s+\S+.*)?$", re.I)
# strongest signal: "<who>'s conversation/chat/thread/dm" — survives a reworded re-plan goal
# ("...find and open my girlfriend's conversation...") where the literal "to <who>" is gone.
_RECIP_POSS = re.compile(
    r"(?P<who>[\w'’❤️ ]+?)\s*['’]s\s+(?:conversation|chat|thread|dm|messages?)\b", re.I)
# leading filler to peel off a captured recipient ("open my girlfriend" -> "my girlfriend").
_RECIP_LEAD = {"open", "find", "and", "go", "to", "the", "into", "in", "send", "text",
               "message", "with", "please", "just", "then", "now", "a", "an", "her", "him",
               "their", "them"}
# a captured span that's really the MESSAGE BODY, not a person — reject it as a recipient.
_RECIP_CONTENT = re.compile(
    r"(message|saying|good\s+morning|good\s+night|note\b|caption|link\b|video\b|['’\"])", re.I)
_APP_NAMES = {"messages", "imessage", "whatsapp", "instagram", "telegram", "messenger", "signal"}
_MSG_APPS = (("whatsapp", "WhatsApp"), ("instagram", "Instagram"), ("telegram", "Telegram"),
             ("messenger", "Messenger"), ("signal", "Signal"))


def _clean_recipient(who: str) -> str:
    """Peel leading filler verbs/articles off a captured recipient span."""
    toks = (who or "").strip().strip(".,!?").split()
    while toks and toks[0].lower() in _RECIP_LEAD:
        toks.pop(0)
    return " ".join(toks).strip()


def _ok_recipient(who: str) -> str:
    """A cleaned recipient if it's a plausible PERSON, else '' (rejects app names and
    message-body spans like 'a good morning message')."""
    who = _clean_recipient(who)
    if not who or who.lower() in _APP_NAMES:
        return ""
    if _RECIP_CONTENT.search(who) or len(who) > 40:
        return ""
    return who


def _is_send_goal(text: str) -> bool:
    return bool(_SEND_RE.search(text or ""))


def _messaging_app(text: str) -> str:
    t = (text or "").lower()
    for needle, name in _MSG_APPS:
        if needle in t:
            return name
    return "Messages"      # default: iMessage / SMS


def _extract_recipient(text: str) -> str:
    """Pull the recipient out of a send goal: 'send the link to my girlfriend in Messages'
    -> 'my girlfriend'. Empty if there's no clear person.

    Order matters. A re-plan reworded the goal to "...find and open my girlfriend's
    conversation, send ... along with a 'good morning' message" — there's no literal
    "to <who>" anymore, so the old 'with' fallback grabbed "a 'good morning' message"
    as the recipient. We try the possessive thread pattern FIRST, then 'to', then a
    'with' fallback that rejects message-body spans."""
    t = text or ""
    # 1) "<who>'s conversation/chat/thread/dm" — most robust across rewordings.
    for m in _RECIP_POSS.finditer(t):
        who = _ok_recipient(m.group("who"))
        if who:
            return who
    # 2) explicit "to <who> [in <app>]"
    m = _RECIP_TO.search(t)
    if m:
        who = _ok_recipient(m.group("who"))
        if who:
            return who
    # 3) "with <who>" — last, and only if it's not really the message body.
    m = _RECIP_WITH.search(t)
    if m:
        who = _ok_recipient(m.group("who"))
        if who:
            return who
    return ""


def _clean_send_text(text: str) -> str:
    """When sending collected content to a person, send the bare URL(s) — not the note-style
    '1. <url>  (106,817 likes)' rendering. Falls back to the original text if there are no URLs
    (so a plain message body still sends verbatim)."""
    urls = re.findall(r"https?://\S+", text or "")
    return "\n".join(urls) if urls else (text or "").strip()


def _run_send_message(acts, goal: str, text: str, log=print) -> tuple[bool, str]:
    """Generic compose-and-send: open the recipient's thread, then type+send `text`, with
    on-screen verification at each stage so it cannot fake a send."""
    app = _messaging_app(goal)
    recipient = _extract_recipient(goal)
    if not recipient:
        return False, "I couldn't tell who to send it to"
    ok, ev = acts.open_conversation(recipient, app)
    log(f"    · open_conversation(app={app!r}, who={recipient!r}) -> {ev}")
    if not ok:
        return False, ev
    ok2, ev2 = acts.send_in_thread(_clean_send_text(text))
    log(f"    · send_in_thread -> {ev2}")
    return ok2, ev2


def _needle(text: str) -> str:
    """A short, distinctive token from `text` to look for ON SCREEN to prove text really landed."""
    m = re.search(r"(?:vm\.)?tiktok\.com/(\w{6,})", text or "")
    if m:
        return m.group(1)
    m = re.search(r"https?://\S{8,}", text or "")
    if m:
        return m.group(0)[-10:]
    words = re.findall(r"\w{6,}", text or "")
    return words[0] if words else ""


def _text_on_screen(acts, needle: str) -> bool:
    """Deterministic, non-AI check: is `needle` actually visible in the current UI elements?"""
    if not needle:
        return False
    try:
        els = acts._read_elements()
    except Exception:
        return False
    nl = needle.lower()
    return any(nl in ((e.label or "") + " " + (e.value or "")).lower() for e in els)


_PLAN_SYS = (
    "Convert ONE phone sub-goal into a do_sequence plan. Output STRICT JSON ONLY:\n"
    '{"app": "<App name, or empty string if already open>", "steps": ["<step>", ...]}\n'
    "A step is a SHORT semantic tap target (grounded by Moss), or 'type: <text>', or "
    "'swipe up'/'swipe down', or 'enter'. Keep steps MINIMAL — usually 1-4.\n"
    "Rules:\n"
    "- ALWAYS set 'app' to the app the goal needs, even if the goal doesn't name it: a note->"
    "Notes, music/song/artist->Spotify, a date/event->Calendar, a text/message->Messages, a "
    "photo/selfie->Camera, finding videos->TikTok, a search->Safari. Only use empty app when the "
    "goal clearly continues in the app that is already open.\n"
    "- Do NOT add an 'open' step; the app field opens it.\n"
    "- A calendar DAY is just the day NUMBER (e.g. '21'), NEVER 'June 21' (that taps the month "
    "header and zooms out).\n"
    "- Notes opens INTO the last note, so to make a NEW note the steps are "
    "['Notes','New Note'] (tap 'Notes' top-left to go back to the list, THEN 'New Note' "
    "bottom-right), and then a 'type:' step for the body.\n"
    "- Spotify play: ['search','type: <name>','enter','first result','play'].\n"
    "- Sending a message: open the recipient's THREAD first, then type, then SEND. The order "
    "is ALWAYS [<the person's conversation>, 'type: <text>', 'send']. The compose box is at the "
    "BOTTOM of the thread. Use a 'send' step (taps the Send arrow) — NEVER 'enter' (Return just "
    "adds a newline in Messages and does not send).\n"
    "Examples:\n"
    'goal "go to June 21 in the Calendar" -> {"app":"Calendar","steps":["21"]}\n'
    'goal "play Drake" -> {"app":"Spotify","steps":["search","type: Drake","enter","first result","play"]}\n'
    'goal "text mom I\'m running late" -> {"app":"Messages","steps":["mom\'s conversation","type: I\'m running late","send"]}\n'
    'goal "open Instagram DMs and the latest chat" -> {"app":"Instagram","steps":["direct messages","first conversation"]}'
)


def _first_json(text: str) -> dict:
    """Extract the FIRST balanced {...} object. Robust to the model adding prose / a 2nd object
    after it (a plain greedy regex grabbed too much -> 'Extra data' JSON errors)."""
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


def _plan_steps(client, model, goal: str, has_text: bool) -> tuple[str, list]:
    """ONE fast LLM call: turn a sub-goal into a Moss-grounded do_sequence plan (app, steps)."""
    usr = f"Sub-goal: {goal}"
    if has_text:
        usr += "\n(There is specific text to type — leave the 'type:' step out; it is added for you.)"
    try:
        r = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": _PLAN_SYS},
                                   {"role": "user", "content": usr}],
            temperature=0, max_tokens=220)
        d = _first_json(r.choices[0].message.content or "")
    except Exception as e:
        print(f"[plan_steps] {e}")
        d = {}
    app = str(d.get("app", "")).strip()
    steps = [str(s) for s in (d.get("steps") or []) if str(s).strip()]
    return app, steps


def _run_ui_step(client, model, acts, goal: str, blackboard: dict, persona_system: str,
                 consumes=None, log=print) -> tuple[bool, str]:
    """FAST UI step: ONE LLM call -> a Moss-grounded do_sequence -> verify. No slow ReAct loop.
    The SCREEN is the judge for text (deterministic needle check), so it can't fake success."""
    blackboard = blackboard if isinstance(blackboard, dict) else {}
    expected_text = _collect_text(blackboard, consumes) if blackboard else ""
    # Does this step want a PRIOR result (the links we collected)? Either the planner
    # told us so (consumes=[...]) or the goal says "those/them/the links". If so but we
    # have nothing to type, the memory is gone — fail HONESTLY instead of opening Notes
    # and reporting a false success (the hallucination the user hit).
    wants_prior = bool(consumes) or _references_prior(goal)
    if wants_prior and not expected_text.strip():
        return False, ("I don't have those anymore (they weren't saved from the earlier "
                       "step) — ask me to grab them again, then save them")
    # SEND-A-MESSAGE goal with content to send (e.g. a recalled link): use the dedicated
    # compose-and-send capability. It opens the right thread, focuses the BOTTOM compose
    # field, types, taps the real Send arrow, and verifies the text actually left the box —
    # fixing the blind do_sequence path that focused the wrong field and never sent.
    if expected_text.strip() and _is_send_goal(goal) and _extract_recipient(goal):
        return _run_send_message(acts, goal, expected_text, log=log)
    needle = _needle(expected_text)
    app, steps = _plan_steps(client, model, goal, bool(expected_text.strip()))
    # Guarantee the REAL text gets typed (don't trust the LLM to echo long text accurately).
    if expected_text.strip():
        steps = [s for s in steps if not s.lower().startswith("type:")] + [f"type: {expected_text}"]
    if not steps:
        return False, "couldn't plan any steps"
    log(f"    · do_sequence(app={app!r}, steps={steps})")
    res = acts.do_sequence(steps, app)
    for ln in res.splitlines()[:8]:
        log(f"      {ln[:100]}")
    # VERIFY against the actual screen, never the model's word.
    if needle:
        if _text_on_screen(acts, needle):
            return True, f"verified {needle!r} is on screen"
        return False, f"{needle!r} is not on screen — the text didn't go in"
    # Judge the LAST step, not "did any step anywhere succeed". A plan like
    # search -> type -> first result -> play has plenty of ✓/typed/pressed earlier,
    # so an "any-✓" check reports success even when the final 'play' matched nothing.
    # That's the bug behind "Iceman album is playing ✓" when nothing actually played.
    # do_sequence joins its steps with " || ", so judge the LAST step's segment —
    # an earlier *optional* step (e.g. a 'sort' that wasn't present) may say "did NOT
    # change" without meaning the whole plan failed.
    segs = [s.strip() for s in res.split(" ||") if s.strip()]
    last = segs[-1] if segs else "done"
    last_low = last.lower()
    failed = ("no element matched" in last_low or "did not change" in last_low
              or "stopped:" in last_low)
    ok = (not failed) and (("✓" in last) or any(k in last_low for k in ("changed", "typed", "pressed")))
    return ok, (last[:90] if ok else f"no visible change ({last[:90]})")


def run_simple(client, model, acts, mcp, command: str, persona_system: str, log=print) -> str:
    """FAST PATH for an ordinary single-intent command (open X, play Y, go to a date, make a note):
    skip the planner entirely — one LLM call -> one Moss-grounded do_sequence -> verify.

    If the command refers BACK to something we made earlier ("paste those links into a
    note"), seed the blackboard from the durable artifact store so the links a previous
    turn collected can still be typed — and so a missing one fails honestly, not falsely."""
    # Recent dialogue so even the fast path understands a follow-up in context.
    history = conversation.transcript()
    # A single-intent QUESTION ("what did she text me", "any unread?") is a READ, not an
    # action — route it to the reader so it actually answers instead of failing in the tap
    # planner with "couldn't plan any steps".
    if _is_read_goal(command):
        ok, ev = _run_read_step(client, model, acts, mcp, command, history=history, log=log)
        reply = ev if ok else f"couldn't read that — {ev}"
        conversation.record(command, reply)
        return reply
    blackboard: dict = {}
    if _references_prior(command):
        blackboard = artifacts.load_fresh()
        if blackboard:
            log(f"  · recalled artifacts from a previous turn: {list(blackboard)}")
    ok, ev = _run_ui_step(client, model, acts, command, blackboard, persona_system, log=log)
    reply = f"done — {command}" if ok else f"couldn't fully do it — {ev}"
    conversation.record(command, reply)
    return reply


def _run_find_videos(acts, mcp, args: dict, log=print) -> tuple[bool, str, list]:
    """The deterministic collect-links capability. Returns (ok, evidence, links)."""
    res = feed.collect_links(acts, mcp, app=args["app"], query=args["query"],
                             min_likes=args["min_likes"], count=args["count"], log=log)
    links = res.get("links", [])
    for L in links:
        log(f"    #{L['rank']}  {L['likes']:,} likes  {L['url']}")
    ok = len(links) >= args["count"]
    ev = (f"collected {len(links)} links ≥ {args['min_likes']:,} likes"
          if links else f"found none ({res.get('note','')})")
    return ok, ev, links


def _collect_text(blackboard: dict, consumes: list) -> str:
    """Render the artifacts a 'save' step consumes into a plain-text block to type into a note.
    Falls back to ALL artifacts if the planner forgot to name what to consume."""
    keys = [k for k in (consumes or []) if k in blackboard] or list(blackboard.keys())
    lines: list[str] = []
    for key in keys:
        val = blackboard.get(key)
        if isinstance(val, list):
            for i, item in enumerate(val, 1):
                if isinstance(item, dict) and item.get("url"):
                    extra = f"  ({item['likes']:,} likes)" if item.get("likes") else ""
                    lines.append(f"{i}. {item['url']}{extra}")
                else:
                    lines.append(f"{i}. {item}")
        elif val:
            lines.append(str(val))
    return "\n".join(lines)


# Questions that should READ the screen and ANSWER, never change anything. Used by the
# fast path (run_simple) so a single-intent question doesn't get sent to the tap planner
# (which has no taps to plan → the "couldn't plan any steps" failure).
_READ_RE = re.compile(
    r"\b(what|who|when|where|which|how many|did|do i have|is there|are there|any\b|read|"
    r"show me|tell me|check|see what|what'?s)\b.*\b(text|texts|message|messages|imessage|dm|dms|"
    r"sent|say|said|wrote|unread|inbox|calendar|event|events|reminder|reminders|email|emails|"
    r"notification|notifications|playing|song|note|notes)\b",
    re.I)


def _is_read_goal(text: str) -> bool:
    """A question that can be answered by looking at a screen (no change to the phone)."""
    t = (text or "").strip().lower()
    if any(t.startswith(v) for v in ("send", "text ", "play ", "set ", "make ", "open ", "post ",
                                     "create ", "add ", "turn ", "call ", "reply", "delete")):
        return False
    return bool(_READ_RE.search(t))


_READ_NAV_SYS = (
    "Turn a READ/look-up goal into the app to open and the MINIMAL navigation taps to REACH "
    "the content to read. You must NEVER type, send, compose, reply, or delete — this is a "
    "read-only look. Output STRICT JSON ONLY:\n"
    '{"app": "<App name, or empty if already open>", "steps": ["<tap target>", ...]}\n'
    "Steps are short semantic tap targets grounded on-device. Rules:\n"
    "- To read the inbox / overview / 'any unread', use NO steps (just open the app).\n"
    "- To read a SPECIFIC person's thread in Messages, go to the list first, then open them: "
    'steps ["go back to the conversation list","<the person>"]. Refer to the person exactly as '
    "the user did ('my girlfriend', a name) — the device resolves it.\n"
    "- Keep it 0-3 steps. No 'open' step (the app field opens it). No typing steps EVER.\n"
    'Examples:\n'
    'goal "what did my girlfriend send today" -> {"app":"Messages","steps":["go back to the conversation list","my girlfriend"]}\n'
    'goal "any unread texts" -> {"app":"Messages","steps":[]}\n'
    'goal "what is on my calendar today" -> {"app":"Calendar","steps":["today"]}'
)


def _today_str() -> str:
    try:
        return datetime.date.today().strftime("%A, %B %-d, %Y")
    except Exception:
        return datetime.date.today().isoformat()


_READ_ANSWER_SYS = (
    "You are reading the user's PHONE SCREEN aloud for them. Below is the TEXT that is "
    "LITERALLY on the screen right now (accessibility labels, top-to-bottom; in a chat the "
    "newest messages are at the BOTTOM, and 'Your iMessage'/'You' lines were sent BY THE USER, "
    "not received). Answer the user's question using ONLY this text.\n"
    "- When the user asks what someone SENT THEM, count ONLY the OTHER person's messages — "
    "never the user's own 'Your iMessage'/'You' lines. Get the count right.\n"
    "- Quote the actual messages/values; include senders and times when present.\n"
    "- If the question is time-scoped ('today'), only count items under that day's header; "
    "today is {today}. If there is nothing matching, SAY SO plainly (e.g. \"she hasn't texted "
    "you yet today\"). \n"
    "- NEVER invent a message, sender, time, or value that isn't in the text. If the relevant "
    "screen/thread clearly isn't open, say you couldn't get to it rather than guessing.\n"
    "- A 'Recent conversation' block may precede the question. Use it ONLY to understand what "
    "the question refers to (who 'she' is, what 'that' means). NEVER answer from it — every "
    "fact you state must come from the ON SCREEN text.\n"
    "- Be concise and conversational — 1-3 sentences, ready to be spoken."
)


def _screen_text(acts) -> str:
    """All on-screen labels/values, top-to-bottom — the raw material the reader answers from."""
    try:
        els = acts._read_elements(tries=4)
    except Exception:
        return ""
    rows = []
    for e in sorted(els, key=lambda e: (e.center[1], e.center[0])):
        t = (e.label or e.value or "").strip().replace("\n", " ")
        if len(t) > 1:
            rows.append(t)
    return "\n".join(rows)


def _run_read_step(client, model, acts, mcp, goal: str, history: str = "", log=print) -> tuple[bool, str]:
    """READ capability: open the app, best-effort navigate to the content (NEVER typing or
    sending), read the live screen, and answer the user's question from ONLY what is on it.
    Honest by construction — the answer is grounded in real on-screen text, and says so when
    the info isn't there instead of faking success.

    `history` (the recent dialogue) lets a contextual question resolve — "what did she say
    after that?", "and the one before?" — against what was already discussed, while the
    ANSWER itself still comes only from the live screen (never invented from the transcript)."""
    app, steps = _plan_read_nav(client, model, goal)
    log(f"    · read(app={app!r}, nav={steps})")
    if app:
        acts.open_app(app, "open")
        acts._wait_loaded()
        time.sleep(0.6)
    # Best-effort navigation. Unlike do_sequence we DON'T abort on a no-op tap — if a nav
    # step misses we still read whatever is on screen and answer honestly from that.
    for st in steps:
        low = st.strip().lower()
        if low.startswith(("type", "send", "reply", "compose", "delete")):
            continue   # hard guarantee: a read never mutates anything
        try:
            r = acts.tap_semantic(st)
            log(f"      nav '{st}': {r[:80]}")
        except Exception as e:
            log(f"      nav '{st}' err: {str(e)[:60]}")
        time.sleep(0.6)
    screen = _screen_text(acts)
    if not screen.strip():
        return False, "I couldn't read anything on the screen"
    convo = f"Recent conversation (oldest first):\n{history.strip()}\n\n" if history.strip() else ""
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _READ_ANSWER_SYS.format(today=_today_str())},
                      {"role": "user", "content": f"{convo}My question: {goal}\n\nON SCREEN NOW:\n{screen[:6000]}"}],
            temperature=0, max_tokens=320)
        answer = (r.choices[0].message.content or "").strip()
    except Exception as e:
        log(f"    [read answer] {e}")
        return False, f"I opened it but couldn't read it back — {str(e)[:60]}"
    return (bool(answer), answer or "I looked but couldn't find an answer on the screen")


def _plan_read_nav(client, model, goal: str) -> tuple[str, list]:
    """ONE LLM call: read-goal -> (app, nav-only steps). Never includes a typing step."""
    try:
        r = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": _READ_NAV_SYS},
                                   {"role": "user", "content": f"Goal: {goal}"}],
            temperature=0, max_tokens=180)
        d = _first_json(r.choices[0].message.content or "")
    except Exception as e:
        print(f"[read_nav] {e}")
        d = {}
    app = str(d.get("app", "")).strip()
    steps = [str(s) for s in (d.get("steps") or [])
             if str(s).strip() and not str(s).strip().lower().startswith(("type", "send", "reply"))]
    return app, steps


def run_goal(client, model, acts, mcp, command: str, persona_system: str, log=print) -> str:
    """Plan `command`, execute each sub-goal as a sub-agent, carry artifacts between
    them, and return a short natural-language summary of what actually happened."""
    # Load durable memory BEFORE planning so the planner can REUSE prior results instead of
    # redundantly re-finding what the user refers back to ("find the video and send it" right
    # after we already found one). The stateless planner used to re-search every time.
    mem = artifacts.load_fresh()
    # If the user explicitly says "find me" / "go on TikTok find", they want a FRESH search —
    # don't show old artifacts to the planner or it'll falsely reuse them.
    explicit_find = _wants_new_search(command)
    if explicit_find and mem:
        log("  · explicit find request — ignoring cached artifacts")
        mem = {}
    # Load the recent DIALOGUE too, so the planner can resolve follow-ups and references
    # ("go back to those messages", "send it to her", "do that again") against what was
    # actually said earlier — turning the session into one conversation, not isolated turns.
    history = conversation.transcript()
    plan = planner.make_plan(client, model, command,
                             memory_desc=artifacts.describe_fresh() if mem else "",
                             history=history)
    steps = plan["steps"]
    log(f"  · plan: {plan.get('interpretation') or command}")
    for s in steps:
        log(f"      {s['id']}. [{s['capability']}] {s['goal']}")

    # Seed the blackboard from durable memory for artifacts this plan CONSUMES but does
    # not itself PRODUCE (e.g. "save those links" with no find step this turn), or when
    # the request refers back to a prior result. We only pull keys the plan actually
    # wants, so an unrelated command never accidentally types stale links into a note.
    blackboard: dict = {}
    produced = {s.get("produces") for s in steps if s.get("produces")}
    wanted = {c for s in steps for c in (s.get("consumes") or [])} - produced
    if wanted or _references_prior(command):
        seed = mem if _references_prior(command) else {k: mem[k] for k in wanted if k in mem}
        if seed:
            blackboard.update(seed)
            log(f"  · recalled artifacts from a previous turn: {list(seed)}")

    # SAFETY NET in case the planner still pads in a redundant find: when the request refers
    # back to a prior result ("the video", "it") and that artifact is already fresh in memory,
    # a find_videos step with no real new topic (empty / "unspecified" / the same query) is
    # just re-doing work — and worse, it OVERWRITES the result the user meant. Convert such a
    # step into a no-op that keeps the existing artifact, so we send what we already have.
    if _references_prior(command):
        for s in steps:
            if s["capability"] != "find_videos":
                continue
            key = s.get("produces")
            if not (key and key in mem):
                continue
            q = str(s["args"].get("query", "")).strip().lower()
            stale_query = (not q) or q in ("unspecified", "video", "videos", "the video")
            if stale_query:
                log(f"  · reusing the {key!r} already in memory instead of re-finding")
                blackboard[key] = mem[key]
                s["_reuse"] = key

    done_summ: list[str] = []
    read_answer: str | None = None      # the spoken result for an informational request
    i = 0
    repairs_left = _REPAIRS
    while i < len(steps):
        s = steps[i]
        log(f"  ▶ step {s['id']}/{len(steps)}: {s['goal']}")

        if s.get("_reuse"):
            # Guarded above: an existing fresh artifact already satisfies this find.
            ok, ev = True, f"reused {len(blackboard.get(s['_reuse']) or [])} {s['_reuse']} from memory"
        elif s["capability"] == "find_videos":
            ok, ev, links = _run_find_videos(acts, mcp, s["args"], log=log)
            if s.get("produces"):
                blackboard[s["produces"]] = links
                # Persist so a LATER turn ("ok now paste those into Notes") can still
                # reach them — the bug was these living only in this turn's memory.
                if links:
                    artifacts.save(s["produces"], links, query=s["args"].get("query", ""))
        elif s["capability"] == "read":
            ok, ev = _run_read_step(client, model, acts, mcp, s["goal"], history=history, log=log)
            if ok:
                read_answer = ev        # carry the actual answer out to be spoken
        else:
            # generic UI: read the screen, tap real elements, type where needed, verify.
            ok, ev = _run_ui_step(client, model, acts, s["goal"], blackboard,
                                  persona_system, consumes=s.get("consumes"), log=log)

        log(f"    {'✓' if ok else '✗'} {ev}")
        done_summ.append(("✓ " if ok else "✗ ") + s["goal"] + (f" ({ev})" if ev else ""))

        if not ok and repairs_left > 0:
            repairs_left -= 1
            log("  · step failed — re-planning the rest")
            rest = planner.make_plan(
                client, model,
                f'Original request: "{command}". So far: {"; ".join(done_summ)}. '
                f'The last step FAILED ({ev}). Plan ONLY the remaining work to still '
                f'satisfy the original request.',
                memory_desc=artifacts.describe_fresh() if artifacts.load_fresh() else "",
                history=history)
            steps = steps[:i + 1] + rest["steps"]      # splice in the new tail
            i += 1
            continue
        if not ok:
            break
        i += 1

    ok_count = sum(1 for d in done_summ if d.startswith("✓"))
    all_ok = ok_count == len(done_summ) and bool(done_summ)
    # For an informational request, the SPOKEN result must be the actual answer (the
    # messages, the calendar, etc.) — not a generic "done", which would hide what was read.
    if all_ok and read_answer:
        reply = read_answer
    elif all_ok:
        reply = f"done — {plan.get('interpretation') or command}"
    else:
        reply = ("partly done: " +
                 "; ".join(done_summ) if done_summ else "couldn't make a plan")
    # Record this turn so the NEXT one can refer back to it ("send that to her",
    # "do it again", "the other one"). This is what makes it a real conversation.
    conversation.record(command, reply, interpretation=plan.get("interpretation", ""))
    return reply
