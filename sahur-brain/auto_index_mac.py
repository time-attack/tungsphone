"""
auto_index_mac.py — READ-ONLY Mac indexer. Builds a Moss index of your Mac's apps
so the desktop agent (sahur.py) has ~no latency.

What it captures, for whatever app is frontmost (switch apps and it follows you):
  * the MENU BAR — every command and its keyboard shortcut (File ▸ Save  ⌘S, …).
    This is the "find shortcuts and functions to call" part: the agent can then
    invoke a feature by pressing its shortcut instead of hunting for a button.
  * the WINDOW — the clickable on-screen elements (buttons, fields, rows).

100% READ-ONLY by design: it reads the Accessibility (AX) tree only. Reading menus
via AX does NOT open or click them — nothing on your desktop is ever activated. So
it is safe to leave running unattended.

    python auto_index_mac.py             # follow the frontmost app, index forever
    python auto_index_mac.py --apps Safari,Notes,Mail   # cycle + index these, then stop
    python auto_index_mac.py --once      # index just the current frontmost app

Needs Accessibility permission for your terminal (System Settings → Privacy &
Security → Accessibility), pyobjc (installed by scripts/run-mac.sh), and Moss creds.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv(".env")

import AppKit
from ApplicationServices import (
    AXIsProcessTrustedWithOptions, AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication, AXValueGetValue, kAXChildrenAttribute,
    kAXPositionAttribute, kAXRoleAttribute, kAXSizeAttribute, kAXTitleAttribute,
    kAXValueAttribute, kAXValueTypeCGPoint, kAXValueTypeCGSize,
    kAXTrustedCheckOptionPrompt,
)

from moss_ui import MossUI


# ---- AX helpers (read-only) -------------------------------------------------

def _a(el, attr):
    try:
        err, val = AXUIElementCopyAttributeValue(el, attr, None)
        return val if err == 0 else None
    except Exception:
        return None


def _s(el, attr) -> str:
    v = _a(el, attr)
    return v if isinstance(v, str) else ""


def _children(el):
    return _a(el, kAXChildrenAttribute) or []


def _center(el):
    p = _a(el, kAXPositionAttribute)
    s = _a(el, kAXSizeAttribute)
    if not p or not s:
        return (0, 0)
    try:
        ok1, pt = AXValueGetValue(p, kAXValueTypeCGPoint, None)
        ok2, sz = AXValueGetValue(s, kAXValueTypeCGSize, None)
        if ok1 and ok2:
            return (int(pt.x + sz.width / 2), int(pt.y + sz.height / 2))
    except Exception:
        pass
    return (0, 0)


# A Moss-friendly element shim (matches what moss_ui expects: label/value/identifier/role/center)
class El:
    __slots__ = ("label", "value", "identifier", "role", "center")

    def __init__(self, label="", value="", role="", center=(0, 0)):
        self.label = label
        self.value = value
        self.identifier = ""
        self.role = role
        self.center = center


# ---- menu-bar enumeration (the "functions to call") -------------------------

_MOD = [(1 << 0, "⇧"), (1 << 1, "⌃"), (1 << 2, "⌥"), (1 << 3, "⌘")]
# AX bitmask is actually: ⌘ is implicit (modifiers==0 => ⌘); flags add ⇧⌃⌥ etc.


def _shortcut(item) -> str:
    ch = _s(item, "AXMenuItemCmdChar")
    if not ch:
        return ""
    mods = _a(item, "AXMenuItemCmdModifiers")
    out = ""
    try:
        mods = int(mods) if mods is not None else 0
    except Exception:
        mods = 0
    # In AppKit, modifiers==0 means ⌘ only; bit 3 set means ⌘ omitted, etc.
    out += "⌃" if mods & 0b0010 else ""
    out += "⌥" if mods & 0b0100 else ""
    out += "⇧" if mods & 0b0001 else ""
    out += "" if (mods & 0b1000) else "⌘"
    return out + ch.upper()


def _walk_menu(menu, path, into, sink, depth=0):
    for item in _children(menu):
        title = _s(item, kAXTitleAttribute)
        if title:
            sc = _shortcut(item)
            label = " ▸ ".join(path + [title]) + (f"  {sc}" if sc else "")
            sink.append(El(label=label, value=sc, role="menu command"))
        # recurse into submenus (still read-only)
        if depth < 4:
            for sub in _children(item):
                _walk_menu(sub, path + [title] if title else path, into, sink, depth + 1)


def menu_commands(pid) -> list[El]:
    app_el = AXUIElementCreateApplication(pid)
    menubar = _a(app_el, "AXMenuBar")
    if not menubar:
        return []
    out: list[El] = []
    for top in _children(menubar):
        name = _s(top, kAXTitleAttribute)
        for sub in _children(top):          # the AXMenu under each top item
            _walk_menu(sub, [name] if name else [], None, out)
    return out


# ---- window elements --------------------------------------------------------

_INTERACTIVE_ROLES = {
    "AXButton", "AXMenuItem", "AXMenuButton", "AXLink", "AXTextField",
    "AXTextArea", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXCell",
    "AXRow", "AXTab", "AXSlider", "AXStaticText",
}


def window_elements(pid, max_n=80) -> list[El]:
    app_el = AXUIElementCreateApplication(pid)
    out: list[El] = []

    def rec(el, depth=0):
        if len(out) >= max_n or depth > 14:
            return
        role = _s(el, kAXRoleAttribute)
        title = _s(el, kAXTitleAttribute) or _s(el, kAXValueAttribute)
        if role in _INTERACTIVE_ROLES and title and len(title.strip()) > 1:
            out.append(El(label=title.strip(), role=role.replace("AX", "").lower(),
                          center=_center(el)))
        for ch in _children(el):
            rec(ch, depth + 1)

    for w in (_a(app_el, "AXWindows") or []):
        rec(w)
    return out


# ---- driver -----------------------------------------------------------------

def _frontmost():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app:
        return None, None, None
    return app.processIdentifier(), (app.bundleIdentifier() or app.localizedName()), app.localizedName()


def index_frontmost(moss: MossUI, seen: set) -> int:
    pid, bundle, name = _frontmost()
    if not pid or not bundle:
        return 0
    els = menu_commands(pid) + window_elements(pid)
    els = [e for e in els if (e.label or e.value)]
    if not els:
        return 0
    n = moss.index_blocking(els, bundle)
    if n > 0:
        print(f"  + {name}: indexed {n} (menu+window)")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", help="comma-separated app names to activate + index, then stop")
    ap.add_argument("--once", action="store_true", help="index only the current frontmost app")
    ap.add_argument("--minutes", type=float, default=0.0, help="stop after N minutes (0 = forever)")
    args = ap.parse_args()

    if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
        sys.exit("Grant Accessibility to your terminal (System Settings → Privacy & "
                 "Security → Accessibility), then run again.")
    moss = MossUI()
    if not moss.enabled:
        sys.exit("Moss disabled — set MOSS_PROJECT_ID/MOSS_PROJECT_KEY in .env")
    moss.warm()
    seen: set = set()
    total = 0

    if args.apps:
        ws = AppKit.NSWorkspace.sharedWorkspace()
        for nm in [s.strip() for s in args.apps.split(",")]:
            print(f"== {nm} ==")
            ws.launchApplication_(nm)
            time.sleep(2.5)
            total += index_frontmost(moss, seen)
            time.sleep(0.5)
        print(f"\n✅ done — {total} elements indexed into Moss '{moss.index}'.")
        return

    if args.once:
        total += index_frontmost(moss, seen)
        print(f"✅ {total} elements indexed.")
        return

    print("📼 Mac index — switch between apps; each app's menus + window get indexed.")
    print("   READ-ONLY (never clicks). Ctrl-C to stop.\n")
    deadline = time.time() + args.minutes * 60 if args.minutes else None
    try:
        while True:
            total += index_frontmost(moss, seen)
            if deadline and time.time() > deadline:
                break
            time.sleep(1.5)
    except KeyboardInterrupt:
        pass
    print(f"\n✅ stopped — {total} elements indexed into Moss '{moss.index}'.")


if __name__ == "__main__":
    main()
