"""
lk_compat.py — runtime shims for MiniMax <-> LiveKit Agents incompatibilities.

IMPORT THIS BEFORE STARTING AN AgentSession (just `import lk_compat`).

MiniMax's OpenAI-compatible streaming returns a usage object whose token counts are NULL
(`completion_tokens`/`prompt_tokens` = None). LiveKit's shared LLM stream
(livekit/agents/inference/llm.py — which the openai plugin's LLMStream subclasses) hardcodes
`stream_options={"include_usage": True}` and then builds `llm.CompletionUsage(...)`, whose
fields REQUIRE ints. Those Nones make pydantic raise, which LiveKit re-wraps as
`APIConnectionError("Connection error")` — and it kills EVERY user turn.

Fix: replace `livekit.agents.llm.CompletionUsage` (looked up as `llm.CompletionUsage` at call
time inside inference/llm.py) with a factory that coerces the Nones to 0 and returns a real
CompletionUsage instance. Token accounting just reads 0 for MiniMax instead of crashing.
"""

from __future__ import annotations

import livekit.agents.llm as _lk_llm

_RealCompletionUsage = _lk_llm.CompletionUsage
_INT_FIELDS = (
    "completion_tokens", "prompt_tokens", "total_tokens",
    "prompt_cached_tokens", "cache_creation_tokens", "cache_read_tokens",
)


def _safe_completion_usage(**kwargs):
    for f in _INT_FIELDS:
        if kwargs.get(f) is None:
            kwargs[f] = 0
    return _RealCompletionUsage(**kwargs)


# Only patch once. inference/llm.py does `from .. import llm` then `llm.CompletionUsage(...)`,
# so replacing the package attribute is enough (no direct `from ..llm import CompletionUsage`).
if getattr(_lk_llm.CompletionUsage, "__name__", "") != "_safe_completion_usage":
    _lk_llm.CompletionUsage = _safe_completion_usage
