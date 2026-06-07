"""
token_server.py — hand SahurKit.app a LiveKit access token for the demo room.

SahurKit calls:  GET http://<laptop-ip>:8788/token?identity=phone&room=sahur
Returns JSON: {"url": <livekit-ws-url>, "token": <jwt>}

Env:
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from livekit import api

load_dotenv()

app = Flask(__name__)


@app.get("/token")
def token():
    room = request.args.get("room", "sahur")
    identity = request.args.get("identity", "phone")
    grant = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    tok = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grant)
        .to_jwt()
    )
    return jsonify({"url": os.environ["LIVEKIT_URL"], "token": tok})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("TOKEN_PORT", "8788")))
