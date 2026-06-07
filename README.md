# PhonePhonePhoneSahur 🪵

**Tung Tung Tung Sahur** lives on your iPhone's SpringBoard. Tap him, talk,
and he actually does it — opens apps, taps through UI, deep-links — then talks back in
his own cloned voice.

> "go on Spotify and play my rock playlist" → he opens Spotify and plays it, chanting
> *tung tung tung… SAHUR!*

Built for the Conversational AI Hackathon (YC, June 2026). Sponsor stack: **LiveKit**
(realtime voice), **MiniMax** (brain LLM + cloned voice), Deepgram (STT).

> 🖥️ **On a Mac?** There's a desktop port — same brain, same agentic
> "index the UI + semantic tap" loop, same floating buddy — in **one Python process,
> no server, no Swift**. It controls the Mac directly through the Accessibility API.
> Quickstart: **`./scripts/run-mac.sh`** (grant Accessibility once). See [MAC.md](MAC.md).

---

## How it works

```
                 LiveKit Cloud room
   ┌──────────────┐   (audio)   ┌──────────────────────────┐
   │ SahurKit.app │◄───────────►│  sahur-brain  (laptop)   │
   │  (on phone)  │             │  LiveKit Agent:          │
   │ LiveKit SDK  │             │   Deepgram STT           │
   │ mic + speaker│             │   MiniMax LLM (tools)    │
   └──────┬───────┘             │   MiniMax TTS (clone)    │
          │ Darwin notify       └───────────┬──────────────┘
          │ (toggle / state)                │ HTTP tool calls (LAN)
   ┌──────▼─────────────────┐    ┌──────────▼──────────────┐
   │ PhonePhonePhoneSahur   │    │ device server (on phone)│
   │ SpringBoard tweak      │    │  :8090/mcp  open_url,   │
   │ floating Sahur overlay │    │  tap, get_ui_elements…  │
   └────────────────────────┘    └─────────────────────────┘
```

- **device control server** (separate on-device component): the phone's "hands & eyes" — an
  HTTP control server (`tap`, `screenshot`, `get_ui_elements`, `open_url`, `launch_app`…) that
  runs on the device and exposes `:8090`. **It is not part of this repo** — bring your own
  on-device control server. The only piece here is our thin Python client: **`sahur-brain/device.py`**.
- **`sahur-brain/`** (Python, laptop): the brain + voice. LiveKit agent that listens,
  reasons with MiniMax + tools, and speaks in Sahur's cloned voice. Tools drive the phone
  via the device control server.
- **`SahurKit/`** (Swift app, phone): joins the LiveKit room — mic in, voice out, runs in
  the background. Bridges the SpringBoard overlay to the voice session via Darwin notifications.
- **`PhonePhonePhoneSahur/`** (Theos tweak, phone): the floating animated Sahur on the
  SpringBoard. Tap → start a turn; animates listening/thinking/speaking; shows a caption bubble.

**Action policy (hybrid):** the brain tries a curated **deep link** first (instant, reliable),
and falls back to a `read_screen → tap` agent loop using the accessibility tree for anything else.

**Moss UI grounding (`moss_ui.py`):** every screen's clickable elements (text → tap coords) are
indexed into **Moss** (the host sponsor). `tap_semantic("search")` then resolves the control by
*meaning* — e.g. it finds Instagram's tab labeled **"Explore"** for the word "search" — and taps
it. No hardcoded controls. `search_in_app(app, query)` chains this into a one-shot search. A local
synonym fallback keeps it working if Moss creds are absent. This also collapses multi-step taps
into single tool calls, which makes the weaker LLM far more reliable.

---

## Setup

### 0. On-device control server (the control layer)
Install an on-device HTTP control server that exposes `tap`, `screenshot`, `get_ui_elements`,
`open_url`, and `launch_app` over `:8090` on the device. This component is separate
from this repo and is not included here. Once it's running, verify from the laptop:
```bash
curl http://<phone-ip>:8090/health      # -> {"status":"ok",...}
```

### 1. sahur-brain (brain + voice)
```bash
cd sahur-brain
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in keys + DEVICE_BASE_URL=http://<phone-ip>:8090
```

**Clone Sahur's voice** (grab a ~15s clean clip of the character first):
```bash
python voiceclone.py sahur_sample.mp3 tungsahur01
# prints a voice_id -> put SAHUR_VOICE_ID=tungsahur01 in .env
```

**Phase 1 — prove brain → phone (no voice/UI yet):**
```bash
python control_proof.py "open spotify and play my rock playlist"
# watch the phone do it. Try: "open settings and turn on airplane mode"
```

**Phase 2 — the voice agent:**
```bash
python agent.py console      # talk to Sahur from your terminal mic
python agent.py dev          # connect to LiveKit Cloud (use the sandbox or SahurKit)
```

### 2. SahurKit (phone-side voice client)
```bash
cd SahurKit
xcodegen generate
open SahurKit.xcodeproj
```
- Set `Config.tokenURL` in `Sources/SahurSession.swift` to your laptop IP, and run
  `python token_server.py` in `sahur-brain` (or hardcode `Config.hardcodedURL/Token`).
- Set your signing team, build & run to the device. Grant mic permission. Leave it open.

### 3. PhonePhonePhoneSahur (the SpringBoard overlay)
```bash
cd PhonePhonePhoneSahur
# (optional) drop a transparent sahur.png into:
#   layout/Library/Application Support/PhonePhonePhoneSahur/sahur.png
make package THEOS_PACKAGE_SCHEME=rootless
make install THEOS_PACKAGE_SCHEME=rootless THEOS_DEVICE_IP=<phone-ip>
# SpringBoard resprings; Sahur appears bottom-right. Drag him anywhere.
```

---

## The full demo
1. `python agent.py dev` on the laptop. SahurKit open on the phone (connected).
2. Sahur is floating on the SpringBoard. **Tap him.**
3. Say: *"open Spotify and play my rock playlist."*
4. He animates listening → thinking → speaking, Spotify opens and plays, caption bubble
   shows the exchange, and he replies in his cloned voice.
5. Show the agent loop: *"open Settings and turn on Bluetooth"* (no deep link → screenshot/
   accessibility → tap).

---

## Reference: common iOS bundle ids
`com.apple.Preferences` (Settings) · `com.spotify.client` · `com.google.ios.youtube` ·
`com.apple.Maps` · `com.apple.MobileSMS` (Messages) · `com.apple.mobilesafari` · `com.apple.springboard`

## Troubleshooting
- **`control_proof` can't reach phone** → check `DEVICE_BASE_URL` and that the device control server is started.
- **MiniMax tool calls misbehave** → try `MINIMAX_MODEL=abab6.5s-chat`; or test the loop with an
  OpenAI key by pointing `MINIMAX_BASE_URL`/key at OpenAI temporarily.
- **Overlay doesn't appear** → `killall -9 SpringBoard`; confirm the tweak installed; check the
  filter targets `com.apple.springboard`.
- **No captions in the bubble** → captions are best-effort (file write may be sandboxed); the
  state animations + phrases still work. Live transcript needs SahurKit's file write to succeed.
- **Agent state not animating** → SahurKit maps LiveKit's `lk.agent.state`; verify the agent
  participant is in the room.

## Stretch (sponsor bonus)
- **Moss** semantic memory ("remember my rock playlist").
- **TrueFoundry** AI gateway in front of MiniMax (just swap `MINIMAX_BASE_URL`).
