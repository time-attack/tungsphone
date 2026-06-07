"""
sahur.py — Tung Tung Tung Sahur for the Mac. The whole thing, one file.

A floating orb sits on your desktop. Press it, talk, and he does it: he SCREENSHOTS
the screen, sees the buttons/elements, clicks around until the task is done, then
talks back in his cloned voice. Right-click the orb to switch persona.

  press orb → 🎙 listen → 📸 screenshot + see clickable elements → 🧠 MiniMax decides
            → 🖱 click / type / open → repeat → 🗣 reply (cloned voice)

No device extension. No server. No CLI to watch — the floating orb shows everything. Nothing
here is shared with the iPhone project; this file is self-contained.

Run:  ./scripts/run-mac.sh        (first run: grant Accessibility + Microphone)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

import AppKit
import httpx
import numpy as np
import objc
import Quartz
import sounddevice as sd
from ApplicationServices import (
    AXIsProcessTrustedWithOptions, AXUIElementCopyActionNames,
    AXUIElementCopyAttributeValue, AXUIElementCreateApplication, AXValueGetValue,
    kAXChildrenAttribute, kAXDescriptionAttribute, kAXPositionAttribute,
    kAXRoleAttribute, kAXSizeAttribute, kAXTitleAttribute, kAXValueAttribute,
    kAXValueTypeCGPoint, kAXValueTypeCGSize, kAXTrustedCheckOptionPrompt,
)
from dotenv import load_dotenv
from Foundation import NSObject, NSPoint, NSTimer
from PyObjCTools import AppHelper

load_dotenv(".env")

MINIMAX_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")   # text brain
# IMPORTANT: only MiniMax-Text-01 actually reads screenshots. Some models (e.g.
# M2.7-highspeed) are blind to images, so the vision loop MUST use this one.
VISION_MODEL = os.environ.get("VISION_MODEL", "MiniMax-Text-01")
ASSETS = os.environ.get("SAHUR_ASSETS",
                        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets")))
PERSONA_FILE = os.path.expanduser("~/Library/Caches/sahur_persona.txt")
FALLBACK_VOICE = "male-qn-qingse"


# ═══════════════════════════ personas (inline) ══════════════════════════════

def _persona_defs():
    raw = [
        ("sahur", "Tung Tung Tung Sahur", "a chaotic Indonesian 'brainrot' wooden-bat creature — hyped, fast, funny, a little unhinged; chants 'tung tung tung … SAHUR!'", os.environ.get("SAHUR_VOICE_ID") or FALLBACK_VOICE, None),
        ("bibi", "Bibi Netanyahu", "grave, theatrical, statesmanlike — 'my friends', 'let me be very clear, ladies and gentlemen'", os.environ.get("BIBI_VOICE_ID"), None),
        ("trump", "Donald Trump", "brash, hyperbolic, total confidence — 'tremendous', 'believe me', 'we're gonna do it bigly'", os.environ.get("TRUMP_VOICE_ID"), None),
        ("charlie", "Charlie Kirk", "rapid-fire campus debater — 'let me ask you a question', 'here's the thing', 'prove me wrong'", os.environ.get("CHARLIE_VOICE_ID"), None),
        ("obama", "Barack Obama", "measured, professorial, smooth, dry humor — 'now look', 'let me be clear', 'folks'", os.environ.get("OBAMA_VOICE_ID"), os.environ.get("OBAMA_MINIMAX_API_KEY")),
        ("biden", "Joe Biden", "folksy, earnest — 'here's the deal', 'come on, man', 'literally', 'not a joke'", os.environ.get("BIDEN_VOICE_ID"), None),
        ("mrbeast", "MrBeast", "hyper, high-energy YouTuber — 'this is INSANE', huge numbers, relentless hype", os.environ.get("MRBEAST_VOICE_ID"), None),
    ]
    out = []
    for name, label, blurb, voice, key in raw:
        png = os.path.join(ASSETS, name + ".png")
        if os.path.exists(png):
            out.append({"name": name, "label": label, "blurb": blurb,
                        "voice": voice or FALLBACK_VOICE, "api_key": key, "png": png})
    return out or [{"name": "sahur", "label": "Sahur", "blurb": "hyped brainrot creature",
                    "voice": FALLBACK_VOICE, "api_key": None, "png": os.path.join(ASSETS, "sahur.png")}]


PERSONAS = _persona_defs()


# ═══════════════════════════ MiniMax (brain + voice) ════════════════════════

def minimax_chat(messages, max_tokens=320, temperature=0.2, api_key=None, model=None) -> str:
    r = httpx.post(f"{MINIMAX_BASE}/text/chatcompletion_v2",
                   headers={"Authorization": f"Bearer {api_key or MINIMAX_KEY}"},
                   json={"model": model or MODEL, "messages": messages,
                         "max_tokens": max_tokens, "temperature": temperature}, timeout=60)
    j = r.json()
    return ((j.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def speak(text: str, voice: str, api_key=None):
    """MiniMax T2A v2 → mp3 → afplay, in the persona's cloned voice."""
    text = _strip_think(text)
    if not text or not MINIMAX_KEY:
        return
    url = f"{MINIMAX_BASE}/t2a_v2"
    for v in (voice, FALLBACK_VOICE):
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {api_key or MINIMAX_KEY}"},
                           json={"model": os.environ.get("MINIMAX_TTS_MODEL", "speech-02-turbo"),
                                 "text": text[:600], "stream": False,
                                 "voice_setting": {"voice_id": v, "speed": 1.05, "vol": 1.0, "pitch": 0},
                                 "audio_setting": {"sample_rate": 32000, "format": "mp3"}}, timeout=30)
            hexaudio = (r.json().get("data") or {}).get("audio")
            if hexaudio:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    f.write(bytes.fromhex(hexaudio)); path = f.name
                subprocess.run(["afplay", path], check=False)
                return
        except Exception as e:
            print(f"[tts] {v}: {e}")
        if v == FALLBACK_VOICE:
            break


def _strip_think(t: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S | re.I)
    return re.sub(r"</?think>", "", t, flags=re.I).strip()


def _extract_json(t: str) -> dict:
    t = _strip_think(t)
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except ValueError:
            pass
    return {}


# ═══════════════════════════ eyes (screenshot + AX) ═════════════════════════

# dimensions of the last screenshot the model was shown (pixels) — for click_xy mapping
_EYE_W, _EYE_H = 0, 0


def screenshot_b64(maxpx=1100) -> str:
    global _EYE_W, _EYE_H
    p = "/tmp/sahur_eye.jpg"
    subprocess.run(["/usr/sbin/screencapture", "-x", "-t", "jpg", p], check=False)
    subprocess.run(["/usr/bin/sips", "-Z", str(maxpx), p], check=False, capture_output=True)
    try:
        dim = subprocess.run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", p],
                             capture_output=True, text=True).stdout
        _EYE_W = int(next(l.split()[-1] for l in dim.splitlines() if "pixelWidth" in l))
        _EYE_H = int(next(l.split()[-1] for l in dim.splitlines() if "pixelHeight" in l))
        b = base64.b64encode(open(p, "rb").read()).decode()
        os.remove(p)
        return b
    except (OSError, StopIteration, ValueError):
        return ""


def screen_points():
    """Main display size in points (CGEvent click space)."""
    f = AppKit.NSScreen.mainScreen().frame()
    return float(f.size.width), float(f.size.height)


def _a(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


def _s(el, attr) -> str:
    v = _a(el, attr)
    return str(v) if v else ""


def _pt(el):
    v = _a(el, kAXPositionAttribute)
    if v is None:
        return None
    ok, p = AXValueGetValue(v, kAXValueTypeCGPoint, None)
    return (float(p.x), float(p.y)) if ok else None


def _sz(el):
    v = _a(el, kAXSizeAttribute)
    if v is None:
        return None
    ok, s = AXValueGetValue(v, kAXValueTypeCGSize, None)
    return (float(s.width), float(s.height)) if ok else None


def _children(el):
    v = _a(el, kAXChildrenAttribute)
    return list(v) if v else []


def _actions(el):
    err, names = AXUIElementCopyActionNames(el, None)
    return list(names) if err == 0 and names else []


_ACTIONABLE = {"AXButton", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton",
               "AXPopUpButton", "AXMenuButton", "AXLink", "AXTab", "AXTextField",
               "AXSearchField", "AXComboBox", "AXTextArea", "AXRow", "AXCell",
               "AXSlider", "AXDisclosureTriangle"}


def clickable_elements(max_n=45):
    """Read the frontmost app's clickable controls with their screen-center coords.
    This is the 'find the buttons' part — the model picks one of these by index."""
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return []
    ax = AXUIElementCreateApplication(app.processIdentifier())
    out, queue, seen = [], [(ax, 0)], 0
    while queue and len(out) < max_n:
        el, d = queue.pop(0)
        seen += 1
        if seen > 6000:
            break
        if d > 0:
            role = _s(el, kAXRoleAttribute)
            pos, size = _pt(el), _sz(el)
            if pos and size and size[0] > 1 and size[1] > 1:
                clickable = ("AXPress" in _actions(el)) or (role in _ACTIONABLE)
                name = (_s(el, kAXTitleAttribute) or _s(el, kAXDescriptionAttribute)
                        or _s(el, kAXValueAttribute))[:42]
                if clickable and name:
                    out.append({"name": name, "role": role,
                                "x": int(pos[0] + size[0] / 2), "y": int(pos[1] + size[1] / 2)})
        if d < 14:
            queue.extend((c, d + 1) for c in _children(el))
    return out


def frontmost_name() -> str:
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.localizedName() if app else "?"


# ═══════════════════════════ hands (clicks/keys) ════════════════════════════

def _post(e):
    if e:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)


def click(x, y):
    p = (float(x), float(y))
    _post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, p, 0))
    time.sleep(0.03)
    _post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, p, Quartz.kCGMouseButtonLeft))
    time.sleep(0.03)
    _post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, p, Quartz.kCGMouseButtonLeft))


def type_text(text):
    for i in range(0, len(text), 8):
        chunk = text[i:i + 8]
        for down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(e, len(chunk), chunk)
            _post(e)
        time.sleep(0.006)


_KEYS = {"return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
         "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126}
_MODS = {"cmd": Quartz.kCGEventFlagMaskCommand, "command": Quartz.kCGEventFlagMaskCommand,
         "ctrl": Quartz.kCGEventFlagMaskControl, "control": Quartz.kCGEventFlagMaskControl,
         "opt": Quartz.kCGEventFlagMaskAlternate, "option": Quartz.kCGEventFlagMaskAlternate,
         "alt": Quartz.kCGEventFlagMaskAlternate, "shift": Quartz.kCGEventFlagMaskShift}


def press_key(spec):
    parts = [p.strip() for p in str(spec).lower().split("+") if p.strip()]
    if not parts or parts[-1] not in _KEYS:
        return
    flags = 0
    for m in parts[:-1]:
        flags |= _MODS.get(m, 0)
    for down in (True, False):
        e = Quartz.CGEventCreateKeyboardEvent(None, _KEYS[parts[-1]], down)
        if flags:
            Quartz.CGEventSetFlags(e, flags)
        _post(e)
        time.sleep(0.01)


def scroll(direction="down", steps=6):
    dy = -110 if direction == "down" else 110
    for _ in range(steps):
        _post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 1, dy))
        time.sleep(0.014)


def open_app(name):
    if name:
        subprocess.run(["/usr/bin/open", "-a", name], check=False)


def open_url(url):
    if url:
        subprocess.run(["/usr/bin/open", url], check=False)


def run_applescript(script):
    """App-native control (open apps, Spotify/Music play, menus). Returns output."""
    try:
        r = subprocess.run(["osascript", "-"], input=script or "",
                           capture_output=True, text=True, timeout=45)
        return (r.stdout.strip() or r.stderr.strip() or "ok")[:400]
    except Exception as e:
        return f"error: {e}"


def _sig(els):
    """Cheap screen signature to detect whether a click actually changed anything."""
    return "|".join(f"{e['name']}:{e['x']},{e['y']}" for e in els[:25])


# ═══════════════════════════ memory (Moss) ══════════════════════════════════
# Fully mossed: before acting the agent QUERIES the Moss auto-index (so it already
# knows the right app + nav path instead of fumbling), and it WRITES every screen it
# sees back into the index — so every task makes it smarter. Degrades to plain vision
# if Moss is unavailable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "autoindex"))
try:
    from moss_index import MossIndex
    _MEM = MossIndex()
    if not _MEM.available():
        _MEM = None
except Exception:
    _MEM = None

_INDEXED: set[str] = set()


def memory_hint(command: str) -> str:
    """Ask Moss what it already knows about this goal; return a hint for the model."""
    if not _MEM:
        return ""
    try:
        hits = _MEM.query(command, top_k=3)
    except Exception:
        return ""
    good = [h for h in hits if "error" not in h and isinstance(h.get("score"), (int, float)) and h["score"] >= 0.85]
    if not good:
        return ""
    lines = []
    for h in good[:3]:
        md = h.get("metadata", {})
        act = md.get("action")
        if act:                       # an indexed ACTION (e.g. Spotify) — give the exact command
            lines.append(f"- {md.get('app','?')}: {h.get('text','').split('.')[0][:55]} "
                         f"→ {md.get('action_type','run')}: {act}")
        else:                         # an indexed SCREEN — give the app + nav path
            lines.append(f"- {md.get('app','?')}: {h.get('text','')[:70]} (nav path: {md.get('path','[]')})")
    return ("MEMORY from your past exploration (prefer this — the app, command, or nav "
            "path is already known, so go straight there):\n" + "\n".join(lines))


def index_screen(app: str, els: list):
    """Write a screen the agent is looking at back into Moss (dedup per session)."""
    if not _MEM or not els:
        return
    names = [e["name"] for e in els if e.get("name")]
    if not names:
        return
    sig = hashlib.md5("|".join(sorted(names)[:40]).encode()).hexdigest()
    if sig in _INDEXED:
        return
    _INDEXED.add(sig)
    try:
        _MEM.add([{"id": hashlib.md5(f"{app}|{sig}".encode()).hexdigest()[:16],
                   "text": f"{app} screen. Controls: {', '.join(names[:30])}",
                   "metadata": {"kind": "screen", "app": app, "path": "[]",
                                "elements": json.dumps([{"name": e["name"], "role": e["role"],
                                                         "x": e["x"], "y": e["y"]} for e in els[:40]])}}])
    except Exception:
        pass


# short in-character confirmations, spoken instantly (no LLM round-trip).
_CANNED = {
    "sahur": "tung tung tung, SAHUR!", "charlie": "done — change my mind.",
    "trump": "done, and it was tremendous, believe me.", "obama": "there you go, folks.",
    "bibi": "done, my friends.", "biden": "there you go — here's the deal.",
    "mrbeast": "DONE, let's gooo!",
}


def canned(name: str) -> str:
    return _CANNED.get(name, "done")


def moss_action(command: str):
    """If Moss holds a confident, ready-to-run action (no missing parameter), return
    (action_type, action) to execute DIRECTLY — no screenshots, no LLM, ~instant."""
    if not _MEM:
        return None
    try:
        hits = _MEM.query(command, top_k=6)
    except Exception:
        return None
    for h in hits:
        if "error" in h:
            return None
        md = h.get("metadata", {})
        act = md.get("action")
        if act and (h.get("score") or 0) >= 0.9 and "QUERY" not in act:
            return md.get("action_type"), act
    return None


# ═══════════════════════════ the agent ══════════════════════════════════════

SYSTEM = """You are {label}, operating this Mac for the user. Personality: {blurb}.
Get the task DONE with the FEWEST, most RELIABLE actions. Don't fumble the UI when a
direct path exists.

You SEE a screenshot + a numbered list of the clickable elements on screen. Reply with
ONLY JSON: either one action, or a whole plan: {{"plan":[action, action, ...]}}.

Actions:
  {{"action":"url","url":"https://…"}}        open a web page / search results in the browser
  {{"action":"applescript","script":"…"}}     run AppleScript (open apps, Spotify/Music play, menus, shell)
  {{"action":"open","app":"Notes"}}           launch / focus an app by name
  {{"action":"click","index":N}}              click listed element N
  {{"action":"click_xy","x":N,"y":N}}         click a PIXEL in the screenshot (for buttons you can SEE but that are NOT in the element list — e.g. Spotify, web apps). Coords are pixels in the image shown to you.
  {{"action":"type","text":"…"}}              type into the focused field
  {{"action":"key","key":"return"}}           press a key (return, tab, escape, cmd+l, …)
  {{"action":"scroll","direction":"down"}}
  {{"action":"done","say":"<one short in-character sentence>"}}

CHOOSE THE DIRECT PATH (very important — this is how you stay reliable):
- Web search / "news about X" / "look up X" / "google X" / "pull up X" → ONE `url` action with a
  search URL, then done. NEVER click the address bar and type — just open the URL.
  e.g. {{"plan":[{{"action":"url","url":"https://www.google.com/search?q=latest+news+about+iran"}},
                 {{"action":"done","say":"pulling up the latest on Iran"}}]}}
- Open a specific site → url with that site.
- Play / control music → `applescript` (NOT just search — search alone does NOT play):
    play or resume:  tell application "Spotify" to play
    pause:           tell application "Spotify" to pause
    skip:            tell application "Spotify" to next track
  e.g. "play some music" → {{"plan":[
    {{"action":"applescript","script":"tell application \\"Spotify\\" to play"}},
    {{"action":"done","say":"<one short line, in YOUR character>"}}]}}
  Only spotify:search:<q> if they ask to FIND something specific (it just opens search, it won't play).
- Open / quit / control an app, click a MENU item → `applescript` (it's more reliable than pixel clicks).
- ONLY use `click`/`type`/`key` when there's no url or applescript path. After typing a search
  query into a field, ALWAYS follow with {{"action":"key","key":"return"}}.
- If the goal is to press an on-screen button you can SEE in the screenshot but it is NOT in the
  element list (Spotify's play button, web-app controls), use `click_xy` with its pixel coords.

You may batch independent steps in one plan. Put `done` (with a short in-character line) as the
last step when the plan finishes the task. JSON only — no prose."""


def _do_action(act, els, status, history):
    """Execute one action. Returns ('done', say) or ('click', None)/('step', None)."""
    a = (act.get("action") or "").lower()
    if a == "done":
        return ("done", act.get("say") or "done")
    if a == "url":
        u = act.get("url", ""); status(f"opening {u[:40]}"); open_url(u); history.append(f"url {u}"); time.sleep(1.4)
    elif a == "applescript":
        s = act.get("script", ""); status("running…"); out = run_applescript(s); history.append(f"applescript -> {out[:60]}"); time.sleep(0.8)
    elif a == "open":
        app = act.get("app", ""); status(f"opening {app}"); open_app(app); history.append(f"open {app}"); time.sleep(1.6)
    elif a == "type":
        t = act.get("text", ""); status(f"typing “{t}”"); type_text(t); history.append(f"type {t}"); time.sleep(0.4)
    elif a == "key":
        k = act.get("key", "return"); status(f"pressing {k}"); press_key(k); history.append(f"key {k}"); time.sleep(0.6)
    elif a == "scroll":
        d = act.get("direction", "down"); status(f"scrolling {d}"); scroll(d); history.append(f"scroll {d}"); time.sleep(0.5)
    elif a == "click":
        i = act.get("index")
        if isinstance(i, int) and 0 <= i < len(els):
            e = els[i]; before = _sig(els)
            status(f"clicking {e['name']}"); click(e["x"], e["y"]); time.sleep(0.8)
            after = _sig(clickable_elements())
            history.append(f"click {e['name']}" + ("" if after != before else " (no change)"))
        else:
            history.append("click (bad index)")
        return ("click", None)       # screen likely changed → re-screenshot before more clicks
    elif a == "click_xy":
        # the model gives PIXEL coords in the screenshot; map to screen points and click.
        ix, iy = float(act.get("x", 0)), float(act.get("y", 0))
        sw, sh = screen_points()
        sx = ix * (sw / _EYE_W) if _EYE_W else ix
        sy = iy * (sh / _EYE_H) if _EYE_H else iy
        status(f"clicking the button at ({int(sx)},{int(sy)})")
        click(sx, sy); history.append(f"click_xy ({int(sx)},{int(sy)})"); time.sleep(0.8)
        return ("click", None)
    else:
        history.append("(no action)")
    return ("step", None)


def run_task(persona, command, status):
    """Plan with MiniMax (screenshot + element index), execute, verify, repeat.

    Prefers direct paths (url / applescript) so common tasks are one fast, reliable
    turn; falls back to clicking through the UI for anything else."""
    # ⚡ fast path: if Moss already has a confident ready-to-run action, just do it —
    # no screenshots, no multi-turn LLM. This is what makes "skip song" / "play music"
    # / "turn it up" near-instant instead of a 30-second vision loop.
    direct = moss_action(command)
    if direct:
        atype, action = direct
        status("⚡ from memory")
        (open_url if atype == "url" else run_applescript)(action)
        return canned(persona.get("name", ""))

    system = SYSTEM.format(label=persona["label"], blurb=persona["blurb"])
    hint = memory_hint(command)                       # ← ask Moss for a hint otherwise
    if hint:
        status("💡 found it in memory (Moss)")
    history = []
    for _ in range(10):
        front = frontmost_name()
        status(f"looking at {front}…")
        els = clickable_elements()
        index_screen(front, els)                      # ← keep learning: index what it sees
        shot = screenshot_b64()
        listing = "\n".join(f"[{i}] {e['name']} <{e['role']}>" for i, e in enumerate(els)) or "(none visible)"
        text = (f"Goal: {command}\n" + (hint + "\n" if hint else "")
                + f"Frontmost app: {front}\nDone so far: {'; '.join(history[-6:]) or 'nothing yet'}\n"
                f"Clickable elements:\n{listing}\n"
                + (f"(The screenshot is {_EYE_W}x{_EYE_H}px — for click_xy give pixel coords in it.)\n"
                   if shot else "")
                + "\nAction or plan (JSON only):")
        user = [{"type": "text", "text": text}]
        if shot:
            user.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{shot}"}})
        plan = []
        for _attempt in range(2):                     # ← retry once on the empty-plan flake
            obj = _extract_json(minimax_chat([{"role": "system", "content": system},
                                              {"role": "user", "content": user}],
                                             max_tokens=420, model=VISION_MODEL))   # ← the model that can SEE
            plan = obj.get("plan") if isinstance(obj.get("plan"), list) else ([obj] if obj else [])
            plan = [a for a in plan if a.get("action")]
            if plan:
                break
            status("…thinking again")
        for act in plan:
            kind, say = _do_action(act, els, status, history)
            if kind == "done":
                return say
            if kind == "click":
                break            # element indices are now stale → re-screenshot and re-plan
    return canned(persona.get("name", ""))


# ═══════════════════════════ voice in (mic → Whisper) ═══════════════════════

SR, FRAME_MS, START_RMS, SIL_MS, MIN_MS, MAX_S = 16000, 30, 0.015, 800, 350, 15


def record_utterance():
    cap = int(sd.query_devices(kind="input").get("default_samplerate", 48000)) or 48000
    frame = int(cap * FRAME_MS / 1000)
    buf, speaking, sil, spoke = [], False, 0, 0
    sil_limit = SIL_MS // FRAME_MS
    with sd.InputStream(samplerate=cap, channels=1, dtype="float32", blocksize=frame) as st:
        start = time.time()
        while True:
            x = st.read(frame)[0][:, 0]
            if float(np.sqrt(np.mean(x ** 2) + 1e-9)) > START_RMS:
                speaking = True; buf.append(x.copy()); spoke += 1; sil = 0
            elif speaking:
                buf.append(x.copy()); sil += 1
            if speaking and (sil >= sil_limit or time.time() - start > MAX_S):
                break
            if not speaking and time.time() - start > 8:
                return None
    if not speaking or spoke * FRAME_MS < MIN_MS:
        return None
    a = np.concatenate(buf)
    if cap != SR:
        a = np.interp(np.linspace(0, 1, int(len(a) * SR / cap), endpoint=False),
                      np.linspace(0, 1, len(a), endpoint=False), a).astype(np.float32)
    return a


class STT:
    def __init__(self):
        from faster_whisper import WhisperModel
        name = os.environ.get("WHISPER_MODEL", "base.en")
        print(f"[stt] loading local Whisper '{name}'…")
        self.model = WhisperModel(name, device="cpu", compute_type="int8")

    def __call__(self, audio):
        segs, _ = self.model.transcribe(audio, language="en", vad_filter=False, beam_size=1)
        return " ".join(s.text for s in segs).strip()


# ═══════════════════════════ the floating orb (GUI) ═════════════════════════

_W, _H, _CHAR, _STYLE = 168.0, 196.0, 120.0, (0 | (1 << 7))


class DragView(AppKit.NSView):
    def initWithFrame_(self, f):
        self = objc.super(DragView, self).initWithFrame_(f)
        if self is None:
            return None
        self.ctl = None; self._down = None; self._moved = False
        return self

    def acceptsFirstMouse_(self, e):
        return True

    def mouseDown_(self, e):
        self._down = AppKit.NSEvent.mouseLocation(); self._moved = False
        if self.ctl:
            self.ctl.dragBegan_(self._down)

    def mouseDragged_(self, e):
        p = AppKit.NSEvent.mouseLocation()
        if self._down and abs(p.x - self._down.x) + abs(p.y - self._down.y) > 4:
            self._moved = True
        if self.ctl:
            self.ctl.dragMoved_(p)

    def mouseUp_(self, e):
        if self.ctl:
            self.ctl.dragEnded() if self._moved else self.ctl.clicked()

    def rightMouseDown_(self, e):
        if self.ctl:
            self.ctl.cycle()


class _Tick(NSObject):
    """Minimal ObjC timer target (NSTimer needs a selector); forwards to the orb."""
    def initWithOrb_(self, orb):
        self = objc.super(_Tick, self).init()
        if self is None:
            return None
        self.orb = orb
        return self

    def fire_(self, timer):
        self.orb.tick()


class Orb:
    """Plain Python controller for the floating panel (no ObjC selector rules)."""

    def __init__(self, shared):
        self.shared = shared            # {"go": Event, "busy": bool}
        self.idx = 0
        self.persona = PERSONAS[0]
        self.grab = (0.0, 0.0)
        self.lock = threading.Lock()
        self._pending = None
        self._hide_at = 0.0

    # ---- build ----
    def install(self):
        scr = AppKit.NSScreen.mainScreen().frame()
        ox, oy = scr.size.width - _W - 36, 90
        p = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(ox, oy, _W, _H), _STYLE, AppKit.NSBackingStoreBuffered, False)
        p.setLevel_(AppKit.NSFloatingWindowLevel)
        p.setOpaque_(False); p.setBackgroundColor_(AppKit.NSColor.clearColor())
        p.setHasShadow_(False); p.setHidesOnDeactivate_(False)
        p.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 7))

        view = DragView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, _W, _H))
        view.ctl = self

        iv = AppKit.NSImageView.alloc().initWithFrame_(AppKit.NSMakeRect((_W - _CHAR) / 2, 0, _CHAR, _CHAR))
        iv.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
        view.addSubview_(iv)

        bub = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(4, _H - 78, _W - 8, 74))
        bub.setWantsLayer_(True)
        bub.layer().setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0.08, 0.93).CGColor())
        bub.layer().setCornerRadius_(14)
        bub.layer().setBorderWidth_(2)
        bub.layer().setBorderColor_(AppKit.NSColor.colorWithRed_green_blue_alpha_(1, 0.78, 0.2, 1).CGColor())
        bub.setAlphaValue_(0.0)
        lbl = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(8, 4, _W - 24, 66))
        lbl.setBezeled_(False); lbl.setDrawsBackground_(False); lbl.setEditable_(False); lbl.setSelectable_(False)
        lbl.setAlignment_(AppKit.NSTextAlignmentCenter); lbl.setTextColor_(AppKit.NSColor.whiteColor())
        lbl.setFont_(AppKit.NSFont.boldSystemFontOfSize_(12)); lbl.setStringValue_("tap me, then talk")
        lbl.cell().setWraps_(True)
        bub.addSubview_(lbl)
        view.addSubview_(bub)

        p.setContentView_(view); p.orderFrontRegardless()
        self.panel, self.imgv, self.bub, self.lbl = p, iv, bub, lbl
        self.home = NSPoint(ox, oy)
        self._apply(self._restore())
        self._ticker = _Tick.alloc().initWithOrb_(self)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.1, self._ticker, "fire:", None, True)
        self.show_("tap me, then talk", 4)

    # ---- persona ----
    def _restore(self):
        try:
            n = open(PERSONA_FILE).read().strip().lower()
            return next((i for i, p in enumerate(PERSONAS) if p["name"] == n), 0)
        except OSError:
            return 0

    def _apply(self, i):
        self.idx = i % len(PERSONAS)
        self.persona = PERSONAS[self.idx]
        img = AppKit.NSImage.alloc().initWithContentsOfFile_(self.persona["png"])
        if img:
            self.imgv.setImage_(img)
        try:
            open(PERSONA_FILE, "w").write(self.persona["name"])
        except OSError:
            pass

    def cycle(self):
        self._apply(self.idx + 1); self.show_(f"now: {self.persona['label']}", 1.6)

    # ---- click → talk ----
    def clicked(self):
        if self.shared.get("busy"):
            self.show_("one sec…", 1.2); return
        self.shared["go"].set()

    # ---- thread-safe status from the worker ----
    def status(self, text):
        with self.lock:
            self._pending = text

    def tick(self):
        with self.lock:
            pend, self._pending = self._pending, None
        if pend is not None:
            self.show_(pend, 6); self._bob()
        if self._hide_at and AppKit.NSDate.date().timeIntervalSince1970() > self._hide_at:
            self._hide_at = 0.0; self._fade(0.0)

    def show_(self, text, secs):
        self.lbl.setStringValue_(text); self._fade(1.0)
        self._hide_at = AppKit.NSDate.date().timeIntervalSince1970() + secs

    def _fade(self, a):
        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(0.18)
        self.bub.animator().setAlphaValue_(a)
        AppKit.NSAnimationContext.endGrouping()

    def _bob(self):
        try:
            anim = Quartz.CAKeyframeAnimation.animationWithKeyPath_("transform.scale")
            anim.setValues_([1.0, 1.1, 0.97, 1.0]); anim.setKeyTimes_([0, 0.3, 0.7, 1.0]); anim.setDuration_(0.4)
            self.imgv.layer().addAnimation_forKey_(anim, "b")
        except Exception:
            pass

    # ---- drag ----
    def dragBegan_(self, loc):
        o = self.panel.frame().origin
        self.grab = (loc.x - o.x, loc.y - o.y)

    def dragMoved_(self, loc):
        self.panel.setFrameOrigin_(NSPoint(loc.x - self.grab[0], loc.y - self.grab[1]))

    def dragEnded(self):
        self.home = self.panel.frame().origin


# ═══════════════════════════ wiring ═════════════════════════════════════════

def worker_loop(orb, shared):
    stt = STT()
    print("🪵 Ready. Tap the orb and talk.")
    while True:
        shared["go"].wait(); shared["go"].clear()
        try:
            shared["busy"] = True
            orb.status("listening…")
            audio = record_utterance()
            if audio is None:
                orb.status("didn't catch that"); continue
            command = stt(audio)
            if not command or len(command) < 2:
                orb.status("didn't catch that"); continue
            print(f"\n🎙  you: {command}")
            orb.status(f"“{command}”")
            persona = orb.persona

            # No slow LLM "quip" anymore — that was the ~4s stall before any audio, and
            # it also leaked the wrong persona. Just do the task (Moss-first makes most
            # commands near-instant) and speak ONE in-character confirmation.
            reply = run_task(persona, command, orb.status)
            print(f"🗣  {persona['label']}: {reply}")
            orb.status(reply)
            speak(reply, persona["voice"], persona["api_key"])
        except Exception as e:
            print(f"[error] {e}"); orb.status("hit a snag")
        finally:
            shared["busy"] = False


def _persona_by_name(name: str) -> dict:
    name = (name or "").strip().lower()
    return next((p for p in PERSONAS if p["name"] == name), PERSONAS[0])


def read_active_persona_name() -> str:
    """Which persona is currently selected on the Mac orb (right-click cycles it)."""
    try:
        parts = open(PERSONA_FILE).read().strip().lower().split()
        return parts[0] if parts else PERSONAS[0]["name"]
    except OSError:
        return PERSONAS[0]["name"]


def run_headless_task(command: str, speak_result: bool = True) -> dict:
    """Run ONE Mac task WITHOUT the orb GUI/mic — used when the phone voice agent
    transfers a 'do X on my Mac' request over here. Prints machine-readable markers
    (MAC_PERSONA / MAC_RESULT) so the caller can parse persona + result, and speaks
    the reply in the active Mac persona's cloned voice (the 'transfer' you hear)."""
    persona = _persona_by_name(read_active_persona_name())
    print(f"MAC_PERSONA::{persona['name']}", flush=True)
    print(f"💻 {persona['label']} on the Mac: {command}", flush=True)
    try:
        reply = run_task(persona, command, lambda s: print(f"  · {s}", flush=True))
    except Exception as e:
        reply = f"hit a snag on the Mac: {e}"
    print(f"MAC_RESULT::{reply}", flush=True)
    if speak_result:
        try:
            speak(reply, persona["voice"], persona.get("api_key"))
        except Exception as e:
            print(f"[tts] {e}", flush=True)
    return {"persona": persona["name"], "reply": reply}


def main():
    if not MINIMAX_KEY:
        print("Set MINIMAX_API_KEY in sahur-brain/.env"); return
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    prompt = os.environ.get("SAHUR_NO_AX_PROMPT") != "1"
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt})

    shared = {"go": threading.Event(), "busy": False}
    orb = Orb(shared)
    orb.install()
    threading.Thread(target=worker_loop, args=(orb, shared), daemon=True).start()
    print("🪵 Sahur is floating. (Grant Accessibility + Microphone if asked, then relaunch.)")
    AppHelper.runEventLoop()


if __name__ == "__main__":
    # Headless single-task mode (phone -> Mac handoff): `python sahur.py task <command>`.
    # No orb, no mic — just do it and speak the result. Set SAHUR_MAC_SPEAK=0 to stay silent.
    if len(sys.argv) >= 2 and sys.argv[1] == "task":
        _cmd = " ".join(sys.argv[2:]).strip()
        if not _cmd:
            print("usage: sahur.py task <command>"); sys.exit(2)
        run_headless_task(_cmd, speak_result=os.environ.get("SAHUR_MAC_SPEAK", "1") != "0")
    else:
        main()
