"""
voiceclone.py — clone Tung Tung Tung Sahur's voice on MiniMax, print the voice_id.

Two steps per MiniMax docs:
  1) upload your audio sample (file purpose = voice_clone) -> file_id
  2) POST /v1/voice_clone with file_id + a chosen voice_id -> registers the clone

Then put the voice_id in .env as SAHUR_VOICE_ID.

Usage:
    python voiceclone.py path/to/sahur_sample.mp3 [desired_voice_id]

Notes:
  - Sample should be ~10-30s, clean, mostly one speaker.
  - voice_id must be >=8 chars, letters+digits, must start with a letter (MiniMax rule).
  - Requires MINIMAX_API_KEY (and MINIMAX_GROUP_ID for the upload endpoint).
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
KEY = os.environ.get("MINIMAX_API_KEY")
GROUP = os.environ.get("MINIMAX_GROUP_ID", "")
CLONE_MODEL = os.environ.get("MINIMAX_TTS_MODEL", "speech-02-turbo")


def upload(path: str) -> str:
    url = f"{BASE}/files/upload"
    if GROUP:
        url += f"?GroupId={GROUP}"
    with open(path, "rb") as f:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {KEY}"},
            data={"purpose": "voice_clone"},
            files={"file": (os.path.basename(path), f, "application/octet-stream")},
            timeout=120,
        )
    r.raise_for_status()
    data = r.json()
    file_id = (data.get("file") or {}).get("file_id") or data.get("file_id")
    if not file_id:
        sys.exit(f"upload failed: {data}")
    print(f"uploaded -> file_id={file_id}")
    return str(file_id)


def clone(file_id: str, voice_id: str) -> None:
    url = f"{BASE}/voice_clone"
    if GROUP:
        url += f"?GroupId={GROUP}"
    r = httpx.post(
        url,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"file_id": file_id, "voice_id": voice_id, "model": CLONE_MODEL,
              "need_noise_reduction": True, "need_volume_normalization": True},
        timeout=120,
    )
    r.raise_for_status()
    print(f"clone response: {r.json()}")


def main():
    if not KEY:
        sys.exit("Set MINIMAX_API_KEY (see .env.example).")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    voice_id = sys.argv[2] if len(sys.argv) > 2 else "tungsahur01"
    fid = upload(path)
    clone(fid, voice_id)
    print(f"\nDone. Put this in .env:\n  SAHUR_VOICE_ID={voice_id}")


if __name__ == "__main__":
    main()
