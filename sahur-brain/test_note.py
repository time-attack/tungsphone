"""
test_note.py — ZERO-LLM, ZERO-TOKEN check of the Notes "new note + type" flow.

This costs you NOTHING (no MiniMax/LiveKit calls) — it drives ios-mcp directly so we can SEE
exactly what the Notes screen looks like and where the taps land.

Run:
    iproxy 8090 8090            # in another terminal (so it can reach the phone)
    .venv-lk/bin/python test_note.py

Then paste the whole output back to me.
"""

from iosmcp import IOSMCP
from actions import Actions


def show(acts, label):
    try:
        els = acts._read_elements()
    except Exception as e:
        print(f"\n--- {label}: read failed ({e}) ---")
        return
    print(f"\n--- {label}: {len(els)} elements ---")
    n = 0
    for e in els:
        lab = (e.label or e.value or "").strip()
        if lab:
            print(f"   {lab[:55]!r:57} @ {e.center}")
            n += 1
            if n >= 45:
                print("   …")
                break


def main():
    m = IOSMCP()
    try:
        m.health()
        print("✅ ios-mcp reachable")
    except Exception as e:
        print(f"❌ ios-mcp NOT reachable: {e}\n   -> run `iproxy 8090 8090` in another terminal first.")
        return

    print("\n>>> kill Notes first (clean state)")
    try:
        m.kill_app("com.apple.mobilenotes")
    except Exception as e:
        print(f"   (kill skipped: {e})")

    print(">>> open Notes")
    a = Actions(m)
    print("   ", a.open_app("Notes", "open"))
    a._wait_loaded()
    show(a, "Notes just opened (is there a 'New note' / compose button here?)")

    print("\n>>> tap_semantic('new note')")
    print("   ->", a.tap_semantic("new note"))
    show(a, "after tap 'new note' (did an empty editable note open?)")

    print("\n>>> type 'hello from sahur'")
    print("   ->", a.type_text("hello from sahur"))
    show(a, "after typing (does the text appear in the note?)")


if __name__ == "__main__":
    main()
