"""
deeplinks.py — curated fast-path URL schemes per app.

The hybrid action policy: before driving the UI by hand, try to satisfy an intent
with a single deep link. These are instant and demo-reliable. If nothing here
fits, the agent falls back to the screenshot/accessibility -> tap loop.

Each app maps a set of "intent" verbs to a function that builds a URL from a free
text argument (e.g. a query or playlist name). Functions return None when they
can't express the intent, signalling fallback to the UI loop.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from dataclasses import dataclass
from typing import Callable


def _q(s: str) -> str:
    return urllib.parse.quote_plus(s.strip())


# Apple Calendar's calshow: scheme takes seconds since the 2001-01-01 reference
# date and opens that day. We target NOON so a timezone offset can't shift the day.
_CAL_REF = datetime(2001, 1, 1)
_CAL_FMTS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d %Y", "%B %d, %Y",
             "%b %d %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %d", "%b %d")


def _cal_date_url(a: str) -> str | None:
    """Build calshow:<seconds> from a free-text date like 'March 16, 2020' or
    '2020-03-16'. Returns None if it can't parse (caller falls back to tapping)."""
    s = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", (a or "").strip(), flags=re.I)
    if not s:
        return None
    for f in _CAL_FMTS:
        try:
            dt = datetime.strptime(s, f)
            if "%Y" not in f and "%y" not in f:      # no year given -> assume current
                dt = dt.replace(year=datetime(2001, 1, 1).today().year)
            secs = int((dt.replace(hour=12) - _CAL_REF).total_seconds())
            return f"calshow:{secs}"
        except ValueError:
            continue
    return None


@dataclass
class App:
    name: str
    bundle_id: str
    # intent verb -> builder(arg) -> url | None
    intents: dict[str, Callable[[str], str | None]]
    aliases: tuple[str, ...] = ()


APPS: list[App] = [
    App(
        name="Instagram",
        bundle_id="com.burbn.instagram",
        aliases=("insta", "ig"),
        intents={
            "open": lambda a: "instagram://app",
            "user": lambda a: f"instagram://user?username={_q(a)}",
            "tag": lambda a: f"instagram://tag?name={_q(a)}",
            # no reliable search-query deep link -> launch + agent loop handles search
            "search": lambda a: None,
        },
    ),
    App(
        name="TikTok",
        bundle_id="com.zhiliaoapp.musically",
        intents={
            "open": lambda a: "tiktok://",
            "search": lambda a: None,  # launch + agent loop
        },
    ),
    App(
        name="Spotify",
        bundle_id="com.spotify.client",
        aliases=("music",),
        intents={
            # Spotify deep links: spotify:search:<q>, or open a known URI directly.
            "search": lambda a: f"spotify:search:{_q(a)}",
            "play": lambda a: f"spotify:search:{_q(a)}",  # opens search; agent taps play, or pass a known URI
            "open": lambda a: "spotify:",
            "uri": lambda a: a if a.startswith("spotify:") else None,
        },
    ),
    App(
        name="Apple Music",
        bundle_id="com.apple.Music",
        intents={
            "search": lambda a: f"music://music.apple.com/search?term={_q(a)}",
            "play": lambda a: f"music://music.apple.com/search?term={_q(a)}",
            "open": lambda a: "music://",
        },
    ),
    App(
        name="YouTube",
        bundle_id="com.google.ios.youtube",
        intents={
            "search": lambda a: f"youtube://results?search_query={_q(a)}",
            "play": lambda a: f"youtube://results?search_query={_q(a)}",
            "open": lambda a: "youtube://",
        },
    ),
    App(
        name="Apple Maps",
        bundle_id="com.apple.Maps",
        aliases=("maps", "directions", "navigate"),
        intents={
            "search": lambda a: f"http://maps.apple.com/?q={_q(a)}",
            "directions": lambda a: f"http://maps.apple.com/?daddr={_q(a)}&dirflg=d",
            "navigate": lambda a: f"http://maps.apple.com/?daddr={_q(a)}&dirflg=d",
            "open": lambda a: "maps://",
        },
    ),
    App(
        name="Google Maps",
        bundle_id="com.google.Maps",
        intents={
            "search": lambda a: f"comgooglemaps://?q={_q(a)}",
            "directions": lambda a: f"comgooglemaps://?daddr={_q(a)}&directionsmode=driving",
            "navigate": lambda a: f"comgooglemaps://?daddr={_q(a)}&directionsmode=driving",
        },
    ),
    App(
        name="Messages",
        bundle_id="com.apple.MobileSMS",
        aliases=("imessage", "text", "sms"),
        intents={
            # sms:&body= opens a new message with body prefilled
            "text": lambda a: f"sms:&body={_q(a)}",
            "open": lambda a: "sms:",
        },
    ),
    App(
        name="Phone",
        bundle_id="com.apple.mobilephone",
        aliases=("call", "dial"),
        intents={
            "call": lambda a: f"tel://{urllib.parse.quote(a.strip())}",
            "dial": lambda a: f"tel://{urllib.parse.quote(a.strip())}",
        },
    ),
    App(
        name="Safari",
        bundle_id="com.apple.mobilesafari",
        aliases=("browser", "web"),
        intents={
            "search": lambda a: f"https://www.google.com/search?q={_q(a)}",
            "open": lambda a: a if a.startswith("http") else f"https://{a.strip()}",
        },
    ),
    App(
        name="Settings",
        bundle_id="com.apple.Preferences",
        intents={
            # A few useful prefs roots; the agent's UI loop handles the rest.
            "wifi": lambda a: "prefs:root=WIFI",
            "bluetooth": lambda a: "prefs:root=Bluetooth",
            "open": lambda a: "prefs:root",
        },
    ),
    App(
        name="Mail",
        bundle_id="com.apple.mobilemail",
        intents={"compose": lambda a: f"mailto:?body={_q(a)}", "open": lambda a: "message://"},
    ),
    App(
        name="Calendar",
        bundle_id="com.apple.mobilecal",
        aliases=("cal",),
        intents={
            # jump straight to a date (e.g. "March 16, 2020"); else launch + tap
            "date": _cal_date_url,
            "goto": _cal_date_url,
            "show": _cal_date_url,
            "open": lambda a: None,
        },
    ),
    App(
        name="Reminders",
        bundle_id="com.apple.reminders",
        aliases=("reminder", "todo", "todos"),
        intents={"open": lambda a: None},
    ),
    App(
        name="Notes",
        bundle_id="com.apple.mobilenotes",
        aliases=("note",),
        intents={"open": lambda a: None},
    ),
    App(
        name="Clock",
        bundle_id="com.apple.mobiletimer",
        aliases=("timer", "alarm", "stopwatch"),
        intents={"open": lambda a: None},
    ),
    App(
        name="Camera",
        bundle_id="com.apple.camera",
        aliases=("selfie", "photo", "picture"),
        intents={"open": lambda a: None},
    ),
    App(
        name="Photos",
        bundle_id="com.apple.mobileslideshow",
        aliases=("photo library", "gallery"),
        intents={"open": lambda a: None},
    ),
    App(
        name="FaceTime",
        bundle_id="com.apple.facetime",
        aliases=("face time", "video call"),
        intents={"open": lambda a: None},
    ),
]


_BY_KEY: dict[str, App] = {}
for _app in APPS:
    _BY_KEY[_app.name.lower()] = _app
    for _al in _app.aliases:
        _BY_KEY[_al.lower()] = _app


def find_app(name: str) -> App | None:
    if not name:
        return None
    key = name.strip().lower()
    if key in _BY_KEY:
        return _BY_KEY[key]
    # loose contains match (e.g. "spotify app")
    for k, app in _BY_KEY.items():
        if k in key or key in k:
            return app
    return None


def resolve(app_name: str, intent: str, arg: str = "") -> tuple[str | None, str | None]:
    """Return (deeplink_url, bundle_id).

    deeplink_url is None when no fast path fits -> caller should fall back to the
    UI agent loop after launching bundle_id (if known)."""
    app = find_app(app_name)
    if not app:
        return None, None
    builder = app.intents.get((intent or "").strip().lower())
    if builder is None:
        # default to a generic verb if present
        builder = app.intents.get("search") or app.intents.get("open")
    url = builder(arg) if builder else None
    return url, app.bundle_id


def catalog() -> str:
    """Human/LLM-readable list of known apps + intents (for the system prompt)."""
    lines = []
    for app in APPS:
        verbs = ", ".join(sorted(app.intents))
        lines.append(f"- {app.name} ({app.bundle_id}): {verbs}")
    return "\n".join(lines)
