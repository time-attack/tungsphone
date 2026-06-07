"""
index_messages.py — pre-ground the iOS Messages COMPOSE FLOW into Moss so "text X"
is instant and reliable next time.

Messages is excluded from both auto-crawlers on purpose (it can send), so nothing
about it was ever indexed — that's why a "text my girlfriend …" run had to guess the
fields live, landed on the conversation-list SEARCH bar, and the text never went into
a real message. This script fixes that by writing the flow's anchor controls into the
runtime UI index (MOSS_UI_INDEX, default 'sahur-ui'), tagged with the Messages bundle
id, exactly where Actions.tap_semantic() looks for them.

Why this works WITHOUT a phone attached
----------------------------------------
At runtime the index is only used to RANK the natural-language target ("message input
field", "send") against the screen. The actual tap coordinate is re-read live from the
on-screen element whose label matches (see moss_ui._map_to_current). So an indexed doc
only needs (a) rich phrasing in `text` so Moss ranks it for the planner's wording, and
(b) a `label` equal to the real on-screen label so the live element is found. Coords are
placeholders. That makes this index device-free, and robust to the UI moving around.

    python index_messages.py            # write the compose-flow anchors (no device)
    python index_messages.py --walk     # ALSO open Messages read-only and capture the
                                        #   real list + an open thread's live labels
    python index_messages.py --verify   # just run the lookup self-test

--walk needs device control server reachable (iproxy 8090) + an unlocked phone. It NEVER types and
NEVER sends — it only opens an existing thread to learn the input-field / Send labels,
then backs out.
"""

from __future__ import annotations

import argparse
import hashlib
import os

from moss_index import MossIndex

BUNDLE = "com.apple.MobileSMS"

# (canonical on-screen label, rich phrasing the planner/agent may say for it)
#
# `label` must match (==, or substring of) the REAL accessibility label on screen so
# the runtime can re-map to the live element. We list the known label variants — iOS
# uses "iMessage" for blue threads and "Text Message" for green/SMS, etc. Extra docs
# are harmless: the runtime keeps the first whose label is actually on screen.
ANCHORS = [
    # --- conversation list ---
    ("Search field", "search for a contact or a conversation; find the person to text; "
                     "search messages; look up a chat; search bar at the top of messages"),
    ("Search", "search for a contact or a conversation; find the person to text; "
               "search messages; look up a chat; search bar at the top of messages"),
    # iOS labels the compose button "Compose" (NOT "New Message"); index both spellings
    # so the natural phrasing still maps to whichever the OS exposes.
    ("Compose", "start a new message; compose a new text; begin a new conversation; "
                "new message button; compose button; write a new text; pencil compose icon"),
    ("New Message", "start a new message; compose a new text; begin a new conversation; "
                    "new message button; compose button; write a new text; pencil compose icon"),
    # back to the inbox. The control is labeled "Messages" with zero unreads, or
    # "<n> unread" when there are unreads — cover both so "go back to the list" maps.
    ("Messages", "go back to the conversation list; back to all messages; the back button; "
                 "return to the inbox; back arrow at the top left of a thread"),
    ("unread", "go back to the conversation list; back to all messages; the back button "
               "with the unread badge; return to the inbox from inside a thread"),

    # --- new-message / recipient ---
    ("To:", "recipient field; type the contact name to send to; who to text; the To field; "
            "address the message; enter the person you want to message"),
    ("To", "recipient field; type the contact name to send to; who to text; the To field; "
           "address the message; enter the person you want to message"),

    # --- the message body field (label varies by thread type) ---
    ("iMessage", "message input field; the field where you type the message; message text box; "
                 "compose field; write your message; type the message body; the text box at the "
                 "bottom; message entry field; where the message text goes"),
    ("Text Message", "message input field; the field where you type the message; message text box; "
                     "compose field; write your message; type the message body; the text box at the "
                     "bottom; message entry field; SMS text field"),
    ("Message", "message input field; the field where you type the message; compose field; "
                "write your message; type the message body; the text box at the bottom"),

    # --- send ---
    ("Send", "send the message; send it; tap send; press send; the send arrow; send button; "
             "the blue up arrow that sends the text"),
]

# Contact aliases — map how the user REFERS to someone to the label their conversation
# row actually carries on screen. The runtime re-maps to the live row by (substring of)
# this label, so "my girlfriend" taps her thread even though it's saved under an emoji.
# These are this user's personal mappings (their device data), not app chrome.
CONTACTS = [
    ("❤️", "my girlfriend; girlfriend; my gf; her; her texts; her messages; my partner; "
           "babe; my love; the pinned heart conversation at the top of messages"),
]


def build_docs() -> list[dict]:
    docs = []
    for label, phrasing in ANCHORS + CONTACTS:
        did = "msg-" + hashlib.md5(f"{label}|{phrasing[:24]}".encode()).hexdigest()[:12]
        docs.append({
            "id": did,
            # Moss ranks on this text; lead with the canonical label so an exact-name
            # query still scores it, then the phrasings for natural wording.
            "text": f"{label} — {phrasing}",
            # x/y are placeholders: the runtime re-maps to the live element by label.
            "metadata": {"app": BUNDLE, "x": "0", "y": "0", "label": label},
        })
    return docs


def write_anchors(index_name: str) -> bool:
    m = MossIndex(name=index_name)
    if not m.available():
        print("✗ No MOSS creds in sahur-brain/.env (MOSS_PROJECT_ID / MOSS_PROJECT_KEY)")
        return False
    docs = build_docs()
    ok, msg = m.add(docs)
    print(f"{'✓' if ok else '✗'} wrote {len(docs)} Messages compose-flow anchors into "
          f"Moss '{m.name}' (app={BUNDLE}) → {'OK' if ok else 'FAILED: ' + msg}")
    return ok


def walk_live(index_name: str) -> None:
    """OPTIONAL, device-only: open Messages read-only and index the real conversation
    list + one open thread, so live labels/coords are captured too. Never types/sends."""
    import sys
    import time

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sahur-brain"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "sahur-brain", ".env"))
    os.environ.setdefault("SAHUR_VISUAL", "0")
    os.environ["MOSS_UI_INDEX"] = index_name  # point the runtime sink at our index

    from actions import Actions
    from device import DeviceClient

    m = DeviceClient()
    try:
        m.health()
    except Exception as e:
        print(f"✗ device control server unreachable: {e} (run 'iproxy 8090 8090', unlock the phone) "
              f"— anchors were still written; skipping live walk.")
        return
    a = Actions(m)
    if not a.moss.enabled:
        print("✗ Moss disabled for the live walk; anchors were still written."); return

    print("\n— live walk (read-only; no typing, no sending) —")
    res = a._launch_verified(BUNDLE)
    if "opened" not in res:
        print(f"   couldn't open Messages: {res}"); return
    time.sleep(2.0)

    # 1) conversation list (search bar, compose, real thread rows)
    els = a._read_elements()
    n = a.moss.index_blocking(els, BUNDLE)
    print(f"   conversation list: indexed {n} live elements")

    # 2) open the TOP existing conversation to learn the input-field + Send labels.
    #    Opening an existing thread is read-only (it does NOT send anything).
    H = (m.screen_info() or {}).get("height", 844)
    rows = sorted(
        (e for e in els
         if e.center[1] > H * 0.18 and e.center[1] < H * 0.88
         and e.raw.get("clickable") and len((e.label or e.value or "").strip()) > 6),
        key=lambda e: e.center[1])
    if rows:
        top = rows[0]
        print(f"   opening thread {(top.label or '')[:32]!r} (read-only) to learn the input field…")
        m.tap(*top.center); time.sleep(1.4)
        if a._frontmost_bundle() == BUNDLE:
            n2 = a.moss.index_blocking(a._read_elements(), BUNDLE)
            print(f"   open thread: indexed {n2} live elements (input field + Send captured)")
        a.press_home()
    else:
        print("   no existing conversation row found to open (anchors still cover it).")
    a.press_home()


def verify(index_name: str) -> None:
    m = MossIndex(name=index_name)
    if not m.available():
        print("✗ No MOSS creds; cannot verify."); return
    print("\nverify (what the agent will say → which Messages control it resolves to):")
    for q in ["message input field", "type the message", "send the message", "send",
              "search for a contact", "start a new message", "the recipient field",
              "where do I type the text"]:
        hits = m.query(q, top_k=3)
        # show the first hit that belongs to Messages
        pick = next((h for h in hits if (h.get("metadata") or {}).get("app") == BUNDLE), None)
        if pick:
            md = pick["metadata"]
            print(f"  {q!r:30} → {md.get('label'):14} ({pick.get('score')})")
        else:
            print(f"  {q!r:30} → {hits[:1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--walk", action="store_true",
                    help="also open Messages read-only and capture live labels/coords")
    ap.add_argument("--verify", action="store_true", help="only run the lookup self-test")
    ap.add_argument("--index", default=os.environ.get("MOSS_UI_INDEX", "sahur-ui"),
                    help="Moss index the runtime queries (default: sahur-ui)")
    args = ap.parse_args()

    if args.verify:
        verify(args.index); return

    if not write_anchors(args.index):
        return
    if args.walk:
        walk_live(args.index)
    verify(args.index)
    print("\nDone. 'text my …' now resolves the compose fields from the index instead of "
          "guessing them live.")


if __name__ == "__main__":
    main()
