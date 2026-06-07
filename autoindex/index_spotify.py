"""
index_spotify.py — fully index Spotify's actions into Moss.

Spotify is a Chromium app with a thin accessibility tree, so click-path crawling
barely works. But it ships a rich AppleScript dictionary + URL schemes — the reliable
control surface — so we index 20 real Spotify ACTIONS (intent → AppleScript/URL),
not flaky UI paths. The action is embedded in each doc so the runtime agent can copy
and run it directly. Indexing only writes to Moss; it never touches playback.
"""

from __future__ import annotations

import hashlib

from moss_index import MossIndex


def _as(body: str) -> str:
    return f'tell application "Spotify" to {body}'


# (intent phrasings for semantic match, action_type, action)
SPOTIFY = [
    ("play some music; put on music; play something; start the music; i want music; resume; unpause; play", "applescript", _as("play")),
    ("pause the music; stop the music; stop playing", "applescript", _as("pause")),
    ("toggle play or pause", "applescript", _as("playpause")),
    ("skip this song; next song; next track; skip forward", "applescript", _as("next track")),
    ("previous song; go back a track; play the last song; rewind", "applescript", _as("previous track")),
    ("restart this song; start the song over; go to the beginning", "applescript", _as("set player position to 0")),
    ("turn shuffle on; shuffle my music; enable shuffle", "applescript", _as("set shuffling to true")),
    ("turn shuffle off; stop shuffling; disable shuffle", "applescript", _as("set shuffling to false")),
    ("turn repeat on; loop this song; enable repeat", "applescript", _as("set repeating to true")),
    ("turn repeat off; stop looping; disable repeat", "applescript", _as("set repeating to false")),
    ("turn it up; volume up; make it louder; crank it", "applescript", _as("set sound volume to (sound volume + 15)")),
    ("turn it down; volume down; make it quieter; lower the volume", "applescript", _as("set sound volume to (sound volume - 15)")),
    ("mute spotify; silence the music", "applescript", _as("set sound volume to 0")),
    ("max volume; full blast; turn it all the way up", "applescript", _as("set sound volume to 100")),
    ("what's playing; what song is this; current track and artist; now playing", "applescript",
     _as('(name of current track) & " by " & (artist of current track)')),
    ("search spotify for a specific named song, artist or album; find a particular track", "url", "spotify:search:QUERY"),
    ("open my liked songs; play my liked songs; saved songs", "url", "spotify:collection:tracks"),
    ("play my discover weekly; open discover weekly", "url", "spotify:search:Discover Weekly"),
    ("open spotify; bring spotify to the front; launch spotify", "applescript", _as("activate")),
]


def build_docs():
    docs = []
    for phrases, kind, action in SPOTIFY:
        intent = phrases.split(";")[0].strip()
        how = (f"Run AppleScript: {action}" if kind == "applescript"
               else f"Open URL: {action}" + (" (replace QUERY with the search terms)" if "QUERY" in action else ""))
        docs.append({
            "id": "spotify-" + hashlib.md5(intent.encode()).hexdigest()[:12],
            "text": f"Spotify — {phrases}. {how}",
            "metadata": {"kind": "action", "app": "Spotify", "action_type": kind,
                         "action": action, "path": "[]"},
        })
    return docs


def main():
    m = MossIndex()
    if not m.available():
        print("✗ No MOSS creds in sahur-brain/.env"); return
    docs = build_docs()
    ok, msg = m.add(docs)
    print(f"Indexed {len(docs)} Spotify actions into Moss '{m.name}' → {'OK' if ok else 'FAILED: ' + msg}")
    if not ok:
        return
    print("\nverify (intent → indexed action):")
    for q in ["skip this song", "make it louder", "shuffle my music", "what song is playing",
              "play my liked songs", "search for drake on spotify", "pause the music",
              "play discover weekly", "mute it"]:
        hits = m.query(q, top_k=1)
        if hits and "error" not in hits[0]:
            md = hits[0].get("metadata", {})
            print(f"  {q!r:32} → [{md.get('action_type')}] {md.get('action','')[:48]}  ({hits[0].get('score')})")
        else:
            print(f"  {q!r:32} → {hits}")


if __name__ == "__main__":
    main()
