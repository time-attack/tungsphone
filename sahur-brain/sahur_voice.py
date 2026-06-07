"""
sahur_voice.py — voice front-end for the proven control_proof brain.

talk  ->  local Whisper (STT, no API/key)  ->  MiniMax brain + Moss + device control server
      ->  phone acts (Sahur walks to each tap)  ->  MiniMax TTS reply (his voice)

Why local Whisper: MiniMax has no STT endpoint (verified — all ASR paths 404).
Whisper here is on-device, no external API, no key. MiniMax still does the brain
and the spoken reply; Moss does the retrieval/grounding.

Run on the laptop (phone reachable via iproxy):
    python sahur_voice.py

Continuous listening with energy-based endpointing: it waits for you to speak,
captures the utterance, stops when you pause (~0.8s silence), then acts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from openai import OpenAI

import actions as A
from actions import Actions
import threading

import conversation
from flavor import flavor_line, closing_line
from intents import route as fast_route
from device import DeviceClient
from music import Music
from persona import get_persona, system_prompt
import orchestrator

load_dotenv(".env")

SR = 16000                  # Whisper wants 16 kHz mono
FRAME_MS = 30               # capture granularity
START_RMS = 0.015           # floor energy to begin capturing (raised by ambient calibration)
SILENCE_MS = 1300           # stop after this much trailing silence (longer -> won't cut you off mid-thought)
MIN_SPEECH_MS = 350         # ignore blips shorter than this
MAX_UTTER_S = 15


def _capture_rate() -> int:
    """Native sample rate of the current default input device. Bluetooth mics
    (AirPods) run at 24 kHz and REFUSE a forced 16 kHz open (CoreAudio err -50),
    so we capture at the device rate and resample down ourselves."""
    try:
        sr = int(sd.query_devices(kind="input")["default_samplerate"])
        return sr if sr > 0 else 48000
    except Exception:
        return 48000


def _resample_to_16k(x: np.ndarray, src: int) -> np.ndarray:
    if src == SR or x.size == 0:
        return x.astype(np.float32)
    n_out = int(round(x.size * SR / src))
    if n_out < 2:
        return x.astype(np.float32)
    t_in = np.linspace(0.0, 1.0, num=x.size, endpoint=False)
    t_out = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(t_out, t_in, x).astype(np.float32)


def _clean_reply(text: str) -> str:
    """Strip the reasoning model's <think>...</think> blocks and noise before TTS."""
    import re
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    text = re.sub(r"</?think>", "", text, flags=re.I)
    text = text.replace("(done)", "").strip()
    return text or "done, tung tung SAHUR"


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + 1e-9))


def record_utterance() -> np.ndarray | None:
    """Block until a spoken utterance is captured; return float32 mono @16k.

    Better endpointing so it stops cutting you off:
      * ambient calibration -> adaptive threshold (robust to room/AirPods noise)
      * hysteresis -> once you're talking, brief mid-sentence pauses DON'T end the turn
      * long trailing-silence window (SILENCE_MS) before it decides you're done
      * pre-roll -> the first word isn't clipped
    Captures at the device's native rate (Bluetooth-safe) and resamples to 16k."""
    cap = _capture_rate()
    frame = int(cap * FRAME_MS / 1000)
    sil_limit = SILENCE_MS // FRAME_MS
    pre: list[np.ndarray] = []          # rolling pre-roll (~300 ms before speech)
    buf: list[np.ndarray] = []
    speaking = False
    silence_frames = 0
    speech_frames = 0
    with sd.InputStream(samplerate=cap, channels=1, dtype="float32", blocksize=frame) as stream:
        # calibrate the noise floor for ~0.4s, set a threshold above it
        ambient = []
        for _ in range(max(1, int(0.4 / (FRAME_MS / 1000)))):
            d, _ = stream.read(frame); ambient.append(_rms(d[:, 0]))
        floor = sorted(ambient)[len(ambient) // 2] if ambient else 0.005
        start_thr = max(START_RMS, floor * 3.0)     # to BEGIN talking
        keep_thr = max(START_RMS * 0.5, floor * 1.6)  # to STAY talking (hysteresis)
        start = time.time()
        while True:
            d, _ = stream.read(frame)
            x = d[:, 0]
            energy = _rms(x)
            if not speaking:
                pre.append(x.copy()); pre = pre[-10:]
                if energy > start_thr:
                    speaking = True
                    buf = list(pre)                 # include the pre-roll
                    speech_frames = 1
            else:
                buf.append(x.copy())
                if energy > keep_thr:               # still talking (low bar -> keep going)
                    speech_frames += 1
                    silence_frames = 0
                else:
                    silence_frames += 1
                if silence_frames >= sil_limit:
                    break
                if time.time() - start > MAX_UTTER_S:
                    break
    if not speaking or speech_frames * FRAME_MS < MIN_SPEECH_MS:
        return None
    return _resample_to_16k(np.concatenate(buf), cap)


class STT:
    def __init__(self):
        from faster_whisper import WhisperModel
        model = os.environ.get("WHISPER_MODEL", "base.en")
        print(f"[stt] loading local Whisper '{model}' (no API, on-device)…")
        self.model = WhisperModel(model, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        segs, _ = self.model.transcribe(audio, language="en", vad_filter=False, beam_size=1)
        return " ".join(s.text for s in segs).strip()


_TTS_FALLBACK_VOICE = "male-qn-qingse"


def _tts_once(text: str, voice: str, api_key: str | None = None) -> bytes | None:
    import httpx
    key = api_key or os.environ["MINIMAX_API_KEY"]
    base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
    group = os.environ.get("MINIMAX_GROUP_ID", "")
    url = f"{base}/t2a_v2" + (f"?GroupId={group}" if group else "")
    body = {
        "model": os.environ.get("MINIMAX_TTS_MODEL", "speech-02-turbo"),
        "text": text[:600],
        "stream": False,
        "voice_setting": {"voice_id": voice, "speed": 1.05, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 32000, "format": "mp3"},
    }
    r = httpx.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body, timeout=30)
    data = r.json()
    hexaudio = (data.get("data") or {}).get("audio")
    return bytes.fromhex(hexaudio) if hexaudio else None


def minimax_tts(text: str, voice: str | None = None, api_key: str | None = None) -> bytes | None:
    """MiniMax T2A v2 -> mp3 bytes in the active persona's voice (on that persona's
    MiniMax account if api_key is given). Falls back to the default voice if a persona
    voice_id is rejected, so speech never goes silent."""
    voice = voice or os.environ.get("SAHUR_VOICE_ID") or _TTS_FALLBACK_VOICE
    for v in (voice, _TTS_FALLBACK_VOICE):
        try:
            audio = _tts_once(text, v, api_key)
            if audio:
                return audio
        except Exception as e:
            print(f"[tts] voice '{v}' failed: {e}")
        if v == _TTS_FALLBACK_VOICE:
            break
    return None


def speak(text: str, voice: str | None = None, api_key: str | None = None):
    audio = minimax_tts(text, voice, api_key)
    if not audio:
        return
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio); path = f.name
    subprocess.run(["afplay", path], check=False)


_PLATFORM = os.environ.get("SAHUR_PLATFORM", "ios").lower()
_IS_MAC = _PLATFORM == "mac"

# On the Mac both processes share ~/Library/Caches; on iOS the file lives on-device.
_DEFAULT_PERSONA_FILE = (os.path.expanduser("~/Library/Caches/sahur_persona.txt")
                         if _IS_MAC else "/var/mobile/Library/Caches/sahur_persona.txt")
_PERSONA_FILE = os.environ.get("SAHUR_PERSONA_FILE", _DEFAULT_PERSONA_FILE)
_STATE_FILE = os.environ.get(
    "SAHUR_STATE_FILE",
    os.path.expanduser("~/Library/Caches/sahur_state.json") if _IS_MAC else "")

def set_state(state: str, caption: str = "", persona: str = "") -> None:
    """Publish the agent's state to the floating buddy (Mac only). The SahurMac
    panel polls this file to drive its listening/thinking/speaking bubble + bob.
    No-op on iOS (there the SpringBoard tweak gets Darwin notifications instead)."""
    if not _STATE_FILE:
        return
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump({"state": state, "caption": caption[:160], "persona": persona}, f)
    except Exception:
        pass


def read_active_persona(mcp) -> str:
    """Read the active-persona name (set by clicking/long-pressing the sprite).
    Defaults to 'sahur' if the file is missing/unreadable."""
    try:
        if _IS_MAC:
            with open(_PERSONA_FILE) as f:        # same machine — read it directly
                out = f.read()
        else:
            out = mcp.run_command(f"cat {_PERSONA_FILE} 2>/dev/null")
            if isinstance(out, dict):             # device control server returns {'exitcode','output'}
                out = out.get("output", "")
        name = str(out or "").strip().lower()
        # keep only the persona token (guard against stray output)
        name = name.split()[0] if name else ""
        return name or "sahur"
    except Exception:
        return "sahur"


def main():
    mcp = DeviceClient()
    try:
        mcp.health()
    except Exception as e:
        if _IS_MAC:
            sys.exit(f"SahurMac control server unreachable: {e}\n"
                     f"Start it first:  cd SahurMac && swift run  (grant Accessibility, then relaunch).")
        sys.exit(f"device control server unreachable: {e}\nRun: iproxy 8090 8090, and start the device control server server.")
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        sys.exit("MINIMAX_API_KEY missing.")
    client = OpenAI(api_key=key, base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1"))
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")
    # flavor (the spoken in-character one-liner) uses a fast NON-reasoning model so it
    # never wastes its budget on <think> and returns instantly.
    flavor_model = os.environ.get("FLAVOR_MODEL", "MiniMax-Text-01")
    acts = Actions(mcp)
    acts.moss.warm()          # load the pre-built Moss index so the first lookup is fast
    stt = STT()
    music = Music()

    def handle_command(text: str):
        """One full turn: pick persona, react in-character (spoken), run the action."""
        if not text or len(text) < 2:
            set_state("idle"); return
        print(f"\n🎙️  you: {text}")
        pname = read_active_persona(mcp)            # set by tapping/long-pressing the sprite
        pcfg = get_persona(pname)
        music.set_track(pcfg["music"])
        print(f"  · persona: {pcfg['display']}")
        set_state("thinking", persona=pname)

        hit = fast_route(text)                      # instant path for common one-shots
        result: dict = {}

        def _do_action():
            try:
                if hit:
                    # trivial single-shot command (e.g. "go home") — no need to plan
                    _, steps, app, core = hit
                    r = acts.do_sequence(steps, app=app)
                    result["log"] = r.splitlines()[0][:120]
                    result["reply"] = f"{core}, {pcfg['catchphrase']}"
                    # Record even the fast path so the running transcript stays complete —
                    # a follow-up ("now do X there") still sees this turn happened. The
                    # planner path records itself inside run_goal.
                    conversation.record(text, core)
                else:
                    # everything else goes through the PLANNER brain: decompose into
                    # verifiable sub-goals, run each as a focused sub-agent, pass
                    # artifacts (e.g. collected links) between them, verify + recover.
                    result["reply"] = orchestrator.run_goal(
                        client, model, acts, mcp, text,
                        persona_system=system_prompt(pname), log=print)
            except Exception as e:
                result["reply"] = f"({e})"

        th = threading.Thread(target=_do_action, daemon=True)
        th.start()
        quip = flavor_line(client, flavor_model, pname, text)   # in-character one-liner
        print(f"🗣  {pcfg['display']}: {quip}")
        if quip:
            set_state("speaking", caption=quip, persona=pname)
            speak(quip, voice=pcfg["voice"], api_key=pcfg.get("api_key"))   # speak FIRST
        set_state("thinking", persona=pname)
        music.start()                              # music only AFTER he's done talking
        th.join()
        music.stop()
        if result.get("log"):
            print(f"  · {result['log']}")
        # conversational wrap-up: confirm what got done + ask if they need anything else
        summary = _clean_reply(result.get("reply", "")) or "that"
        close = closing_line(client, flavor_model, pname, summary)
        print(f"🗣  {pcfg['display']} (done): {close}")
        set_state("speaking", caption=close, persona=pname)
        speak(close, voice=pcfg["voice"], api_key=pcfg.get("api_key"))
        set_state("idle")

    print(f"[ready] moss={'on' if acts.moss.enabled else 'fallback'} | model={model} | platform={_PLATFORM}")
    print("🪵 Tung Tung Tung Sahur is listening. Just talk (Ctrl-C to quit).")
    set_state("idle")
    while True:
        try:
            set_state("listening")
            audio = record_utterance()
            if audio is None:
                set_state("idle"); continue
            handle_command(stt.transcribe(audio))
        except KeyboardInterrupt:
            set_state("idle"); print("\nbye 🪵"); break
        except Exception as e:
            print(f"[loop error] {e}")


if __name__ == "__main__":
    main()
