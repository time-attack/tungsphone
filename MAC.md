# Tung Tung Tung Sahur — macOS 🪵🖥️

A floating orb sits on your desktop. **Press it, talk, and he does it** — he
screenshots the screen, sees the buttons, and clicks around until the task is done,
then talks back in his cloned voice. Right-click the orb to switch persona.

**The entire app is one file:** [`sahur-brain/sahur.py`](sahur-brain/sahur.py). No
device extension, no server, no CLI to watch (the orb shows everything), and **nothing shared
with the iPhone project** — it imports zero code from it.

## How he does it

A press starts one turn:

```
  press orb → 🎙 Whisper (local STT) → 📸 screenshot + read the clickable elements
            → 🧠 MiniMax plans the action(s)  → ⚡ do it → repeat until done
            → 🗣 reply in the persona's cloned voice
```

It's a **hybrid**, so it stays reliable instead of fumbling the UI:

- **Web tasks** ("news about X", "look up X", "open <site>") → one `url` action that opens
  the results/page directly — no address-bar typing.
- **App control** (play music, open/quit apps, menus) → `applescript` — app-native and
  reliable.
- **Anything else** → it sees the **screenshot** + the on-screen elements (with real
  coordinates from macOS) and **clicks a real button by index** — the vision fallback.
- It can **batch** a whole plan (`{"plan":[…]}`) in one turn, and verifies that a click
  actually changed the screen before moving on.

```
┌──────────────────── one process, one file, on your Mac ─────────────────────┐
│  the floating orb (PyObjC NSPanel) IS the whole UI — status shows in its     │
│  bubble (“opening Spotify”, “clicking Search”, “typing lofi”), not a terminal│
│                                                                              │
│  press → record → Whisper → [screenshot + AX element list] → MiniMax (vision)│
│        → CGEvent click/type/key + NSWorkspace open → loop → MiniMax TTS reply│
└──────────────────────────────────────────────────────────────────────────────┘
  One-time permission (no device extension): Accessibility + Microphone for your terminal.
```

## Run it

```bash
./scripts/run-mac.sh
```

One command. First launch asks for **Accessibility** (to read the UI + click) and
**Microphone**, both for your terminal — grant them, run again, and the orb appears.

- **Press** the orb, then talk: *"open Notes"*, *"play lofi beats on Spotify"*, *"search
  the new Mac mini in Safari."* Watch the bubble narrate what he's doing.
- **Right-click** to cycle persona (Sahur / Bibi / Trump / Charlie / Obama / Biden /
  MrBeast — any with a `<name>.png` in `assets/` and a `*_VOICE_ID` in `.env`).
- **Drag** him anywhere.

Prereqs: the `sahur-brain` venv (the conductor **setup** script builds it) + a
`MINIMAX_API_KEY` in `sahur-brain/.env`. STT is local Whisper (no key). The only Mac dep
is `pyobjc` (for the orb); `run-mac.sh` installs it on first run.

## Why MiniMax for vision?

`MiniMax-Text-01` is multimodal — it accepts the screenshot and reasons about it. The
app sends the image + element list to MiniMax's `/text/chatcompletion_v2` and gets back
one JSON action per step. The same MiniMax account also does the cloned-voice TTS.

## Notes

- **Self-contained:** `sahur.py` imports only third-party libs (pyobjc, numpy,
  sounddevice, faster-whisper, httpx) + your persona images/voices. Nothing from
  `actions.py` / `device.py` / `deeplinks.py` / etc.
- **Coordinates:** AX element centers and CGEvent clicks are both top-left-origin global
  points, so clicks land where the element is. Single primary display assumed.
- **Reliability:** native apps expose rich element trees; for web/Electron content the
  model can still scroll/open and work from what's visible. Up to 12 steps per task.
- **Accessibility identity:** running from Terminal attaches the grant to Terminal (fine
  for dev). For a double-click app, wrap `sahur.py` in a `.app` (e.g. py2app).
