# 🪵 Tung Tung Tung Sahur — Hackathon Demo Runbook

A ~4-minute live demo. The whole bit: **a brainrot wooden bat lives on the device,
you talk to him, he actually drives the phone/Mac, and he talks back in his own
cloned voice — chanting his theme music the whole time.** Then you turn him into
Trump. The room loses it.

> Pick ONE device to drive live. **Mac** = bulletproof, one command (`./scripts/run-mac.sh`).
> **iPhone SpringBoard** = the showstopper (he's floating ON the home screen). If the
> phone is solid on the wifi, do the phone. If anything's flaky, do the Mac.

---

## 0. Before you walk up (do this OFFSTAGE)

- [ ] App already running, orb/sprite on screen. **Don't boot it live.**
- [ ] Mac: `cd sahur-brain && ./scripts/run-mac.sh` → orb visible, draggable.
- [ ] Phone: SahurKit running, device control server server started, `curl http://<phone-ip>:8090/health` → ok.
- [ ] Spotify (or Music) **logged in**, a playlist ready. Safari open to a blank tab.
- [ ] System volume up — the **theme music loops out of the laptop** while he works.
      That's the audio energy; don't mute it.
- [ ] One clean prior run so the model/voice are warm (first call is the slow one).
- [ ] Mirror the screen big. Mic close. **You talk, he works** — don't narrate over his voice.

---

## 1. The hook (15 sec — say this, don't read it)

> "Everybody built a chatbot this weekend. We built a **brainrot creature that lives
> on your phone and actually uses it for you.** This is Tung Tung Tung Sahur. Watch —
> I'm not going to touch anything."

Hands visibly off the keyboard. That's the whole pitch: **hands off, voice in, it does it.**

---

## 2. "Let's play some music" (the opener you asked for)

Press the orb / tap Sahur. Speak:

> **"Yo Sahur — open Spotify and play my rock playlist."**

What the room sees:
- His theme kicks in and **loops while he works** (`tung tung tung… SAHUR!` energy).
- Bubble narrates live: *"opening Spotify… finding your playlist… playing."*
- Spotify actually opens and music starts.
- He talks back in his cloned voice, hyped.

> 🎙 If he asks/needs a beat, just say **"yeah, the rock one."** Letting him recover
> on his own *on stage* is more convincing than a clean first-try — lean into it.

**Why it's not a demo trick (say this while he runs):**
> "He didn't have a hardcoded 'Spotify button.' He **screenshots the screen, reads the
> real buttons, and clicks by meaning** — we index every clickable element into Moss and
> resolve the control semantically. New app, new layout, he still finds it."

---

## 3. The "okay that's actually agentic" beat (pick ONE)

You've shown he can open an app. Now show he **navigates UI he's never been told about.**

**Option A — semantic search (best):**
> **"Search for the new Mac mini on it."**
He finds the search control *by meaning* (even if it's labeled "Explore"/"Discover"),
types, submits. One sentence → multi-step UI flow.

**Option B — web task:**
> **"Look up who won the game last night."**
He opens results directly (no address-bar fumbling) and reads it back in character.

**Option C — cross-app:**
> **"Quit Spotify and open Notes, write 'we won the hackathon'."**
Shows app control + typing + that he verifies the screen actually changed between steps.

> Keep it to ONE. A demo that does two things well beats one that does five things half-way.

---

## 4. The persona flip (the laugh / the closer)

This is the mic-drop. **Right-click the orb** (Mac) / **long-press the sprite** (phone)
to cycle persona → land on **Trump**.

> "Same brain. Same agent loop. Different soul."

Press, speak the SAME kind of command:

> **"Play some music to celebrate."**

Now:
- The theme music swaps to **his** track (each persona has their own — Trump/Obama get
  *Hail to the Chief*, Bibi gets *Hava Nagila*, etc.).
- He does the task **and** roasts it in a dead-on cloned voice — *"tremendous playlist,
  the best music, believe me."*

Cycle to **MrBeast** or **Obama** for one more line if the room's hot. Then stop.

> Personas available: Sahur, Bibi, Trump, Obama, Biden, MrBeast, Charlie. Adding one is
> just a `<name>.png` + a `*_VOICE_ID` — say that, it shows it's a platform not a one-off.

---

## 5. The 20-second close (the "why this matters")

> "Three sponsors, one creature: **LiveKit** for realtime voice, **MiniMax** for the brain
> *and* the cloned voices, **Moss** for grounding every tap in real UI. No hardcoded flows —
> he infers, plans, and clicks real buttons by meaning. That's the same loop that lets an
> agent use *any* app a human can. We just made it a wooden bat that screams. Thank you."

Hands up. Done. Don't keep talking after the laugh.

---

## Commands that reliably land (your menu — keep it visible offstage)

| Say this | What he does |
|---|---|
| "open Spotify and play my rock playlist" | opens app, finds + plays playlist |
| "play some music to celebrate" | persona-appropriate playback |
| "search the new Mac mini" | semantic search inside the current app |
| "look up who won last night" | one-shot web result |
| "open Notes and write 'we shipped'" | app launch + typing |
| "quit Spotify" | app control |

---

## If it stalls (stay in character — DON'T debug on stage)

- **Long pause / thinking:** the music's still looping, so the room thinks it's intentional.
  Say in-character: *"he's cooking — give him a second, he's a wooden bat."* Most recoveries
  happen within one retry; he's built to re-read the screen and try again (up to ~12 steps).
- **Wrong button:** *"see, he saw that was wrong and he's fixing it"* — and he usually does,
  because he verifies the screen changed before moving on.
- **Hard fail:** flip persona and re-run a *different, simpler* command (open Notes). Movement
  + a new voice resets the room's attention. Never show a terminal.
- **Total wifi death (phone):** switch the mirror to the **Mac** orb and run `./scripts/run-mac.sh`
  flow instead. Same demo, no device extension, one process. Have it pre-warmed as your safety net.

---

## The one rule

**You talk. He works. You shut up while he talks back.** The demo is him doing things and
sounding insane — every second you narrate over his cloned voice, you're stepping on the joke.
