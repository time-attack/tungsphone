"""music.py — loop Sahur's theme on the laptop while he thinks/executes.

Played on the laptop (not the phone) on purpose: activating an audio session from
inside SpringBoard deadlocks it. Uses ffplay (ships with ffmpeg) to loop, and is
killed the instant the task finishes.
"""

from __future__ import annotations

import os
import subprocess

_DIR = os.path.dirname(__file__)
_PATH = os.path.join(_DIR, "sahur_present.mp3")


def track_for(filename: str) -> str:
    """Resolve a persona's music filename to an absolute path under sahur-brain/."""
    return os.path.join(_DIR, filename)


class Music:
    def __init__(self, path: str = _PATH, volume: int = 45):
        self.path = path
        self.volume = volume
        self.proc: subprocess.Popen | None = None

    def set_track(self, filename: str):
        """Switch the loop track (by filename in sahur-brain/) for the next start()."""
        if filename:
            self.path = track_for(filename)

    def start(self):
        if self.proc or not os.path.exists(self.path):
            return
        try:
            self.proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-loglevel", "quiet", "-loop", "0",
                 "-volume", str(self.volume), self.path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            self.proc = None

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
