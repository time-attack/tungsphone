"""
actions.py — the device action layer the LLM drives.

Wraps DeviceClient into a small set of high-level actions and exposes them as both:
  - plain Python methods (used by control_proof.py)
  - OpenAI-style tool JSON schemas (used by control_proof.py and the LiveKit agent)

Keeping the action surface small and high-level keeps the model on rails and the
demo reliable.
"""

from __future__ import annotations

import json
import time
from typing import Any

import deeplinks
from device import DeviceClient, UIElement
from moss_ui import MossUI

# Max interactive elements we surface to the model per read (keeps tokens sane +
# the model fast — big screen dumps balloon the reasoning context).
MAX_ELEMENTS_TO_MODEL = 22

# Where grounding telemetry (Moss-index hit vs local fallback) is appended.
import os as _os
_METRICS_FILE = _os.environ.get(
    "SAHUR_METRICS_FILE", _os.path.join(_os.path.dirname(__file__), "sahur_metrics.jsonl"))

# Rows that are NOT real content items — trays, section headers, tab bar, chrome,
# media-preview. The "first/latest/first conversation" selector skips these so it
# never opens e.g. Instagram's Notes tray (which auto-plays a friend's song).
_ROW_EXCL_SUB = (
    "new message", "new chat", "compose", "create", "notes tray", "stories tray",
    "story tray", "add song", "add a song", "play audio", "audio preview",
    "link spotify", "meta ai", "leave a note", "your note", "add note",
    "quick reply", "suggested for you", "create a post", "story or",
)
_ROW_EXCL_EXACT = {
    "messages", "requests", "search", "notes", "active now", "suggested",
    "explore", "home", "reels", "profile", "main feed", "for you", "following",
    "notifications", "stories tray", "your story",
    # transient banners / prompts
    "dismiss", "not now", "allow", "continue", "skip", "maybe later", "got it",
    "no thanks", "ok", "okay", "cancel", "close", "turn on", "turn on notifications",
}

# hints that a list row is an actual conversation/chat cell (name + activity)
_CONV_HINT = ("active", "ago", "new message", "new messages", "sent ", "unread",
              "liked", "typing", "tap to", "·", "delivered", "seen")

# The device/output picker. A "play"/"shuffle" target must NEVER resolve to one of
# these: Moss (and a naive substring rank) score "Bluetooth and AirPlay" high for
# "play" because "air·play" contains "play" — tapping it opens the Connect-to-a-device
# sheet instead of playing anything. Drop these for primary-media actions so a real
# Play control (or an honest "nothing to play") wins instead.
_DEVICE_TRAP_SUB = (
    "airplay", "bluetooth", "connect to a device", "connect to device",
    "connect device", "devices", "speaker", "chromecast", "cast to",
)


def _is_device_trap(label) -> bool:
    low = (label or "").strip().lower()
    return bool(low) and any(s in low for s in _DEVICE_TRAP_SUB)


class Actions:
    def __init__(self, mcp: DeviceClient):
        self.mcp = mcp
        self.moss = MossUI()
        self._screen: tuple[int, int] | None = None

    def _frontmost_bundle(self) -> str:
        try:
            f = self.mcp.frontmost_app()
            return f.get("bundleId") or f.get("bundle_id") or f.get("name") or "unknown"
        except Exception:
            return "unknown"

    def _focus_search_field(self) -> bool:
        """Tap the on-screen search/text FIELD so typing actually lands (tapping a
        'Search' tab opens a browse page whose field isn't focused yet), then clear
        any leftover text so a new query replaces (not appends to) the old one."""
        els = self._read_elements(tries=3)
        for kw in ("what do you want", "search with", "ask anything", "search", "type a", "message"):
            for e in els:
                lab = (e.label or e.value or "").lower()
                if kw in lab and e.center[1] < 400:   # field is near the top
                    self.mcp.tap(*e.center)
                    time.sleep(0.4)
                    self._clear_search_field()
                    return True
        return False

    def _focus_input_field(self) -> bool:
        """Focus a text-entry field so typing lands. Handles a TOP search field (TikTok,
        Spotify, Safari) AND a BOTTOM compose field (Messages/WhatsApp/IG DM). The old
        _focus_search_field only looked above y=400, so message boxes were never focused —
        that's why the link never got typed."""
        if self._focus_search_field():
            return True
        field = self._compose_field()
        if field:
            self.mcp.tap(*field.center); time.sleep(0.35)
            return True
        return False

    def _clear_search_field(self) -> bool:
        """Tap a Clear/✕ button (or backspace-clear) so the field is empty before we
        type — apps often restore the previous query when reopened."""
        els = self._read_elements(tries=1)
        for e in els:
            lab = (e.label or e.value or "").strip().lower()
            if lab in ("clear text", "clear", "clear search", "✕", "x", "close") and e.center[1] < 200:
                self.mcp.tap(*e.center)
                time.sleep(0.25)
                return True
        return False

    def _brief(self, els: list[UIElement], n: int = 14) -> str:
        """Compact one-line list of the top tappable elements — appended to action
        results so the model can chain taps WITHOUT a separate read_screen call."""
        inter = [e for e in els if _is_interactive(e)] or els
        parts = []
        for e in inter[:n]:
            label = (e.label or e.value or "").strip()
            if label:
                cx, cy = e.center
                parts.append(f"{label[:26]}@({cx},{cy})")
        return ("on screen: " + " | ".join(parts)) if parts else "on screen: (no labeled elements)"

    def _read_elements(self, tries: int = 6, delay: float = 0.35) -> list[UIElement]:
        """Read UI elements, retrying while device control server's accessibility runtime warms
        up after an app launch (it reports 'axRuntimeMode: inactive' for ~1-3s)."""
        last = None
        for _ in range(tries):
            try:
                els = self.mcp.ui_elements()
            except Exception as e:
                last = e
                els = None
            if els:
                return els
            time.sleep(delay)
        if last:
            raise last
        return []

    # ---- helpers -----------------------------------------------------------

    def _screen_size(self) -> tuple[int, int]:
        if self._screen is None:
            try:
                info = self.mcp.screen_info()
                w = int(info.get("width") or info.get("Width") or 0)
                h = int(info.get("height") or info.get("Height") or 0)
                self._screen = (w, h) if w and h else (0, 0)
            except Exception:
                self._screen = (0, 0)
        return self._screen

    # ---- actions (return short strings the model can read) -----------------

    def _launch_verified(self, bundle_id: str) -> str:
        """Launch and confirm it's frontmost. device control server's immediate 'frontmost still ...'
        error is often spurious (the app just hadn't switched yet), so ignore it and
        poll frontmost instead, with one retry."""
        for _ in range(2):
            try:
                self.mcp.launch_app(bundle_id)
            except Exception:
                pass
            for _ in range(6):
                time.sleep(0.4)
                if self._frontmost_bundle() == bundle_id:
                    try:
                        return f"opened {bundle_id}. " + self._brief(self._read_elements(tries=3))
                    except Exception:
                        return f"opened {bundle_id}"
        return f"could not bring {bundle_id} to front (frontmost={self._frontmost_bundle()}); is it installed?"

    def open_app(self, app: str, intent: str = "open", arg: str = "") -> str:
        """Open an app. For a specific intent with a deep link (search/play/...), use the
        deep link; otherwise launch by bundle id and verify it reached the foreground."""
        url, bundle = deeplinks.resolve(app, intent, arg)
        # Only deep-link for genuinely URL-native actions; for search/play/open we
        # launch the app and let Sahur TAP through it (so the user sees him work).
        url_native = {"directions", "navigate", "call", "dial", "text", "compose",
                      "date", "goto", "show"}
        if url and (intent or "").strip().lower() in url_native:
            self.mcp.open_url(url)
            time.sleep(1.5)
            return f"opened {app} via deep link: {url}"
        if bundle:
            return self._launch_verified(bundle)
        if url:
            self.mcp.open_url(url)
            time.sleep(1.0)
            return f"opened url {url}"
        return f"unknown app '{app}'. Use launch_app with a bundle id."

    def launch_app(self, bundle_id: str) -> str:
        return self._launch_verified(bundle_id)

    def open_url(self, url: str) -> str:
        self.mcp.open_url(url)
        return f"opened url {url}"

    def read_screen(self) -> str:
        """Return a compact, numbered list of interactive elements on screen."""
        front = ""
        try:
            f = self.mcp.frontmost_app()
            front = f.get("bundleId") or f.get("bundle_id") or f.get("name") or ""
        except Exception:
            pass
        els = self._read_elements()
        interactive = [e for e in els if _is_interactive(e)] or els
        interactive = interactive[:MAX_ELEMENTS_TO_MODEL]
        head = f"frontmost: {front}\n" if front else ""
        if not interactive:
            return head + "no readable UI elements (accessibility may be off for this app)."
        lines = [f"{i}. {e.describe()}" for i, e in enumerate(interactive)]
        return head + "\n".join(lines)

    def tap(self, x: int, y: int) -> str:
        self.mcp.tap(int(x), int(y))
        return f"tapped ({int(x)},{int(y)})"

    def tap_label(self, label: str) -> str:
        """Find a visible element by (fuzzy) label and tap its center."""
        els = self._read_elements()
        target = _best_label_match(els, label)
        if not target:
            return f"no element matching '{label}'. Call read_screen and tap by coordinates."
        cx, cy = target.center
        self.mcp.tap(cx, cy)
        return f"tapped '{target.label or target.identifier}' @({cx},{cy})"

    def _wait_loaded(self, min_els: int = 5, timeout: float = 6.0) -> list:
        """Block until the current app's UI has actually rendered (a cold launch
        reports 'axRuntimeMode: inactive' for 1-3s and returns ~1 element). Poll
        until enough elements/interactives appear, so the first tap doesn't miss."""
        t0 = time.time()
        els: list = []
        while time.time() - t0 < timeout:
            try:
                els = self._read_elements(tries=1)
            except Exception:
                els = []     # AX runtime not ready yet on a cold launch — keep polling
            inter = [e for e in els if _is_interactive(e)]
            if len(els) >= min_els or len(inter) >= 3:
                return els
            time.sleep(0.4)
        return els

    def _log_metric(self, target, app, changed, label=None, score=None):
        """Append one grounding event so we can see Moss-index hits vs local fallback."""
        try:
            import json as _json
            rec = {
                "t": round(time.time(), 1), "app": app, "target": target,
                "source": getattr(self.moss, "last_source", "?"),
                "ms": round(getattr(self.moss, "last_ms", 0.0), 1),
                "label": label, "score": round(score, 3) if isinstance(score, (int, float)) else None,
                "changed": bool(changed),
            }
            with open(_METRICS_FILE, "a") as f:
                f.write(_json.dumps(rec) + "\n")
            tag = {"moss": "⚡moss", "local": "·local", "none": "✗none"}.get(rec["source"], rec["source"])
            print(f"  [{tag} {rec['ms']}ms] '{target}' -> {label!r} ({rec['score']}) {'✓' if changed else '✗'}")
        except Exception:
            pass

    def tap_semantic(self, target: str) -> str:
        """Semantic tap with verification: Moss finds the best matches for `target`;
        tap the top one and confirm the screen changed. If it didn't (dead spot /
        wrong element), try the next candidate. Reports whether it worked so the
        model never re-taps a spot that does nothing."""
        app = self._frontmost_bundle()
        els = self._read_elements()
        before = _screen_sig(els)

        # "first/latest/top" -> the topmost CONTENT row (a list item with real text,
        # below the header bar), NOT a top icon button like "New message"/"Search".
        low = target.lower()
        if any(w in low for w in ("first", "latest", "top result", "newest", "most recent")):
            def _content_row(e):
                lab = (e.label or e.value or "").strip()
                ll = lab.lower()
                return (e.center[1] > 150 and _is_interactive(e) and len(lab) > 6
                        and ll not in _ROW_EXCL_EXACT
                        and not _is_dead_label(lab) and not _is_sponsored_label(lab)
                        and not any(b in ll for b in _ROW_EXCL_SUB))
            rows = [e for e in els if _content_row(e)]
            # for "first conversation/message/chat", prefer rows that look like a chat
            # cell (a name + activity), so we skip banners and pick a real thread.
            if any(w in low for w in ("conversation", "message", "chat", " dm", "inbox", "thread")):
                conv = [e for e in rows
                        if any(h in (e.label or e.value or "").lower() for h in _CONV_HINT)]
                if conv:
                    rows = conv
            rows.sort(key=lambda e: e.center[1])
            if rows:
                c = rows[0]; cx, cy = c.center
                self.mcp.tap(cx, cy)
                time.sleep(0.45)
                after = self._read_elements()
                if _screen_sig(after) != before:
                    return f"opened first item {c.label!r} — changed ✓. " + self._brief(after)

        # PRIMARY ACTION ("play"/"shuffle"/...): several elements can share the exact
        # label (the green hero button AND the bottom now-playing/transport button).
        # Geometry — not tree order — decides which is the real one. Tap hero-first and
        # require the screen to actually change; fall through to the next only if it
        # didn't. This is the fix for "play resumed my current media / faked the tap".
        if _is_primary_action(target):
            _w, _h = self._screen_size()
            prim = _primary_candidates(els, target, _h)
            if prim:
                for c in prim:
                    cx, cy = c.center
                    self.mcp.tap(cx, cy)
                    time.sleep(0.5)
                    after = self._read_elements()
                    if _screen_sig(after) != before:
                        self._log_metric(target, app, changed=True,
                                         label=c.label or c.value, score=3.0)
                        return (f"tapped {(c.label or c.value)!r} for '{target}' "
                                f"(primary action, hero-first) ✓. " + self._brief(after))
                self._log_metric(target, app, changed=False,
                                 label=prim[0].label or prim[0].value, score=3.0)
                return (f"tapped {(prim[0].label or prim[0].value)!r} for '{target}' but the "
                        f"screen did NOT change — read_screen and choose a different target.")

        matches = self.moss.find(els, app, target, top_k=6)
        # A play/shuffle target reaching here means there was no exact hero Play button.
        # Never let it fall onto the device picker ("Bluetooth and AirPlay" et al.) —
        # tapping that opens Connect-to-a-device, not playback. Better to report nothing
        # matched (honest failure) than to fake a ✓ by opening the wrong sheet.
        if _is_primary_action(target):
            matches = [m for m in matches if not _is_device_trap(m.get("label"))]
        if not matches:
            self._log_metric(target, app, changed=False)
            return f"no element matched '{target}' on {app}. Call read_screen to see options."
        # EXACT label match is high-confidence: tap it and TRUST it. Never fall through to other
        # candidates (that's how "new note" wrongly tapped "Note Actions" and opened a grey menu).
        tl = target.strip().lower()
        exact = next((m for m in matches if (m.get("label") or "").strip().lower() == tl), None)
        if exact:
            self.mcp.tap(exact["x"], exact["y"])
            time.sleep(0.5)
            after = self._read_elements()
            self._log_metric(target, app, changed=(_screen_sig(after) != before),
                             label=exact["label"], score=exact.get("score"))
            return f"tapped {exact['label']!r} for '{target}' (exact match) ✓. " + self._brief(after)
        tried = []
        for cand in matches[:3]:
            self.mcp.tap(cand["x"], cand["y"])
            tried.append(f"{cand['label']!r}@({cand['x']},{cand['y']})")
            time.sleep(0.45)   # let the screen transition before checking it changed
            after = self._read_elements()
            if _screen_sig(after) != before:
                self._log_metric(target, app, changed=True, label=cand["label"], score=cand.get("score"))
                return (f"tapped {cand['label']!r} for '{target}' — changed ✓. " + self._brief(after))
        self._log_metric(target, app, changed=False, label=matches[0]["label"], score=matches[0].get("score"))
        return (f"tapped {', '.join(tried)} for '{target}' but the screen did NOT change — "
                f"this control may not be tappable; read_screen and choose a different target/action.")

    def do_sequence(self, steps: list, app: str = "") -> str:
        """Execute a whole planned navigation in ONE call — the model plans the path,
        we ground+tap each step locally using the Moss index (no LLM round-trip per
        step). Each step is a tap target ("direct messages", "first conversation"),
        or "type: <text>", or "swipe up"/"swipe down", or "enter".
        Stops early if a tap doesn't change the screen (so the model can recover."""
        out = []
        if app:
            out.append(self.open_app(app, "open"))
            self._wait_loaded()        # wait until the app's UI actually rendered (cold launch)
        for raw in (steps or []):
            s = str(raw).strip()
            low = s.lower()
            # redundant "open"/"open app"/"open tiktok" steps — the app is already
            # launched above. Tapping a stray "open" lands on the wrong control
            # (e.g. TikTok's LIVE button is the first element on the feed). No-op them.
            if low in ("open", "open app", "launch") or low.startswith("open ") or low.startswith("launch "):
                continue
            if low.startswith("type:") or low.startswith("type "):
                txt = s.split(":", 1)[1].strip() if ":" in s else s[5:].strip()
                self._focus_input_field()   # focus the field first — top search OR bottom compose
                out.append(self.type_text(txt)); time.sleep(0.5)
            elif low in ("send", "send message", "send it", "tap send"):
                # In a chat, Send is the ↑ arrow — NOT the return key (which inserts a newline).
                if self._tap_send_button():
                    out.append("tapped Send ✓"); time.sleep(0.7)
                else:
                    out.append("send: no Send button found")
            elif low.startswith("swipe") or low.startswith("scroll") or low.startswith("next video"):
                direction = "down" if "down" in low else ("left" if "left" in low else ("right" if "right" in low else "up"))
                out.append(self.swipe(direction)); time.sleep(0.7)
                if self._recover_from_live():       # don't get stuck in TikTok LIVE
                    out.append("(exited LIVE -> For You)")
            elif low in ("enter", "submit", "return", "press enter", "go"):
                try:
                    self.mcp.press_key("enter"); out.append("pressed enter")
                except Exception:
                    out.append("enter failed")
                time.sleep(0.6)
            else:
                r = self.tap_semantic(s)
                out.append(r)
                # sort/filter steps are best-effort: if the control isn't there, skip it
                # and keep going (still open the results) rather than aborting the plan.
                optional = any(k in low for k in
                               ("most liked", "most popular", "sort", "filter", "recent", "top"))
                if "did NOT change" in r and not optional:   # stuck -> hand back to the model
                    out.append("(stopped: last step had no effect — read the screen and continue)")
                    break
        return " || ".join(out)

    def find_videos(self, query: str, min_likes: int = 500000, count: int = 10,
                    app: str = "TikTok", max_scrolls: int = 16) -> list:
        """BATCH FIND: search <app> for <query>, then scan the results, parse each
        video's like count, and collect up to <count> videos with >= min_likes likes,
        scrolling to load more. Read-only (scan + swipe only). Returns a list of
        {rank, title, likes, x, y}. The like count lives in each result cell's label
        (e.g. '... 1324203 likes.')."""
        # cold-ish search: open -> search -> type -> enter
        self.open_app(app, "open"); self._wait_loaded()
        self.tap_semantic("search"); time.sleep(0.5)
        self._focus_search_field(); self.type_text(query); time.sleep(0.6)
        try:
            self.mcp.press_key("enter")
        except Exception:
            pass
        time.sleep(1.3)
        self.tap_semantic("videos"); time.sleep(0.8)   # the Videos tab = clean list w/ counts

        found, seen, scrolls = [], set(), 0
        while len(found) < count and scrolls <= max_scrolls:
            for e in self._read_elements():
                lab = (e.label or e.value or "").strip()
                # skip ads and any greyed/blacked-out/'null' tile (checks all node fields)
                if _is_sponsored_label(lab) or _is_dead_cell(e):
                    continue
                likes = _parse_likes(lab)
                if likes < min_likes:
                    continue
                title = _video_title(lab)
                key = (title[:30].lower(), likes)
                if key in seen:
                    continue
                seen.add(key)
                cx, cy = e.center
                found.append({"rank": len(found) + 1, "title": title, "likes": likes, "x": cx, "y": cy})
                print(f"    ✓ #{len(found)} {likes:>9,} likes — {title[:48]}")
                if len(found) >= count:
                    break
            if len(found) >= count:
                break
            self.swipe("up"); scrolls += 1; time.sleep(0.7)   # next videos = swipe up
        return found

    def search_in_app(self, app: str, query: str) -> str:
        """One-shot search: open the app, semantically find+tap its search control,
        type the query, submit. Works for any app (no hardcoded controls)."""
        steps = [self.open_app(app, "open")]
        time.sleep(2.0)
        steps.append("find search -> " + self.tap_semantic("search"))
        time.sleep(1.0)
        # Some apps need a second tap to focus the text field.
        field = self.tap_semantic("search text field")
        steps.append("focus field -> " + field)
        time.sleep(0.6)
        steps.append(self.type_text(query))
        time.sleep(0.5)
        try:
            self.mcp.press_key("enter")
            steps.append("submitted (enter)")
        except Exception as e:
            steps.append(f"enter failed: {e}")
        time.sleep(1.2)
        return " | ".join(steps)

    def type_text(self, text: str) -> str:
        self.mcp.input_text(text)
        return f"typed: {text}"

    def swipe(self, direction: str = "up", amount: float = 0.6) -> str:
        w, h = self._screen_size()
        if not (w and h):
            w, h = 390, 844  # sane default (iPhone logical pts ~ may differ; px ok too)
        cx = w // 2
        span = int(h * max(0.1, min(0.9, amount)))
        mid = h // 2
        d = direction.lower()
        if d in ("up", "down"):
            fy, ty = (mid + span // 2, mid - span // 2) if d == "up" else (mid - span // 2, mid + span // 2)
            self.mcp.swipe(cx, fy, cx, ty)
        else:  # left / right
            cy = h // 2
            span = int(w * max(0.1, min(0.9, amount)))
            fx, tx = (w // 2 + span // 2, w // 2 - span // 2) if d == "left" else (w // 2 - span // 2, w // 2 + span // 2)
            self.mcp.swipe(fx, cy, tx, cy)
        return f"swiped {d}"

    def _recover_from_live(self) -> bool:
        """TikTok feed swipes sometimes drift into a LIVE room / LIVE discovery grid
        ('Tap to watch LIVE'). Detect that and tap the 'For You' tab to get back to
        the normal scrolling feed. Returns True if it recovered. No-op elsewhere."""
        els = self._read_elements(tries=1)
        blob = " ".join((e.label or e.value or "").lower() for e in els)
        in_live = ("watch live" in blob or "tap to watch" in blob
                   or ("live" in blob and "request" in blob))
        if not in_live:
            return False
        for e in els:
            lbl = (e.label or e.value or "").strip().lower()
            if lbl == "for you" or lbl.startswith("for you"):
                x, y = e.center
                self.mcp.tap(x, y); time.sleep(0.6)
                return True
        return False

    # ---- messaging (generic compose-and-send: iMessage / WhatsApp / IG DM) ----
    # NOT per-app logic — every chat app shares the same shape: a thread has a compose
    # field pinned to the BOTTOM and a Send control beside it. The old do_sequence path
    # failed here because it focused only TOP search fields, typed AFTER tapping send, and
    # had no real send. These helpers verify each stage on the live screen so they can't
    # fake success (open the wrong thread, type into nothing, or "send" with no send).

    # The compose placeholder is labelled EXACTLY one of these (this device collapses every
    # role to 'control', so the label — not the role — is what identifies the field).
    _COMPOSE_EXACT = ("message", "imessage", "text message", "text message·sms",
                      "write a message", "type a message", "send a message", "ask anything")

    def _compose_field(self) -> UIElement | None:
        """The message INPUT field — the bottom-most text-entry element on a thread screen.
        iOS labels its placeholder 'Message'/'iMessage'/'Text Message'. Retries once because
        a single early read can miss it (the false-negative that made us bail on a real thread).
        Chat BUBBLES also carry 'iMessage'/'❤️' in their label — we exclude those ('Your
        iMessage, …' sent bubbles, and any label with a trailing ', H:MM AM/PM' timestamp)
        so a bubble is never mistaken for the compose box."""
        for attempt in range(2):
            els = self._read_elements(tries=3)
            _w, h = self._screen_size()
            h = h or 844
            # 1) EXACT placeholder label, lowest on screen — the strongest signal.
            exact = [e for e in els
                     if (e.label or e.value or "").strip().lower() in self._COMPOSE_EXACT
                     and e.center[1] > h * 0.4]
            if exact:
                exact.sort(key=lambda e: e.center[1])
                return exact[-1]
            # 2) fallback: a texty control low on screen that ISN'T a chat bubble.
            cands = []
            for e in els:
                role = (e.role or "").lower()
                lab = (e.label or e.value or "").strip().lower()
                if not lab or _is_chat_bubble(lab):
                    continue
                is_field = ("textfield" in role or "textview" in role or "field" in role
                            or "search" in role or any(hk in lab for hk in self._COMPOSE_EXACT))
                if is_field and e.center[1] > h * 0.4:   # the compose box lives low on screen
                    cands.append(e)
            if cands:
                cands.sort(key=lambda e: e.center[1])
                return cands[-1]                          # bottom-most = the compose box
            time.sleep(0.5)                               # let a lagging tree settle, then retry
        return None

    def _in_thread(self) -> bool:
        """True if we're INSIDE a conversation (a bottom compose field is present), not on
        the conversation LIST. Used to verify a thread actually opened before we type."""
        return self._compose_field() is not None

    def _wait_in_thread(self, timeout: float = 3.5) -> bool:
        """Poll for the compose field after a tap. A freshly opened thread ANIMATES in and
        the accessibility tree lags ~1-2s, so a single early read reports 'still on the list'
        — a false negative. That false negative is what made us back OUT of the thread we'd
        just correctly opened and tap around the list. Poll until the field shows up."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._in_thread():
                return True
            time.sleep(0.3)
        return False

    def _messages_back_to_list(self) -> None:
        """If Messages reopened straight into a thread, step back to the list so we can pick
        the RIGHT person. The back affordance is the top-left 'Messages'/back chevron.
        ONLY acts when we're actually in a thread — on the list the top-left control is
        'Edit', and tapping it drops us into selection mode (the 'random tapping' bug)."""
        if not self._in_thread():
            return
        els = self._read_elements(tries=2)
        for e in els:
            lab = (e.label or "").strip().lower()
            # never 'edit' — that's a LIST control, not a back affordance. The Messages back
            # button is top-left and often labelled with the unread badge ('9 unread'), not
            # the word 'Messages' — accept that too.
            if e.center[1] < 130 and e.center[0] < 160 and (
                    lab in ("messages", "back", "chats", "conversations")
                    or "unread" in lab):
                self.mcp.tap(*e.center); time.sleep(0.6)
                return

    def open_conversation(self, recipient: str, app: str = "Messages") -> tuple[bool, str]:
        """Open the named person's CHAT THREAD and verify we landed in it (a compose field
        is on screen). Tries a couple of phrasings; honest failure if the thread won't open."""
        self.open_app(app, "open"); self._wait_loaded(); time.sleep(0.5)
        if self._in_thread():
            self._messages_back_to_list()             # don't assume the last thread is the right one
        for phrase in (f"{recipient} conversation", recipient, f"chat with {recipient}"):
            self.tap_semantic(phrase)
            # POLL for the thread (don't judge on a single early read) — the tree lags the
            # open animation, and judging too soon backed us out of the correct thread.
            if self._wait_in_thread(timeout=3.5):
                return True, f"opened {recipient}'s thread"
            # genuinely still on the list — step back out (no-op if we're not in a thread)
            # and try the next phrasing.
            self._messages_back_to_list(); time.sleep(0.4)
        return False, f"couldn't open {recipient}'s conversation (stayed on the list)"

    def _tap_send_button(self) -> bool:
        """Tap the Send control (the ↑ arrow) in the bottom compose strip. Never an enter
        key (Return just inserts a newline in iMessage) and never a list row labelled 'send'.
        The arrow sits at the FAR RIGHT of the compose row, below mid-screen — among any
        'Send' elements we pick the right-most one in the lower half (geometry, not order)."""
        els = self._read_elements(tries=2)
        _w, h = self._screen_size(); h = h or 844
        sends = [e for e in els
                 if (e.label or "").strip().lower() in ("send", "send message",
                                                         "send imessage", "send text")
                 and e.center[1] > h * 0.4]          # in the compose strip, not a top control
        if sends:
            sends.sort(key=lambda e: e.center[0])     # right-most = the ↑ send arrow
            self.mcp.tap(*sends[-1].center); return True
        # fallback: any element labelled exactly 'send' (right-most wins)
        any_send = [e for e in els if (e.label or "").strip().lower() == "send"]
        if any_send:
            any_send.sort(key=lambda e: e.center[0])
            self.mcp.tap(*any_send[-1].center); return True
        return False

    def send_in_thread(self, text: str) -> tuple[bool, str]:
        """In an already-open thread: focus the compose field, type `text`, tap Send, and
        VERIFY the text left the field (became a sent bubble). Screen is the judge."""
        field = self._compose_field()
        if not field:
            return False, "no message field on screen — not in a conversation"
        self.mcp.tap(*field.center); time.sleep(0.4)
        self.type_text(text); time.sleep(0.5)
        if not self._tap_send_button():
            return False, "typed it but couldn't find the Send button"
        time.sleep(0.9)
        # VERIFY: after sending, the compose field should be empty again (text became a
        # bubble). If our text is still sitting in the field, it did NOT send.
        after = self._compose_field()
        still = ((after.value or after.label or "").strip().lower()) if after else ""
        needle = (_needle_token(text) or "").lower()
        if needle and needle in still:
            return False, "tapped Send but the text is still in the box — it didn't send"
        return True, "sent ✓"

    def press_home(self) -> str:
        self.mcp.press_home()
        return "pressed home"


# ---- element matching ------------------------------------------------------

# Markers that mean a feed cell / video is a paid AD, not organic content. We never
# open or collect these (the user got an accidental tap on a sponsored grid tile). Kept
# precise — bare "ad" is excluded so it can't match "add"/"ready"/"read".
_SPONSORED_RE = _re_sponsored = __import__("re").compile(
    r"(sponsored|promoted|paid partnership|advertisement|#ad\b|\bad\s*·|·\s*ad\b)", __import__("re").I)


def _is_sponsored_label(text: str) -> bool:
    """True if THIS element's label marks it as an ad (e.g. a 'Sponsored' grid tile)."""
    return bool(_SPONSORED_RE.search(text or ""))


def _screen_is_sponsored(els) -> bool:
    """True if the CURRENT screen (e.g. the open player) is a sponsored/ad video."""
    return any(_is_sponsored_label((e.label or e.value or "")) for e in (els or []))


# A sent/received message BUBBLE in a thread. iMessage labels these like
# "Your iMessage, <text>" / "<contact>, <text>, 8:08 AM" — they carry 'imessage' and
# timestamps, so they must NOT be mistaken for the compose field or the contact row.
_BUBBLE_RE = __import__("re").compile(
    r"(^your (?:imessage|sms|text)\b|,\s*\d{1,2}:\d{2}\s*[ap]m\b)", __import__("re").I)


def _is_chat_bubble(label: str) -> bool:
    """True if this label is a conversation message bubble, not a control."""
    return bool(_BUBBLE_RE.search(label or ""))


# A cell/video that didn't load — it renders as a greyed/blacked-out tile and the
# accessibility tree exposes the literal string "null" (or nil/nan/undefined/none),
# which can sit in the label, the VALUE, or the IDENTIFIER (not just the label). We must
# never open or collect these — skip to a real one in the grid instead.
#   - \b keeps it safe: "null" matches the word, never "annul"/"vanilla"/"banana".
#   - "(null)" / "<null>" (Obj-C NSNull printouts) are caught explicitly.
_re = __import__("re")
_DEAD_RE = _re.compile(r"(\bnull\b|\bnil\b|\bnan\b|\bundefined\b|\bnone\b|\(null\)|<null>|null null)", _re.I)
# A real TikTok result cell always carries engagement (likes/views/comments/shares) or a
# real handle/hashtag. We use this as POSITIVE proof a tile is a genuine video before we
# ever tap it — a greyed/null tile has none of this.
_ALIVE_RE = _re.compile(r"(\d[\d,.]*\s*[KMB]?\s*(likes?|views?|comments?|shares?|plays?)|@\w|#\w)", _re.I)


def _cell_text(e) -> str:
    """All text a node exposes (label + value + identifier) — 'null' can hide in any of them."""
    return " ".join(s for s in ((getattr(e, "label", "") or ""),
                                (getattr(e, "value", "") or ""),
                                (getattr(e, "identifier", "") or "")) if s).strip()


def _is_dead_label(text: str) -> bool:
    """True if this text marks a failed/'null'/blacked-out tile with no real content."""
    t = (text or "").strip()
    return len(t) <= 1 or bool(_DEAD_RE.search(t))


def _is_dead_cell(e) -> bool:
    """True if this ELEMENT is a greyed/null/failed video tile — checks label, value AND
    identifier, and treats a tile with a 'null' marker and NO real video signal as dead."""
    blob = _cell_text(e)
    if not blob or len(blob) <= 1:
        return True
    if _DEAD_RE.search(blob):
        return True                       # explicit 'null'/'nil'/'(null)' anywhere → dead
    return False


def _is_live_cell(e) -> bool:
    """POSITIVE proof a tile is a real, loaded video (engagement count or @handle/#tag).
    Used to gate which grid cell we OPEN, so a greyed tile with no signal is never tapped."""
    return bool(_ALIVE_RE.search(_cell_text(e))) and not _is_dead_cell(e)


def _screen_is_dead(els) -> bool:
    """True if the CURRENT player is a 'null'/blacked-out video (no real content loaded).
    A loaded video screen has many real labels AND a live signal (likes/handle); a dead one
    shows a 'null' marker on a near-empty screen or simply has no video signal at all."""
    blobs = [_cell_text(e) for e in (els or [])]
    real = [b for b in blobs if len(b) > 1]
    if not real:
        return True
    has_null = any(_DEAD_RE.search(b) for b in real)
    has_signal = any(_ALIVE_RE.search(b) for b in real)
    # dead if a null marker dominates a sparse screen, OR there's simply no video signal
    return (has_null and len(real) <= 4) or (not has_signal)


def _parse_likes(label: str) -> int:
    """Pull a like count out of a result-cell label. TikTok exposes it as a raw count
    ('... 1324203 likes.'); also handle '1.2M' / '500K' just in case."""
    import re
    m = re.search(r"([\d][\d,]*)\s+likes", label, re.I)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = re.search(r"([\d.]+)\s*([KMB])\b", label, re.I)
    if m:
        try:
            return int(float(m.group(1)) * {"k": 1e3, "m": 1e6, "b": 1e9}[m.group(2).lower()])
        except (ValueError, KeyError):
            pass
    return 0


def _video_title(label: str) -> str:
    """Trim a TikTok result label down to a short human title."""
    t = label.split(". Photo")[0].split(". Top liked")[0]
    return t.strip()[:70]


def _needle_token(text: str) -> str:
    """A short distinctive token from `text` (a TikTok short-code, a URL tail, or the first
    long word) — used to check on-screen whether typed text actually landed."""
    import re
    m = re.search(r"(?:vm\.)?tiktok\.com/(\w{6,})", text or "")
    if m:
        return m.group(1)
    m = re.search(r"https?://\S{8,}", text or "")
    if m:
        return m.group(0)[-10:]
    words = re.findall(r"\w{6,}", text or "")
    return words[0] if words else ""


def _screen_sig(els: list[UIElement]) -> str:
    """A cheap signature of the screen to detect whether a tap changed anything."""
    import hashlib
    parts = [f"{(e.label or e.value)[:24]}|{int(e.x)}|{int(e.y)}" for e in els[:30]]
    return hashlib.md5("\n".join(parts).encode()).hexdigest()


def _is_interactive(e: UIElement) -> bool:
    if "clickable" in e.raw:
        return bool(e.raw.get("clickable"))
    role = (e.role or "").lower()
    if any(k in role for k in ("button", "link", "textfield", "field", "switch", "cell",
                               "tab", "search", "control", "slider", "menu", "keyboard")):
        return True
    return bool(e.raw.get("hittable") or e.raw.get("enabled"))


def _best_label_match(els: list[UIElement], query: str) -> UIElement | None:
    q = query.strip().lower()
    if not q:
        return None
    exact = [e for e in els if (e.label or "").lower() == q or (e.identifier or "").lower() == q]
    if exact:
        return exact[0]
    contains = [e for e in els if q in (e.label or "").lower() or q in (e.identifier or "").lower()]
    if contains:
        # prefer the smallest matching element (usually the actual control)
        return min(contains, key=lambda e: (e.width * e.height) or 1e9)
    return None


# A "primary action" is one that has a big hero control on the current screen
# (artist/album/video page) AND a tiny duplicate in the bottom now-playing /
# transport bar. The accessibility tree gives BOTH the same label (e.g. "Play"),
# with no colour info, so we disambiguate by geometry: the hero button is large
# and lives in the content area; the transport button is small and pinned to the
# bottom strip. Generic across Spotify / Apple Music / YouTube — no app names.
_PRIMARY_ACTION_WORDS = ("play", "shuffle", "resume", "start", "watch")


def _is_primary_action(target: str) -> bool:
    t = target.strip().lower()
    if not t:
        return False
    return t in _PRIMARY_ACTION_WORDS or t.split()[0] in _PRIMARY_ACTION_WORDS


def _primary_candidates(els: list[UIElement], target: str, screen_h: int) -> list[UIElement]:
    """Elements whose label matches a primary-action target, ranked hero-first:
    NOT in the bottom transport strip first, then largest area (the green hero
    button), then higher on screen. This is what stops a 'play' tap from hitting
    the now-playing bar (which just resumes whatever was already playing)."""
    tl = target.strip().lower()
    head = tl.split()[0] if tl else ""
    cands = [e for e in els
             if (e.label or e.value or "").strip().lower() in (tl, head)]
    if not cands:
        return []
    bottom = screen_h * 0.86 if screen_h else float("inf")

    def rank(e: UIElement):
        _cx, cy = e.center
        in_transport = 1 if cy >= bottom else 0          # mini-player bar -> last
        area = (e.width or 0) * (e.height or 0)
        return (in_transport, -area, cy)                  # hero (big, content area) wins

    cands.sort(key=rank)
    return cands


# ---- OpenAI / MiniMax tool schemas -----------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open an app and optionally perform an intent via a deep link "
            "(instant). Falls back to launching the app if no deep link fits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "App name, e.g. 'Spotify', 'Maps', 'YouTube'."},
                    "intent": {"type": "string", "description": "Verb: open, search, play, directions, text, call..."},
                    "arg": {"type": "string", "description": "Query/argument, e.g. 'rock playlist' or an address."},
                },
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Launch an app by iOS bundle id (e.g. com.apple.Preferences).",
            "parameters": {
                "type": "object",
                "properties": {"bundle_id": {"type": "string"}},
                "required": ["bundle_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open any URL or URL scheme (http://, spotify:, tel:, prefs:root=...).",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_screen",
            "description": "Read the current screen as a numbered list of interactive UI elements "
            "with their coordinates. Use this to decide what to tap.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tap",
            "description": "Tap at absolute screen coordinates (from read_screen).",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tap_label",
            "description": "Tap a visible element by its label/text (exact-ish fuzzy match).",
            "parameters": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tap_semantic",
            "description": "PREFERRED way to tap. Describe the control in natural language "
            "(e.g. 'search', 'the like button', 'first result') and Moss semantically finds "
            "and taps it on the current screen. No coordinates needed.",
            "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_app",
            "description": "One-shot: open an app, find+tap its search box, type the query, submit. "
            "Use for any 'search X in <app>' request.",
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string"}, "query": {"type": "string"}},
                "required": ["app", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "do_sequence",
            "description": "FASTEST way to do multi-step tasks. When you know the path, plan it ALL "
            "here in ONE call and it executes locally (no slow per-step round-trips). "
            "Example for 'open Instagram, go to DMs, open the latest chat': "
            "app='Instagram', steps=['direct messages','first conversation']. "
            "Each step is a tap target, or 'type: <text>', or 'swipe up'/'down', or 'enter'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "App to open first (optional)."},
                    "steps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the currently focused field.",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swipe",
            "description": "Scroll/swipe the screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "number", "description": "0..1 fraction of the screen."},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_home",
            "description": "Go to the home screen.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def dispatch(actions: Actions, name: str, arguments: dict | str) -> str:
    """Execute a tool call by name. Returns a short string for the model."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            arguments = {}
    arguments = arguments or {}
    try:
        fn = {
            "open_app": lambda a: actions.open_app(a.get("app", ""), a.get("intent", "open"), a.get("arg", "")),
            "launch_app": lambda a: actions.launch_app(a.get("bundle_id", "")),
            "open_url": lambda a: actions.open_url(a.get("url", "")),
            "read_screen": lambda a: actions.read_screen(),
            "tap": lambda a: actions.tap(a.get("x", 0), a.get("y", 0)),
            "tap_label": lambda a: actions.tap_label(a.get("label", "")),
            "tap_semantic": lambda a: actions.tap_semantic(a.get("target", "")),
            "search_in_app": lambda a: actions.search_in_app(a.get("app", ""), a.get("query", "")),
            "do_sequence": lambda a: actions.do_sequence(a.get("steps", []), a.get("app", "")),
            "type_text": lambda a: actions.type_text(a.get("text", "")),
            "swipe": lambda a: actions.swipe(a.get("direction", "up"), a.get("amount", 0.6)),
            "press_home": lambda a: actions.press_home(),
        }[name]
    except KeyError:
        return f"unknown tool '{name}'"
    try:
        return fn(arguments)
    except Exception as e:  # surface device errors to the model so it can recover
        return f"error in {name}: {e}"
