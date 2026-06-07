"""
index_girlfriend.py — pin the girlfriend ('❤️') chat into Moss so "send a message
to my girlfriend" resolves 100% of the time.

What it indexes (and NOTHING else):
  1. her conversation row on the Messages list  — the real '❤️' element (live coords)
  2. the compose / message field inside her thread
  3. the Send button inside her thread

Plus it pushes ALIAS docs: every phrasing you might use for her ("my girlfriend",
"gf", "babe", "send to my girlfriend", …) all point at the '❤️' label, and likewise
for "send". At runtime Moss ranks the alias, then maps the stored label back to the
LIVE element on screen — so the tap coordinate is always correct even if the pin moves.

SAFE: it types a single throwaway character to make the Send button render, indexes
it, then deletes it. It NEVER taps Send and NEVER sends a message.

    python index_girlfriend.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import time

os.environ.setdefault("SAHUR_VISUAL", "0")

from dotenv import load_dotenv
load_dotenv(".env")

from actions import Actions
from device import DeviceClient

try:
    from moss import DocumentInfo
except Exception as e:
    sys.exit(f"moss SDK not importable: {e}")

MESSAGES_BUNDLE = "com.apple.MobileSMS"

# Everything you might call her — all map to the '❤️' contact.
GF_ALIASES = [
    "my girlfriend", "girlfriend", "my gf", "gf", "my girl", "babe", "baby", "bae",
    "my love", "sweetheart", "honey", "my partner", "her",
    "send to my girlfriend", "text my girlfriend", "message my girlfriend",
    "my girlfriend conversation", "my girlfriend's chat", "chat with my girlfriend",
    "open my girlfriend's conversation",
]
# Ways you might say "send" once the thread is open.
SEND_ALIASES = ["send", "send it", "send message", "send the message", "send text",
                "tap send", "send imessage"]
COMPOSE_ALIASES = ["message field", "compose", "compose box", "type a message",
                   "the text box", "message input", "where i type"]


def _docs_for(aliases, label, app, cx, cy, tag):
    """Build alias DocumentInfos that all point at `label` (re-mapped to live coords
    at query time, so the stored x/y is only a hint)."""
    out = []
    for phrase in aliases:
        did = hashlib.md5(f"{tag}|{phrase}|{label}".encode()).hexdigest()
        out.append(DocumentInfo(
            id=did, text=phrase,
            metadata={"app": app, "x": str(cx), "y": str(cy), "label": label}))
    return out


def _ensure_messages_front(a, m, tries=4):
    """Launch Messages and WAIT until it's actually frontmost (kill+relaunch can race
    while the app is still terminating)."""
    for _ in range(tries):
        a.open_app("Messages", "open")
        for _ in range(8):
            time.sleep(0.4)
            if a._frontmost_bundle() == MESSAGES_BUNDLE:
                a._wait_loaded(); time.sleep(0.4)
                return True
    return a._frontmost_bundle() == MESSAGES_BUNDLE


def _push(moss, docs, tag):
    sig = "alias-" + hashlib.md5(("|".join(d.id for d in docs)).encode()).hexdigest()
    moss._run(moss._index_coro(docs, sig), timeout=40)
    print(f"   + pushed {len(docs)} alias docs ({tag})")


def main():
    m = DeviceClient()
    a = Actions(m)
    try:
        m.health()
    except Exception as e:
        sys.exit(f"device control server unreachable: {e} (run iproxy 8090 8090, start device control server)")
    if not a.moss.enabled:
        sys.exit("Moss disabled — set MOSS_PROJECT_ID/MOSS_PROJECT_KEY in .env")

    print("📼 Indexing your girlfriend's chat into Moss (read-mostly, never sends)\n")

    # 1) CLEAN START — kill + relaunch Messages so we never trust a warm/restored screen.
    print("· restarting Messages for a clean state…")
    try:
        m.kill_app(MESSAGES_BUNDLE)
    except Exception:
        pass
    time.sleep(1.5)
    if not _ensure_messages_front(a, m):
        sys.exit(f"✗ Messages didn't come to front (frontmost={a._frontmost_bundle()})")
    time.sleep(0.4)
    if a._in_thread():
        a._messages_back_to_list()
        time.sleep(0.5)

    bundle = a._frontmost_bundle() or MESSAGES_BUNDLE
    print(f"· frontmost = {bundle}")

    # 2) FIND her row on the list (the '❤️' element).
    els = a._read_elements()
    heart = next((e for e in els if (e.label or e.value or "").strip() == "❤️"), None)
    if heart is None:
        # fall back to Moss's own ranking for "my girlfriend"
        hits = a.moss.find(els, bundle, "my girlfriend", top_k=3)
        if hits:
            hx, hy = hits[0]["x"], hits[0]["y"]
            heart_label = hits[0]["label"]
            print(f"· girlfriend row via Moss: {heart_label!r} @ ({hx},{hy})")
        else:
            sys.exit("✗ couldn't find the '❤️' conversation on the Messages list — is it pinned/visible?")
    else:
        hx, hy = heart.center
        heart_label = (heart.label or heart.value or "❤️").strip()
        print(f"· girlfriend row: {heart_label!r} @ ({hx},{hy})")

    # index the real element + alias docs
    a.moss.index_blocking([heart] if heart else [], bundle)
    _push(a.moss, _docs_for(GF_ALIASES, heart_label, bundle, hx, hy, "gf"), "girlfriend")

    # 3) OPEN her thread (uses the fixed poll-based open).
    print("· opening her thread…")
    a.tap_semantic("my girlfriend")
    if not a._wait_in_thread(timeout=4.0):
        # try the explicit row coords
        m.tap(hx, hy)
        if not a._wait_in_thread(timeout=4.0):
            sys.exit("✗ couldn't open her thread to index the compose/send controls")
    print("  ✓ in her thread")

    # 4) INDEX the compose field.
    tels = a._read_elements()
    field = a._compose_field()
    if field:
        fx, fy = field.center
        flabel = (field.label or field.value or "iMessage").strip()
        print(f"· compose field: {flabel!r} @ ({fx},{fy})")
        a.moss.index_blocking([field], bundle)
        _push(a.moss, _docs_for(COMPOSE_ALIASES, flabel, bundle, fx, fy, "compose"), "compose")
    else:
        print("  ! no compose field detected (will still try send-button capture)")

    # 5) Make the SEND button render: type a throwaway char, index, then delete it.
    print("· capturing the Send button (typing a throwaway char, will delete)…")
    if field:
        m.tap(fx, fy); time.sleep(0.4)
    m.input_text("x"); time.sleep(0.6)
    sels = a._read_elements()
    send = None
    for e in sels:
        lab = (e.label or "").strip().lower()
        if lab in ("send", "send message", "send imessage", "send text"):
            send = e
            break
    if send:
        sx, sy = send.center
        slabel = (send.label or "Send").strip()
        print(f"· send button: {slabel!r} @ ({sx},{sy})")
        a.moss.index_blocking([send], bundle)
        _push(a.moss, _docs_for(SEND_ALIASES, slabel, bundle, sx, sy, "send"), "send")
    else:
        print("  ! Send button not found in tree even after typing — skipped")

    # delete the throwaway char (NEVER send) — tap the on-screen 'delete' key.
    _clear_compose(a, m)

    # 6) VERIFY on a CLEAN list screen — relaunch Messages so we're never querying from
    # inside the thread (that's what made earlier verification miss).
    print("\n🔎 verification — relaunching Messages to a clean list…")
    m.kill_app(MESSAGES_BUNDLE); time.sleep(1.5)
    _ensure_messages_front(a, m); time.sleep(0.4)
    if a._in_thread():
        a._messages_back_to_list(); time.sleep(0.5)
    vbundle = a._frontmost_bundle() or MESSAGES_BUNDLE
    vels = a._read_elements()
    ok_all = True
    for q in ("send a message to my girlfriend", "my girlfriend", "babe", "text my gf",
              "message my girl"):
        hits = a.moss.find(vels, vbundle, q, top_k=1)
        if hits:
            print(f"   {q!r:42} -> {hits[0]['label']!r} @ ({hits[0]['x']},{hits[0]['y']})  "
                  f"[{a.moss.last_source} {a.moss.last_ms:.0f}ms]")
        else:
            ok_all = False
            print(f"   {q!r:42} -> (no hit)")

    print("\n✅ done — her chat, the compose field and the Send button are pinned in Moss."
          if ok_all else
          "\n⚠️  done indexing, but some phrasings didn't resolve on the list — see above.")


def _clear_compose(a, m):
    """Empty the compose box by tapping the on-screen 'delete' key (press_key('delete')
    isn't supported by this device control server). Never sends."""
    for _ in range(4):
        after = a._compose_field()
        val = ((after.value or after.label or "") if after else "").strip().lower()
        if val in ("", "message", "imessage", "text message"):
            print("  ✓ throwaway char cleared")
            return
        els = a._read_elements(tries=1)
        dkey = next((e for e in els if (e.label or "").strip().lower() == "delete"), None)
        if not dkey:
            break
        m.tap(*dkey.center); time.sleep(0.3)
    print("  ! couldn't confirm the char cleared — nothing was sent; clear it manually if needed")


if __name__ == "__main__":
    main()
