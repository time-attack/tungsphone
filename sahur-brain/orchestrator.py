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

import json
import re

import actions as A
import artifacts
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
    "Examples:\n"
    'goal "go to June 21 in the Calendar" -> {"app":"Calendar","steps":["21"]}\n'
    'goal "play Drake" -> {"app":"Spotify","steps":["search","type: Drake","enter","first result","play"]}\n'
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
    blackboard: dict = {}
    if _references_prior(command):
        blackboard = artifacts.load_fresh()
        if blackboard:
            log(f"  · recalled artifacts from a previous turn: {list(blackboard)}")
    ok, ev = _run_ui_step(client, model, acts, command, blackboard, persona_system, log=log)
    return f"done — {command}" if ok else f"couldn't fully do it — {ev}"


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


def run_goal(client, model, acts, mcp, command: str, persona_system: str, log=print) -> str:
    """Plan `command`, execute each sub-goal as a sub-agent, carry artifacts between
    them, and return a short natural-language summary of what actually happened."""
    plan = planner.make_plan(client, model, command)
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
        mem = artifacts.load_fresh()
        seed = mem if _references_prior(command) else {k: mem[k] for k in wanted if k in mem}
        if seed:
            blackboard.update(seed)
            log(f"  · recalled artifacts from a previous turn: {list(seed)}")

    done_summ: list[str] = []
    i = 0
    repairs_left = _REPAIRS
    while i < len(steps):
        s = steps[i]
        log(f"  ▶ step {s['id']}/{len(steps)}: {s['goal']}")

        if s["capability"] == "find_videos":
            ok, ev, links = _run_find_videos(acts, mcp, s["args"], log=log)
            if s.get("produces"):
                blackboard[s["produces"]] = links
                # Persist so a LATER turn ("ok now paste those into Notes") can still
                # reach them — the bug was these living only in this turn's memory.
                if links:
                    artifacts.save(s["produces"], links, query=s["args"].get("query", ""))
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
                f'satisfy the original request.')
            steps = steps[:i + 1] + rest["steps"]      # splice in the new tail
            i += 1
            continue
        if not ok:
            break
        i += 1

    ok_count = sum(1 for d in done_summ if d.startswith("✓"))
    if ok_count == len(done_summ) and done_summ:
        return f"done — {plan.get('interpretation') or command}"
    return ("partly done: " +
            "; ".join(done_summ) if done_summ else "couldn't make a plan")
