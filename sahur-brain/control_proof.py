"""
control_proof.py — Phase 1: prove brain -> hands with no voice/UI.

Runs the MiniMax LLM with tool-calling against the live phone (via device control server). Type
a command, watch the phone do it.

    python control_proof.py "open spotify and play my rock playlist"
    python control_proof.py            # interactive REPL

Env (see .env.example):
    DEVICE_BASE_URL   http://<phone-ip>:8090
    MINIMAX_API_KEY   your MiniMax key
    MINIMAX_BASE_URL  https://api.minimax.io/v1   (OpenAI-compatible)
    MINIMAX_MODEL     MiniMax-Text-01
"""

from __future__ import annotations

import json
import os
import re
import sys

from dotenv import load_dotenv
from openai import OpenAI

import actions as A
from actions import Actions
from device import DeviceClient
from persona import INSTRUCTIONS, PERSONA

load_dotenv()

MAX_STEPS = 24

_TOOL_NAMES = {t["function"]["name"] for t in A.TOOL_SCHEMAS}


def parse_fake_call(content: str):
    """MiniMax sometimes writes a tool call as TEXT instead of using the tool API,
    e.g. `functions.tap_semantic({"target":"search"})`. Recover it so the loop
    doesn't stall. Returns (name, args_dict) or None."""
    if not content:
        return None
    m = re.search(r"(?:functions\.)?([a-z_]+)\s*\(\s*(\{.*?\})?\s*\)", content, re.S)
    if not m:
        return None
    name = m.group(1)
    if name not in _TOOL_NAMES:
        return None
    args = {}
    if m.group(2):
        try:
            args = json.loads(m.group(2))
        except ValueError:
            args = {}
    return name, args


def build_client() -> tuple[OpenAI, str]:
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        sys.exit("Set MINIMAX_API_KEY (see .env.example).")
    base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")
    return OpenAI(api_key=key, base_url=base), model


def _prune(messages: list, keep_last: int = 2) -> None:
    """Stub out old screen/tool outputs so the reasoning model's context stays small
    (huge stale screen dumps are the #1 cause of per-step slowdown). Keeps structure
    (tool_call_id pairing) intact — only shortens content of older tool messages."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    for i in tool_idx[:-keep_last] if keep_last else tool_idx:
        c = messages[i].get("content", "")
        if isinstance(c, str) and len(c) > 60 and not c.startswith("[older"):
            messages[i]["content"] = "[older step output omitted]"


def run_once(client: OpenAI, model: str, acts: Actions, command: str, system: str | None = None) -> str:
    messages = [
        {"role": "system", "content": system or (PERSONA + "\n\n" + INSTRUCTIONS)},
        {"role": "user", "content": command},
    ]
    nudges = 0
    for step in range(MAX_STEPS):
        _prune(messages)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=A.TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=256,        # one tool call needs few tokens; caps runaway reasoning
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            content = (msg.content or "").strip()
            # MiniMax sometimes writes a tool call as text — execute it anyway.
            fake = parse_fake_call(content)
            if fake:
                name, args = fake
                result = A.dispatch(acts, name, args)
                print(f"  · (text){name}({args}) -> {result.splitlines()[0][:120]}")
                messages.append({"role": "user", "content": f"[ran {name} -> {result}] continue; emit real tool calls, not text."})
                continue
            # If it stopped but the task may be incomplete, nudge it to prove DONE
            # or keep acting. Bounded so we never loop forever.
            if nudges < 2 and "DONE" not in content.upper():
                nudges += 1
                messages.append({
                    "role": "user",
                    "content": "If the request is fully complete and verified, reply with only "
                               "the word DONE. Otherwise, immediately call the next tool "
                               "(start with read_screen) — do not narrate.",
                })
                continue
            return content.replace("DONE", "").strip() or "(done)"
        nudges = 0
        for tc in msg.tool_calls:
            name = tc.function.name
            args = tc.function.arguments
            result = A.dispatch(acts, name, args)
            print(f"  · {name}({args}) -> {result.splitlines()[0][:120]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return "(hit step limit)"


def main():
    client, model = build_client()
    mcp = DeviceClient()
    try:
        health = mcp.health()
        print(f"device control server ok: {health}  | model: {model}")
    except Exception as e:
        sys.exit(f"Can't reach device control server at {mcp.base_url}: {e}\nIs the phone on and DEVICE_BASE_URL correct?")

    acts = Actions(mcp)
    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
        print(f"\n> {cmd}")
        print(run_once(client, model, acts, cmd))
        return
    print("Sahur control REPL. Type a command (Ctrl-C to quit).")
    while True:
        try:
            cmd = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not cmd:
            continue
        print(run_once(client, model, acts, cmd))


if __name__ == "__main__":
    main()
