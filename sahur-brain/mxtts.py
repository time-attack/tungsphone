"""
mxtts.py — a custom LiveKit TTS adapter for MiniMax T2A v2 with CLONED voices.

Why not the stock `livekit-plugins-minimax`? It (1) validates voice_id against a fixed
Literal of stock voices and REJECTS our cloned `moss_audio_*` ids, (2) hard-requires a
GroupId, (3) points at api.minimax.chat while our cloned voices live on api.minimax.io,
and (4) hardcodes the model. This adapter calls the SAME endpoint/payload that already
works in sahur_voice.py (`/t2a_v2`, per-persona api_key, arbitrary cloned voice_id) and
streams PCM into LiveKit for low latency.

    tts = MiniMaxTTS(api_key=..., voice_id="moss_audio_...", model="speech-02-turbo")
"""

from __future__ import annotations

import os

import aiohttp
from osc_data.text_stream import TextStreamSentencizer

from livekit.agents import APIConnectOptions, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

_DEFAULT_BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")


class MiniMaxTTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        voice_id: str,
        model: str = "speech-02-turbo",
        base_url: str | None = None,
        group_id: str | None = None,
        sample_rate: int = 32000,
        language_boost: str = "English",
        speed: float = 1.05,
        volume: float = 1.0,
        pitch: int = 0,
        http_session: aiohttp.ClientSession | None = None,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_key = api_key or os.environ.get("MINIMAX_API_KEY")
        if not self._api_key:
            raise ValueError("MiniMaxTTS: api_key (or MINIMAX_API_KEY) is required")
        self._voice_id = voice_id
        self._model = model
        self._base_url = (base_url or _DEFAULT_BASE).rstrip("/")
        self._group_id = group_id if group_id is not None else os.environ.get("MINIMAX_GROUP_ID", "")
        self._sample_rate = sample_rate
        self._language_boost = language_boost
        self._speed, self._volume, self._pitch = speed, volume, pitch
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def _url(self) -> str:
        u = f"{self._base_url}/t2a_v2"
        return u + (f"?GroupId={self._group_id}" if self._group_id else "")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _payload(self, text: str) -> dict:
        return {
            "model": self._model,
            "text": text,
            # NON-streaming: one JSON response per sentence. (Streaming/SSE returns a single
            # `data:` line bigger than aiohttp's 512KB readline limit -> LineTooLong crash.)
            "stream": False,
            "language_boost": self._language_boost,
            "voice_setting": {
                "voice_id": self._voice_id,
                "speed": self._speed,
                "vol": self._volume,
                "pitch": self._pitch,
            },
            "audio_setting": {"sample_rate": self._sample_rate, "format": "pcm"},
        }

    def synthesize(self, text, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        raise NotImplementedError("MiniMaxTTS streams; use stream()")

    def stream(self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        return _MiniMaxStream(tts=self, conn_options=conn_options, session=self._ensure_session())


class _MiniMaxStream(tts.SynthesizeStream):
    def __init__(self, *, tts: MiniMaxTTS, session: aiohttp.ClientSession,
                 conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: MiniMaxTTS = tts
        self._session = session

    async def _run(self, emitter: tts.AudioEmitter) -> None:
        # ONE output segment per stream instance. LiveKit's input side counts exactly one
        # segment per say()/LLM-reply and refuses more; if we open a segment per sentence we
        # get "number of segments mismatch: expected 1, but got N". So: start the segment
        # once, stream every sentence's audio into it, end it once.
        emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts._sample_rate,
            mime_type="audio/pcm",
            stream=True,
            num_channels=1,
        )
        emitter.start_segment(segment_id=utils.shortuuid())
        splitter = TextStreamSentencizer()
        async for token in self._input_ch:
            if isinstance(token, self._FlushSentinel):
                sentences = splitter.flush()
            else:
                sentences = splitter.push(text=token)
            for sentence in sentences:
                if sentence.strip():
                    await self._synth_into(sentence, emitter)   # synth per sentence (low latency)
        emitter.end_segment()

    async def _synth_into(self, sentence: str, emitter: tts.AudioEmitter) -> None:
        """POST one sentence to MiniMax T2A v2 and push its PCM into the open segment.
        Non-streaming: read the whole JSON ({"data": {"audio": "<hex pcm>"}}) and push once —
        avoids aiohttp's readline 512KB limit (LineTooLong) that big SSE lines trip."""
        async with self._session.post(
            self._tts._url(),
            json=self._tts._payload(sentence),
            headers=self._tts._headers(),
            timeout=aiohttp.ClientTimeout(total=120, sock_connect=self._conn_options.timeout),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        hexaudio = (data.get("data") or {}).get("audio")
        if hexaudio:
            emitter.push(bytes.fromhex(hexaudio))
