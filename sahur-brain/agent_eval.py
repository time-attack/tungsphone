"""
agent_eval.py — dry-run the agent's brain on realistic commands and monitor difficulties.

For each test command it asks the SAME brain sahur.py uses "what would you do?" (one
planning turn: screenshot + on-screen elements → an action plan), then a second AI
judges whether that first action sensibly progresses the goal and tags the difficulty.
It also checks whether the Moss auto-index already has a relevant answer. NOTHING is
executed — this only reads the brain's intended action, so it's safe to run anywhere.

    python agent_eval.py            # run the default battery
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "autoindex"))

import sahur                                   # the live runtime brain (loads .env)
try:
    from moss_index import MossIndex
    _MOSS = MossIndex()
except Exception:
    _MOSS = None

COMMANDS = [
    "play my discover weekly on spotify",
    "pull up the latest news about the stock market",
    "what's on my calendar today",
    "search youtube for lofi hip hop",
    "open my messages",
    "create a new note titled groceries",
    "google the weather in tokyo",
    "open calculator and put it in scientific mode",
    "show me my conductor workspaces",
    "directions to the nearest coffee shop in maps",
    "play some chill music",
    "text mom that i'll be late",                 # tricky: needs a contact + typing + send
    "turn on do not disturb",
    "what time is it in london",
    "open comet and go to github dot com",
    "remind me to call the dentist tomorrow",
]

JUDGE_SYS = (
    "You evaluate a Mac voice-assistant's FIRST chosen action for a user goal. The "
    "assistant can use: url (open a web page/search), applescript (control apps), open "
    "(launch an app), click (an on-screen element by index), type, key, scroll, done. "
    "For web/app tasks a direct url or applescript is better than clicking around. "
    "Reply ONLY JSON: {\"good\": true|false, \"category\": \"<correct|wrong-tool|"
    "no-action|needs-more-steps|risky|missing-capability|bad-json>\", \"difficulty\": "
    "\"<short reason it might fail or be wrong; empty if correct>\"}."
)


def plan(command: str):
    els = sahur.clickable_elements()
    shot = sahur.screenshot_b64()
    listing = "\n".join(f"[{i}] {e['name']} <{e['role']}>" for i, e in enumerate(els)) or "(none)"
    system = sahur.SYSTEM.format(label="Sahur", blurb="a fast helpful assistant")
    user = [{"type": "text", "text": f"Goal: {command}\nFrontmost app: {sahur.frontmost_name()}\n"
             f"Done so far: nothing yet\nClickable elements:\n{listing}\n\nAction or plan (JSON only):"}]
    if shot:
        user.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{shot}"}})
    raw = sahur.minimax_chat([{"role": "system", "content": system},
                              {"role": "user", "content": user}], max_tokens=420)
    obj = sahur._extract_json(raw)
    plan = obj.get("plan") if isinstance(obj.get("plan"), list) else ([obj] if obj else [])
    first = plan[0] if plan else {}
    return first, plan, raw


def classify(first: dict) -> str:
    """Deterministic read of what the agent ACTUALLY chose (no flaky judge)."""
    a = (first.get("action") or "").lower() if first else ""
    if not a:
        return "no-action"            # empty {} — the real reliability bug
    if a == "done":
        return "premature-done"       # claims finished without doing anything
    if a in ("url", "applescript", "open"):
        return "direct-path"          # reliable fast path (good)
    if a in ("click", "type", "key", "scroll"):
        return "ui-step"              # vision step (ok, multi-turn)
    return f"other:{a}"


def judge(command: str, first: dict) -> dict:
    """Secondary AI opinion — retried, and never pollutes the agent stats if it flakes."""
    for _ in range(2):
        raw = sahur.minimax_chat(
            [{"role": "system", "content": JUDGE_SYS},
             {"role": "user", "content": f"GOAL: {command}\nFIRST ACTION: {json.dumps(first)}"}],
            max_tokens=160, temperature=0.0)
        v = sahur._extract_json(raw)
        if v and "good" in v:
            return v
    return {}                          # judge unavailable for this row


def main():
    if not sahur.MINIMAX_KEY:
        print("Set MINIMAX_API_KEY in .env"); return
    print(f"Dry-run eval — {len(COMMANDS)} commands · brain={sahur.MODEL} · "
          f"moss={'on' if _MOSS and _MOSS.available() else 'off'}\n", flush=True)
    cats: dict[str, int] = {}
    troubles, moss_could_help = [], 0
    judge_ok = 0
    for i, cmd in enumerate(COMMANDS, 1):
        try:
            first, full, _ = plan(cmd)
        except Exception as e:
            first, full = {}, []
            print(f"{i:2}. {cmd}  plan ERROR: {e}", flush=True)
        cat = classify(first)                       # deterministic — what it really did
        cats[cat] = cats.get(cat, 0) + 1
        bad = cat in ("no-action", "premature-done")
        v = judge(cmd, first)
        if v:
            judge_ok += 1
        act = first.get("action", "—")
        detail = first.get("url") or first.get("app") or first.get("script") or first.get("index") or ""
        moss_hit, moss_score = "", None
        if _MOSS and _MOSS.available():
            hits = _MOSS.query(cmd, top_k=1)
            if hits and "error" not in hits[0]:
                moss_score = hits[0].get("score")
                moss_hit = f" | moss: {str(hits[0].get('text',''))[:30]} ({moss_score})"
        if bad and moss_score and moss_score >= 0.85:
            moss_could_help += 1
        jverdict = ("" if not v else (" judge:ok" if v.get("good") else f" judge:bad({v.get('difficulty','')[:40]})"))
        mark = "✗" if bad else "✓"
        print(f"{i:2}. {mark} [{cat}] {cmd}", flush=True)
        print(f"     → {act} {str(detail)[:46]}"
              f"{(' (' + str(len(full)) + ' steps)') if len(full) > 1 else ''}{moss_hit}{jverdict}", flush=True)
        if bad:
            troubles.append((cmd, cat, moss_score))

    print("\n──────── what the agent ACTUALLY did ────────", flush=True)
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {n:2}×  {c}")
    reliable = cats.get("direct-path", 0) + cats.get("ui-step", 0)
    print(f"\n  {reliable}/{len(COMMANDS)} produced a usable action; "
          f"{len(troubles)} failed (no-action / premature-done)")
    print(f"  judge (same model) returned valid JSON on only {judge_ok}/{len(COMMANDS)} "
          f"— it's too weak to auto-score, so deterministic classes above are the real signal")
    if troubles:
        print("\n  failures (and whether the Moss index already has a strong answer):")
        for cmd, cat, ms in troubles:
            help_ = f"Moss has it ({ms})" if (ms and ms >= 0.85) else "Moss weak/none"
            print(f"   • [{cat}] {cmd}  →  {help_}")
        print(f"\n  → {moss_could_help}/{len(troubles)} of the failures are already covered by the "
              f"auto-index (score ≥0.85): routing through Moss first would fix them.")


if __name__ == "__main__":
    main()
