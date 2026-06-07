"""
agent.py — the LiveKit voice agent (the "brain" + voice) for PhonePhonePhoneSahur.

Pipeline (all hackathon sponsors):
    STT  : LiveKit Inference  (deepgram/nova-3) — uses ONLY your LiveKit Cloud creds, no provider key
    LLM  : MiniMax  (OpenAI-compatible endpoint; the "openai" plugin name just means OpenAI WIRE
                     FORMAT — it talks to MiniMax, not OpenAI)
    TTS  : MiniMax  T2A cloned voice (per-persona voice_id; some personas live on a 2nd account)
    Turn : semantic end-of-utterance model + Silero VAD

ARCHITECTURE — the voice LLM is a thin ROUTER, the orchestrator is the brain:
  The voice LLM has a TINY prompt and exactly ONE tool — do_task(request). It just recognizes
  "the user wants something done" and forwards their words. do_task hands off to
  orchestrator.run_goal(), which PLANS the request into sub-goals, runs each as a Moss-grounded
  sub-agent (with the full 12-tool surface), verifies, recovers, and returns a summary. The voice
  layer then speaks that summary in character.

  Why: a big prompt + many tools made MiniMax Text-01 slow (LLM timeouts) AND made it leak the
  tool call into SPOKEN text ("functions do_sequence tiktok"). One simple tool fixes both.

PERSONA is chosen ON THE DEVICE (long-press the sprite). Read at startup AND after every user
turn, so switching character changes the system prompt + the cloned voice live.

Run:
    .venv-lk/bin/python agent.py console     # talk from your terminal (local mic)
    .venv-lk/bin/python agent.py dev         # join LiveKit Cloud; SahurKit on the phone is the client
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess

import httpx
from dotenv import load_dotenv

import lk_compat  # noqa: F401  — patch LiveKit to tolerate MiniMax's null usage tokens (must precede session use)
from livekit import agents
from livekit.agents import Agent, AgentSession, StopResponse, inference
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.english import EnglishModel
from openai import OpenAI

import flavor
import orchestrator
from actions import Actions
from device import DeviceClient
from mxtts import MiniMaxTTS
from music import Music
from persona import get_persona, system_prompt

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Quiet LiveKit's very chatty DEBUG/INFO logs; we print our own clean status lines instead.
for _n in ("livekit", "livekit.agents", "livekit.plugins"):
    logging.getLogger(_n).setLevel(logging.WARNING)

_PLATFORM = os.environ.get("SAHUR_PLATFORM", "ios").lower()
_IS_MAC = _PLATFORM == "mac"
_DEFAULT_PERSONA_FILE = (
    os.path.expanduser("~/Library/Caches/sahur_persona.txt")
    if _IS_MAC else "/var/mobile/Library/Caches/sahur_persona.txt"
)
_PERSONA_FILE = os.environ.get("SAHUR_PERSONA_FILE", _DEFAULT_PERSONA_FILE)

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY")
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
# Fast non-reasoning model for the voice loop (no <think> latency, does tool calls).
MINIMAX_MODEL = os.environ.get("SAHUR_AGENT_MODEL", "MiniMax-Text-01")
TTS_MODEL = os.environ.get("MINIMAX_TTS_MODEL", "speech-02-turbo")
STT_MODEL = os.environ.get("SAHUR_STT_MODEL", "deepgram/nova-3")
# Fast model for the short in-character "on it" / "done" one-liners (plain completion, no tools).
FLAVOR_MODEL = os.environ.get("FLAVOR_MODEL", "MiniMax-Text-01")
# MiniMax first-token latency is high + variable; give it room so we don't hit the
# default ~10s timeout and retry-storm (which made a turn take ~40s).
LLM_TIMEOUT = float(os.environ.get("SAHUR_LLM_TIMEOUT", "30"))

# The LLM ONLY handles chit-chat now. Phone tasks are routed in Python (see on_user_turn_completed)
# because MiniMax unreliably WRITES tool calls as text instead of actually calling them in the
# streaming voice loop — so a "tool" would silently never run. Direct routing is what worked before.
CHAT_INSTRUCTIONS = """\
You are a live VOICE assistant on a real iPhone, chatting with the user. Reply in ONE short
sentence, fully in character. You are just talking here — phone actions are handled for you, so
do NOT describe steps, do NOT mention tools, never output code, JSON, or "functions". Just talk
like a person.
"""

# Short greetings / acknowledgements -> just chat. Everything else -> do it on the phone.
_CHITCHAT = re.compile(
    r"^\s*(hi|hey+|hello|yo|sup|wass?up|what'?s up|how are you|how'?s it going|how you doing|"
    r"thanks?|thank you|thx|ty|nice|cool|awesome|sweet|lol+|lmao|haha+|ok|okay|kk|good|great|"
    r"who are you|what can you do|what do you do|nvm|never ?mind|nothing|stop|shut ?up|"
    r"bye|goodbye|good ?night|gn|yes|yeah|yep|no|nope|sure|alright|hmm+)\b", re.I)


def _is_task(text: str) -> bool:
    """Route to the phone unless it's clearly just chit-chat. We decide in CODE (not via an LLM
    tool call) so tasks always actually run."""
    t = (text or "").strip()
    if len(t) < 2:
        return False
    if _CHITCHAT.match(t) and len(t.split()) <= 4:
        return False
    return True


# Use the full PLANNER (multi-step, sub-agents, feed engine) for anything COMPOUND — multiple
# apps / "then" / find-videos batches. Use the FAST single-shot path only for one simple intent.
_BATCH_RE = re.compile(r"\b(find|get|grab|collect|pull|fetch)\b.{0,40}"
                       r"\b(video|videos|reel|reels|tiktoks?|clips?|links?)\b", re.I)
_COMPOUND_RE = re.compile(r"\b(then|after that|afterwards|and then|next)\b|;|, and ", re.I)


def _needs_planner(text: str) -> bool:
    """Multi-step / multi-app / batch-collect -> planner. Single intent -> fast path."""
    t = (text or "").lower()
    if _COMPOUND_RE.search(t):
        return True
    return bool(_BATCH_RE.search(t))

GREETINGS = {
    "sahur":   "tung tung tung SAHUR! talk to me — what you need?",
    "bibi":    "My friends, Bibi here — tell me what you need, it was promised to us three thousand years ago.",
    "trump":   "Folks, it's Trump — the best phone assistant ever, believe me. What do you need?",
    "charlie": "Charlie Kirk here — prove me wrong, but I can do whatever you need. What's up?",
    "obama":   "Now, look — it's Barack. What can I do for you, folks?",
    "biden":   "Look folks, here's the deal — Joe's on the line, no malarkey. What do you need?",
    "mrbeast": "YO, it's MrBeast and this is gonna be INSANE — what do you need? Let's GO!",
}

# Markers that mean the model leaked a tool call / code / JSON into spoken text. If a reply
# contains any of these, we stay SILENT rather than read pseudocode aloud.
_TOOLCALL_RE = re.compile(
    r'(\bfunctions?\b\s*[.\[]|do_task\s*\(|do_sequence\s*\(|find_videos\s*\(|tap_semantic\s*\('
    r'|"app"\s*:|"steps"\s*:|```|tool_call|<\|)', re.I)


def _looks_like_toolcall(text: str) -> bool:
    t = (text or "").strip()
    return bool(t.startswith("{") or _TOOLCALL_RE.search(t))


def read_active_persona(mcp) -> str:
    """Read the active-persona name (set by tapping/long-pressing the sprite). Default 'sahur'."""
    try:
        if _IS_MAC:
            with open(_PERSONA_FILE) as f:
                out = f.read()
        else:
            out = mcp.run_command(f"cat {_PERSONA_FILE} 2>/dev/null")
            if isinstance(out, dict):
                out = out.get("output", "")
        name = str(out or "").strip().lower()
        name = name.split()[0] if name else ""
        return name or "sahur"
    except Exception:
        return "sahur"


# ── Mac handoff ─────────────────────────────────────────────────────────────
# The voice agent runs ON the Mac (talking to the iPhone over USB). When the user
# EXPLICITLY says to do something on their Mac, we transfer the instruction to the
# Mac agent (sahur.py) — which has its own active persona (set by the floating orb)
# and drives the desktop via AppleScript/Accessibility.

_MAC_PERSONA_FILE = os.path.expanduser("~/Library/Caches/sahur_persona.txt")

# ONLY route to the Mac when the user clearly names it — never for an ordinary phone task.
_MAC_RE = re.compile(
    r"\bon\s+(?:the\s+)?mac\b"
    r"|\b(?:on|onto|to|over to|over on|from|using|via|open|run|check|do)\b[^.]*?"
    r"\b(?:my|the|your)\s+(?:mac|macbook|computer|laptop|desktop)\b"
    r"|\bmy\s+(?:mac|macbook|computer|laptop|desktop)\b",
    re.I)


def _is_mac_task(text: str) -> bool:
    return bool(_MAC_RE.search(text or ""))


def read_mac_persona() -> str:
    """Which persona the Mac orb (sahur.py) currently has active. Default 'sahur'."""
    try:
        with open(_MAC_PERSONA_FILE) as f:
            parts = f.read().strip().lower().split()
        return (parts[0] if parts else "") or "sahur"
    except Exception:
        return "sahur"


def _mac_command(text: str) -> str:
    """Strip the 'hey go on my mac and …' routing wrapper down to the real instruction."""
    t = _MAC_RE.sub(" ", text or "")
    t = re.sub(r"\b(hey|yo|ok|okay|please|can you|could you|would you|go|jump|head|"
               r"switch|transfer|over|and then|and|then|just|also|now)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,.;:")
    return t or (text or "").strip()


def run_on_mac(command: str) -> tuple[str, str]:
    """Hand ONE instruction to the Mac agent (sahur.py) in its OWN venv and return
    (mac_persona, result_text). The Mac speaks the result in its cloned voice; we
    capture the text for the console + handback."""
    here = os.path.dirname(os.path.abspath(__file__))
    mac_py = os.path.join(here, ".venv", "bin", "python")          # the Mac orb's venv
    if not os.path.exists(mac_py):
        mac_py = os.path.join(here, ".venv-lk", "bin", "python")   # last resort (may lack mac deps)
    script = os.path.join(here, "sahur.py")
    if not os.path.exists(script):
        raise FileNotFoundError("sahur.py (the Mac agent) not found next to agent.py")
    proc = subprocess.run([mac_py, script, "task", command],
                          capture_output=True, text=True, timeout=240, cwd=here)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    def _marker(tag: str) -> str:
        m = re.search(rf"^{tag}::(.*)$", out, re.M)
        return m.group(1).strip() if m else ""

    persona = _marker("MAC_PERSONA") or read_mac_persona()
    result = _marker("MAC_RESULT") or (
        "done on your Mac" if proc.returncode == 0
        else f"the Mac agent errored ({(proc.stderr or '').strip()[-120:] or 'no output'})")
    return persona, result


def build_stt() -> inference.STT:
    """LiveKit Inference STT — billed on your LiveKit Cloud creds, NO provider API key needed."""
    return inference.STT(model=STT_MODEL)


def build_tts(cfg: dict) -> MiniMaxTTS:
    """MiniMax cloned-voice TTS for a persona (Obama/Biden/MrBeast use a 2nd-account key)."""
    return MiniMaxTTS(
        api_key=cfg.get("api_key") or MINIMAX_API_KEY,
        model=TTS_MODEL,
        voice_id=cfg["voice"],
        sample_rate=32000,
        language_boost="English",
    )


def build_llm() -> openai.LLM:
    return openai.LLM(
        model=MINIMAX_MODEL,
        api_key=MINIMAX_API_KEY,
        base_url=MINIMAX_BASE_URL,
        temperature=0.6,
        timeout=httpx.Timeout(LLM_TIMEOUT),
    )


def _env_or_zshrc(*names: str) -> str:
    """Find a key from the process env, or fall back to parsing the user's ~/.zshrc
    (the key lives there but a non-login launch shell often doesn't export it, which
    silently dropped us to a fallback brain). Accepts several env-var names; the first
    that resolves wins."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    try:
        with open(os.path.expanduser("~/.zshrc")) as f:
            for line in f:
                for n in names:
                    m = re.match(rf'\s*export\s+{re.escape(n)}\s*=\s*"?([^"\s]+)"?', line)
                    if m:
                        key = m.group(1).strip()
                        os.environ[n] = key   # cache for child calls this run
                        return key
    except Exception:
        pass
    return ""


def build_brain() -> tuple[OpenAI, str]:
    """The PLANNER/ORCHESTRATOR brain — use a SMART model (this is what plans steps + grounds).
    Priority:
      1) Claude (ANTHROPIC_API_KEY) -> claude-sonnet-4-6
      2) a smart model via LiveKit Inference — your EXISTING LiveKit creds, NO new key
      3) MiniMax fallback.
    Override the model with SAHUR_BRAIN_MODEL.
    (TTS stays MiniMax cloned voices; STT stays LiveKit.)"""
    ak = _env_or_zshrc("ANTHROPIC_API_KEY")
    if ak:
        model = os.environ.get("SAHUR_BRAIN_MODEL", "claude-sonnet-4-6")
        print(f"🧠 brain: {model} (Anthropic / Claude)")
        return OpenAI(api_key=ak, base_url="https://api.anthropic.com/v1/"), model
    try:
        from livekit.agents.inference._utils import create_access_token, get_default_inference_url
        tok = create_access_token(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"], ttl=36000)
        model = os.environ.get("SAHUR_BRAIN_MODEL", "openai/gpt-4.1")
        print(f"🧠 brain: {model} (LiveKit Inference — no extra key)")
        return OpenAI(api_key=tok, base_url=get_default_inference_url()), model
    except Exception as e:
        print(f"🧠 brain: MiniMax fallback ({str(e)[:60]})")
        return OpenAI(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL), MINIMAX_MODEL


class SahurAgent(Agent):
    """Persona + a PYTHON router. No LLM tools: each user turn, we decide in code whether it's a
    phone task (run the orchestrator directly) or chit-chat (let the LLM reply). Persona/voice
    hot-swap when you switch the character on the device."""

    def __init__(self, acts: Actions, mcp: DeviceClient, brain: OpenAI, brain_model: str, persona_name: str):
        self.acts = acts
        self.mcp = mcp
        self.brain = brain                       # smart client for orchestrator/planner/flavor
        self.brain_model = brain_model
        self.persona_name = persona_name
        cfg = get_persona(persona_name)
        # Theme loop played on the laptop (ffplay) while a task runs — no TTS during work.
        self.music = Music()
        self.music.set_track(cfg.get("music", ""))
        super().__init__(instructions=cfg["persona"] + "\n\n" + CHAT_INSTRUCTIONS)

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        # 1) hot-swap persona if it changed on the device
        name = await asyncio.to_thread(read_active_persona, self.mcp)
        if name and name != self.persona_name:
            self.persona_name = name
            cfg = get_persona(name)
            await self.update_instructions(cfg["persona"] + "\n\n" + CHAT_INSTRUCTIONS)
            self.music.set_track(cfg.get("music", ""))
            try:
                self.session.tts = build_tts(cfg)
                print(f"🔁 switched to {cfg['display']}")
            except Exception as e:
                print(f"[persona] voice swap failed: {e}")

        # 2) ROUTE IN PYTHON. Do NOT rely on the LLM to call a tool — MiniMax writes tool calls
        #    as text instead of calling them, so the task would silently never run. This is the
        #    direct-dispatch approach that worked before.
        text = (getattr(new_message, "text_content", None) or "").strip()
        if not _is_task(text):
            return  # chit-chat → let the default LLM reply in character (no tools, can't misfire)

        # 2.5) EXPLICIT MAC HANDOFF — only when the user names their Mac. Read which persona
        #      is connected to the Mac, announce the transfer in the PHONE voice, then let the
        #      Mac agent execute + speak the result in ITS cloned voice.
        if _is_mac_task(text):
            mac_persona = await asyncio.to_thread(read_mac_persona)
            cmd = _mac_command(text)
            print(f"💻 MAC handoff → persona={mac_persona!r}  cmd={cmd!r}")
            try:
                quip = await asyncio.to_thread(flavor.flavor_line, self.brain, self.brain_model,
                                               self.persona_name, f"handing this to {mac_persona} on the mac")
            except Exception:
                quip = ""
            await self.session.say(quip or f"transferring to {mac_persona} on your Mac…",
                                   allow_interruptions=False)
            try:
                who, result = await asyncio.to_thread(run_on_mac, cmd)
                print(f"✅ mac done — {who}: {result}")
            except Exception as e:
                print(f"❌ mac handoff failed: {e}")
                await self.session.say(f"couldn't reach your Mac — {str(e)[:60]}",
                                       allow_interruptions=True)
            raise StopResponse()

        print(f"📱 task: {text}")
        # No TTS: don't talk. Just start the theme loop and immediately get to work.
        await asyncio.to_thread(self.music.start)

        # Compound / multi-app / batch -> full planner; single intent -> fast single-shot path.
        runner = orchestrator.run_goal if _needs_planner(text) else orchestrator.run_simple
        print(f"   route: {'planner/run_goal' if runner is orchestrator.run_goal else 'fast/run_simple'}")
        try:
            summary = await asyncio.to_thread(
                runner, self.brain, self.brain_model, self.acts, self.mcp,
                text, system_prompt(self.persona_name), print)
            print(f"✅ {summary}")
        except Exception as e:
            print(f"❌ task failed: {e}")
            summary = f"couldn't finish that — {str(e)[:80]}"
        finally:
            # kill the music the instant the work ends
            await asyncio.to_thread(self.music.stop)

        # Silent finish — no spoken summary. Skip the default LLM reply for this turn.
        raise StopResponse()

    async def tts_node(self, text, model_settings):
        """Safety net: never SPEAK leaked code/JSON. (Rare now that we don't use LLM tools.)"""
        buf = ""
        async for chunk in text:
            buf += chunk
        spoken = "" if _looks_like_toolcall(buf) else buf.strip()
        if buf.strip() and not spoken:
            print(f"🤫 (suppressed non-speech: {buf.strip()[:60]!r})")

        async def _src():
            if spoken:
                yield spoken
        async for frame in Agent.default.tts_node(self, _src(), model_settings):
            yield frame


def _wire_console_logs(session: AgentSession, cfg: dict) -> None:
    """Print clean, human-readable lines so the console is followable (you SEE the live STT)."""
    @session.on("user_input_transcribed")
    def _on_user(ev):
        try:
            if getattr(ev, "is_final", True) and getattr(ev, "transcript", "").strip():
                print(f"\n🎤 you: {ev.transcript.strip()}")
        except Exception:
            pass

    @session.on("conversation_item_added")
    def _on_item(ev):
        try:
            it = ev.item
            if getattr(it, "role", "") == "assistant":
                txt = (getattr(it, "text_content", None) or "").strip()
                if txt:
                    print(f"🗣  {cfg['display']}: {txt}")
        except Exception:
            pass


async def entrypoint(ctx: agents.JobContext):
    for _n in ("livekit", "livekit.agents", "livekit.plugins"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    await ctx.connect()

    mcp = DeviceClient()
    try:
        await asyncio.to_thread(mcp.health)
        print("📲 device control server connected")
    except Exception as e:
        print(f"⚠️  device control server not reachable ({e}) — voice works, phone actions won't until `iproxy 8090 8090`.")
    acts = Actions(mcp)
    brain, brain_model = build_brain()        # smart planner/orchestrator brain

    persona_name = await asyncio.to_thread(read_active_persona, mcp)
    cfg = get_persona(persona_name)
    print(f"🎭 persona: {cfg['display']}  |  🧠 {brain_model} (brain)  |  👂 {STT_MODEL} (LiveKit)  |  🔊 cloned voice")

    session = AgentSession(
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(cfg),
        vad=silero.VAD.load(),
        turn_detection=EnglishModel(),
        min_endpointing_delay=0.6,
        max_endpointing_delay=6.0,
    )
    _wire_console_logs(session, cfg)

    await session.start(agent=SahurAgent(acts, mcp, brain, brain_model, persona_name), room=ctx.room)
    # Deterministic greeting via TTS (NOT generate_reply: a system-only request makes MiniMax
    # 400 "chat content is empty 2013").
    greeting = GREETINGS.get(persona_name) or f"{cfg['catchphrase']}! what do you need?"
    print(f"🗣  {cfg['display']}: {greeting}")
    await session.say(greeting, allow_interruptions=True)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
