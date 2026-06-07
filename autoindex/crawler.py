"""
crawler.py — autonomous macOS UI explorer.

For each app it: launches + force-activates it, waits until it's actually up, reads
the app's FULL accessibility tree BY PID (works even if the app isn't perfectly
frontmost — the #1 thing that made the naive version index nothing), indexes the home
screen, then clicks through the app's primary navigation (sidebar rows, tabs, toolbar
buttons) one level deep, indexing each resulting screen. Everything streams into Moss.

SAFETY: never clicks anything whose label looks destructive (delete/send/buy/sign-out…)
OR that creates content (new/add/compose — indexed but not triggered, so no junk notes),
never types, skips toggles/sliders and the menu bar, and is time-budgeted.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time

import AppKit
import Quartz
from ApplicationServices import (
    AXUIElementCopyActionNames, AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication, AXValueGetValue, kAXChildrenAttribute,
    kAXDescriptionAttribute, kAXPositionAttribute, kAXRoleAttribute, kAXSizeAttribute,
    kAXTitleAttribute, kAXValueAttribute, kAXValueTypeCGPoint, kAXValueTypeCGSize,
    kAXWindowsAttribute,
)

_ACTIVATE = 1 << 1                      # NSApplicationActivateIgnoringOtherApps

# ───────────────────────── safety ──────────────────────────
_DANGER = ("delete", "remove", "trash", "erase", "reset", "sign out", "log out",
           "logout", "sign-out", "send", "post", "publish", "tweet", "buy", "purchase",
           "pay", "checkout", "order", "subscribe", "unsubscribe", "confirm", "quit",
           "shut down", "restart", "empty", "deauthorize", "block", "report", "unfriend",
           "unfollow", "format", "wipe", "factory", "uninstall", "destroy", "permanently",
           "leave", "decline", "reject", "discard", "move to trash", "clear")
# Indexed but NOT clicked during a crawl (they create content / start captures).
_NO_TRIGGER = ("new ", "add ", "create", "compose", "record", "start ", "make ")
_NAV_ROLES = {"AXButton", "AXMenuItem", "AXTab", "AXRow", "AXCell", "AXLink",
              "AXDisclosureTriangle", "AXPopUpButton", "AXOutlineRow", "AXStaticText"}
_CLICK_ROLES = {"AXButton", "AXTab", "AXRow", "AXCell", "AXLink",
                "AXDisclosureTriangle", "AXOutlineRow"}


def _is_safe_click(name: str, role: str) -> bool:
    low = (name or "").lower().strip()
    if not low or role not in _CLICK_ROLES:
        return False
    if any(d in low for d in _DANGER) or any(low.startswith(p.strip()) or p in low for p in _NO_TRIGGER):
        return False
    return True


# ───────────────────────── AX read/click ──────────────────────────

def _a(el, attr):
    err, v = AXUIElementCopyAttributeValue(el, attr, None)
    return v if err == 0 else None


def _s(el, attr):
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


def _label(el):
    return (_s(el, kAXTitleAttribute) or _s(el, kAXDescriptionAttribute)
            or _s(el, kAXValueAttribute))[:48]


def read_app(pid, max_n=180):
    """BFS the app's FULL accessibility tree (all windows) by pid → elements."""
    ax = AXUIElementCreateApplication(pid)
    out, q, seen = [], [(ax, 0)], 0
    while q and len(out) < max_n:
        el, d = q.pop(0)
        seen += 1
        if seen > 14000:
            break
        if d > 0:
            role = _s(el, kAXRoleAttribute)
            pos, size = _pt(el), _sz(el)
            name = _label(el)
            if pos and size and size[0] > 0 and size[1] > 0 and (name or role in _NAV_ROLES):
                err, acts = AXUIElementCopyActionNames(el, None)
                clickable = (role in _CLICK_ROLES) or (acts and "AXPress" in acts)
                out.append({"name": name, "role": role,
                            "x": int(pos[0] + size[0] / 2), "y": int(pos[1] + size[1] / 2),
                            "clickable": bool(clickable)})
        if d < 18:
            kids = _a(el, kAXChildrenAttribute) or []
            q.extend((c, d + 1) for c in kids)
    return out


def _running_app(name):
    for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if (a.localizedName() or "") == name:
            return a
    return None


def _has_window(pid):
    err, w = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(pid), kAXWindowsAttribute, None)
    return bool(err == 0 and w and len(w) > 0)


def launch(app_name, timeout=10.0):
    """Launch + force-activate the app; return its pid once it has a window (or None).

    Re-issues `open -a` if no window appears — some apps stay alive with their window
    closed (Messages, Mail…), and a second open triggers the window-reopen."""
    app = None
    for attempt in range(2):
        subprocess.run(["/usr/bin/open", "-a", app_name], check=False)
        end = time.time() + timeout
        while time.time() < end:
            app = _running_app(app_name)
            if app:
                break
            time.sleep(0.3)
        if not app:
            continue
        app.activateWithOptions_(_ACTIVATE)
        pid = app.processIdentifier()
        end2 = time.time() + (timeout if attempt == 0 else 4.0)
        while time.time() < end2:
            if _has_window(pid):
                time.sleep(0.5)
                return pid
            time.sleep(0.3)
        # no window yet — loop re-issues `open -a` to trigger a reopen
    return app.processIdentifier() if app else None


def activate(app_name):
    a = _running_app(app_name)
    if a:
        a.activateWithOptions_(_ACTIVATE)
        time.sleep(0.35)


def _click(x, y):
    p = (float(x), float(y))
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, p, 0))
    time.sleep(0.04)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, p, Quartz.kCGMouseButtonLeft))
    time.sleep(0.03)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, p, Quartz.kCGMouseButtonLeft))


def _press_escape():
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, 53, True))
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, 53, False))


def _sig(els):
    parts = sorted(f"{e['name']}/{e['role']}" for e in els if e["name"])[:45]
    return hashlib.md5("\n".join(parts).encode()).hexdigest()


# ───────────────────────── the explorer ──────────────────────────

def _make_doc(app_name, path, els):
    names = [e["name"] for e in els if e["name"]]
    doc_id = hashlib.md5(f"{app_name}|{_sig(els)}".encode()).hexdigest()[:16]
    return doc_id, names, {
        "id": doc_id,
        "text": f"{app_name} screen — reached by: {' › '.join(path) or 'launch'}. "
                f"Controls: {', '.join(names[:30])}",
        "metadata": {"kind": "screen", "app": app_name, "path": json.dumps(path),
                     "elements": json.dumps([{"name": e["name"], "role": e["role"],
                                              "x": e["x"], "y": e["y"]} for e in els[:45]])},
    }


def explore_app(app_name, moss, *, budget_s=120, max_screens=40, batch_size=15, on_status=print):
    """Launch, index the home screen, click through the primary navigation, index each."""
    t0 = time.time()
    on_status(f"  ↳ exploring {app_name}")
    pid = launch(app_name)
    if not pid:
        on_status(f"    ✗ couldn't launch {app_name}")
        return {"app": app_name, "screens": 0, "pushed": 0, "secs": round(time.time() - t0, 1)}

    seen, batch, pushed = set(), [], 0

    def index(path, els):
        nonlocal pushed
        if not els:
            return
        sig = _sig(els)
        if sig in seen:
            return
        seen.add(sig)
        doc_id, names, doc = _make_doc(app_name, path, els)
        batch.append(doc)
        on_status(f"    • [{' › '.join(path) or 'launch'}] — {len(names)} controls")
        if len(batch) >= batch_size:
            pushed += _flush(batch, moss, on_status)
            batch.clear()

    # 1) home screen (retry a couple times for apps slow to populate their AX tree)
    home = read_app(pid)
    for _ in range(3):
        if home:
            break
        activate(app_name)
        time.sleep(1.0)
        home = read_app(pid)
    if not home:
        on_status("    ✗ no readable window (is the app's window open?)")
    index([], home)

    # 2) click through the primary navigation, one level deep
    nav = [e for e in home if _is_safe_click(e["name"], e["role"])]
    on_status(f"    ({len(nav)} navigable controls to try)")
    for e in nav:
        if time.time() - t0 > budget_s or len(seen) >= max_screens:
            on_status("    (budget/screen cap reached)")
            break
        activate(app_name)
        _click(e["x"], e["y"])
        time.sleep(0.7)
        index([e["name"]], read_app(pid))
        _press_escape()                # dismiss any popover/sheet it opened
        time.sleep(0.2)

    pushed += _flush(batch, moss, on_status)
    return {"app": app_name, "screens": len(seen), "pushed": pushed, "secs": round(time.time() - t0, 1)}


def _flush(batch, moss, on_status):
    if not batch:
        return 0
    ok, msg = moss.add(list(batch))
    on_status(f"    → Moss add {len(batch)} docs: {'OK' if ok else 'FAILED — ' + msg[:90]}")
    return len(batch) if ok else 0
