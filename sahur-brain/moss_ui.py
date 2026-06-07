"""
moss_ui.py — Moss-powered UI grounding (fast path).

Resolve a natural-language target ("the search button") to an exact tap coordinate
via Moss semantic search over the screen's clickable elements.

SPEED: one persistent MossClient on a background event loop (connect once), and each
distinct screen is indexed into Moss only ONCE (cached by signature) — after that a
lookup is just a query (Moss's sub-10ms path). Repeated screens (the search tab, a
results list) cost nothing to re-ground. Falls back to a local lexical ranker if Moss
is unavailable, so taps never block.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading

from device import UIElement

try:
    from moss import DocumentInfo, MossClient, QueryOptions
    _MOSS_IMPORTABLE = True
except Exception:
    _MOSS_IMPORTABLE = False


def _doc_text(e: UIElement) -> str:
    base = e.label or e.value or e.identifier
    return f"{base} ({e.role})" if e.role and base else base


class MossUI:
    def __init__(self):
        pid = os.environ.get("MOSS_PROJECT_ID")
        pk = os.environ.get("MOSS_PROJECT_KEY")
        self.index = os.environ.get("MOSS_UI_INDEX", "sahur-ui")
        self.model = os.environ.get("MOSS_MODEL_ID", "moss-minilm")
        self._pid, self._pk = pid, pk
        self.enabled = bool(pid and pk and _MOSS_IMPORTABLE)
        self._client = None
        self._loop = None
        self._indexed: set[str] = set()   # screen signatures already in Moss
        self._indexing: set[str] = set()  # screen signatures being pushed right now
        self._loaded = False              # index loaded locally at least once
        # grounding telemetry: how each lookup resolved (moss index vs local fallback)
        self.stats = {"moss": 0, "local": 0, "none": 0}
        self.last_source = "none"         # "moss" | "local" | "none" for the last find()
        self.last_ms = 0.0                # latency of the last find()

    # ---- persistent background event loop -----------------------------------

    def _ensure_loop(self):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def _run(self, coro, timeout: float = 20.0):
        self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    # ---- public sync API ----------------------------------------------------

    def find(self, elements: list[UIElement], app: str, target: str, top_k: int = 5) -> list[dict]:
        """Moss does the semantic ranking (using the pre-indexed corpus, fast ~3-9ms),
        then we map its top labels back to the elements ON THIS SCREEN so the tap
        coordinate is always live/correct. Never blocks: also background-indexes the
        current screen for future dynamic content, and falls back to a local ranker."""
        import time as _t
        _t0 = _t.time()
        # drop single-char labels (keyboard keys) so we never tap a letter key
        cands = [e for e in elements if len((e.label or e.value or e.identifier or "").strip()) > 1]
        if not cands:
            self.last_source = "none"; self.last_ms = 0.0
            return []
        if self.enabled:
            moss = []
            try:
                moss = self._run(self._query_async(app, target, top_k), timeout=8)
            except Exception as ex:
                print(f"  (moss query fallback: {ex})")
            mapped = _map_to_current(moss, cands)
            if mapped:
                self.last_source = "moss"; self.stats["moss"] += 1     # pre-indexed Moss -> fast
                self.last_ms = (_t.time() - _t0) * 1000
                return mapped
        # not covered (e.g. dynamic results) -> instant local ranking; no runtime indexing
        res = _local_rank(cands, target, top_k)
        self.last_source = "local" if res else "none"
        self.stats[self.last_source] += 1
        self.last_ms = (_t.time() - _t0) * 1000
        return res

    # ---- moss internals -----------------------------------------------------

    def _sig(self, elements: list[UIElement], app: str) -> str:
        parts = [app] + [f"{_doc_text(e)}|{int(e.center[0])}|{int(e.center[1])}" for e in elements[:40]]
        return hashlib.md5("\n".join(parts).encode()).hexdigest()

    def _docs(self, elements: list[UIElement], app: str) -> list:
        docs = []
        for e in elements:
            text = _doc_text(e)
            if not text:
                continue
            cx, cy = e.center
            did = hashlib.md5(f"{app}|{text}|{cx}|{cy}".encode()).hexdigest()
            docs.append(DocumentInfo(
                id=did, text=text,
                metadata={"app": app, "x": str(cx), "y": str(cy), "label": (e.label or e.value or text)},
            ))
        return docs

    async def _ensure_client(self):
        if self._client is None:
            self._client = MossClient(self._pid, self._pk)
        return self._client

    def _parse(self, res) -> list[dict]:
        out = []
        for d in getattr(res, "docs", []) or []:
            m = getattr(d, "metadata", None) or {}
            try:
                out.append({
                    "label": m.get("label", getattr(d, "text", "")),
                    "x": int(float(m.get("x", 0))), "y": int(float(m.get("y", 0))),
                    "score": getattr(d, "score", None),
                })
            except (TypeError, ValueError):
                continue
        return out

    async def _query_async(self, app: str, target: str, top_k: int) -> list[dict]:
        client = await self._ensure_client()
        if not self._loaded:   # load the (possibly pre-built) index once per session
            try:
                await client.load_index(self.index)
                self._loaded = True
            except Exception:
                pass
        flt = {"field": "app", "condition": {"$eq": app}}
        try:
            res = await client.query(self.index, target, QueryOptions(top_k=top_k, filter=flt))
        except Exception:
            res = await client.query(self.index, target, QueryOptions(top_k=top_k))
        return self._parse(res)

    # ---- background indexing (never blocks a tap) ---------------------------

    def _bg_index(self, elements: list[UIElement], app: str, sig: str) -> None:
        if sig in self._indexed or sig in self._indexing:
            return
        self._indexing.add(sig)
        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(self._index_coro(self._docs(elements, app), sig), self._loop)

    async def _index_coro(self, docs, sig: str) -> None:
        try:
            client = await self._ensure_client()
            try:
                await client.add_docs(self.index, docs)
            except Exception:
                await client.create_index(self.index, docs, self.model)
            try:
                await client.load_index(self.index)
                self._loaded = True
            except Exception:
                pass
            self._indexed.add(sig)
        except Exception:
            pass
        finally:
            self._indexing.discard(sig)

    def prewarm(self, elements: list[UIElement], app: str) -> None:
        """Kick off background indexing of the current screen (non-blocking)."""
        if not self.enabled:
            return
        cands = [e for e in elements if (e.label or e.value or e.identifier)]
        if cands:
            self._bg_index(cands, app, self._sig(cands, app))

    def warm(self) -> None:
        """Load the (pre-built) Moss index now so the first runtime lookup is fast."""
        if not self.enabled or self._loaded:
            return
        async def _load():
            client = await self._ensure_client()
            try:
                await client.load_index(self.index)
                self._loaded = True
            except Exception:
                pass
        try:
            self._run(_load(), timeout=15)
        except Exception:
            pass

    def index_blocking(self, elements: list[UIElement], app: str, timeout: float = 40) -> int:
        """Index a screen and WAIT for it to land (used by the offline pre-indexer).
        Returns number of docs pushed (0 if skipped/disabled)."""
        if not self.enabled:
            return 0
        cands = [e for e in elements if (e.label or e.value or e.identifier)]
        if not cands:
            return 0
        sig = self._sig(cands, app)
        if sig in self._indexed:
            return 0
        docs = self._docs(cands, app)
        try:
            self._run(self._index_coro(docs, sig), timeout=timeout)
            return len(docs)
        except Exception as e:
            print(f"  (index error: {e})")
            return 0


# ---- local fallback ranker -------------------------------------------------

_SYN = {
    "search": {"explore", "find", "discover", "magnify", "lookup", "browse"},
    "explore": {"search", "discover"},
    "like": {"heart", "favorite", "love"},
    "share": {"send", "paper", "plane"},
    "send": {"share", "direct", "dm", "message"},
    "messages": {"direct", "dm", "inbox", "chat"},
    "profile": {"account", "me", "you", "avatar"},
    "home": {"feed", "main"},
    "comment": {"comments", "reply"},
    "back": {"close", "cancel", "dismiss", "return"},
    "create": {"new", "add", "compose", "post", "plus"},
    "settings": {"options", "preferences", "gear"},
    "play": {"start", "watch", "resume"},
}


def _map_to_current(moss_results: list[dict], current: list[UIElement]) -> list[dict]:
    """Moss ranked labels -> the matching element ON THIS SCREEN (live coords)."""
    if not moss_results:
        return []
    cur = [(e, (e.label or e.value or e.identifier or "").strip().lower()) for e in current]
    out, seen = [], set()
    for mr in moss_results:
        lbl = (mr.get("label") or "").strip().lower()
        if not lbl:
            continue
        match = next((e for e, el in cur if el == lbl), None)
        if not match:
            match = next((e for e, el in cur if el and (lbl in el or el in lbl)), None)
        if match:
            cx, cy = match.center
            if (cx, cy) in seen:
                continue
            seen.add((cx, cy))
            out.append({"label": match.label or match.value, "x": cx, "y": cy, "score": mr.get("score")})
    return out


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _expand(toks: set[str]) -> set[str]:
    out = set(toks)
    for t in toks:
        out |= _SYN.get(t, set())
    return out


def _local_rank(elements: list[UIElement], target: str, top_k: int) -> list[dict]:
    tt = _expand(_tokens(target))
    scored = []
    for e in elements:
        label = e.label or e.value or e.identifier
        et = _tokens(label)
        if not et:
            continue
        overlap = len(tt & et)
        # Word-boundary match, NOT raw substring: "play" must match the word "play"
        # but never the "play" buried inside "airplay" (which was scoring the device
        # picker as the top hit for a play command).
        tl = target.strip().lower()
        sub = 1 if tl and re.search(rf"\b{re.escape(tl)}\b", label.lower()) else 0
        score = sub * 2 + overlap
        if score > 0:
            cx, cy = e.center
            scored.append((score, {"label": label, "x": cx, "y": cy, "score": float(score)}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:top_k]]
