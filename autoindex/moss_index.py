"""
moss_index.py — Moss is the ONLY store. No local files.

The autonomous crawler streams everything it discovers into a Moss index via batched
add_docs (one network op per batch, to respect the project's monthly op quota). At
runtime the agent queries this same index for an instant answer.

Creds come from sahur-brain/.env (MOSS_PROJECT_ID / MOSS_PROJECT_KEY / MOSS_MODEL_ID).
The target index is MOSS_AUTOINDEX (default 'sahur-autoindex').
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "sahur-brain", ".env"))

from moss import DocumentInfo, MossClient, QueryOptions


class MossIndex:
    def __init__(self, name: str | None = None):
        self.pid = os.environ.get("MOSS_PROJECT_ID")
        self.pk = os.environ.get("MOSS_PROJECT_KEY")
        self.name = name or os.environ.get("MOSS_AUTOINDEX", "sahur-autoindex")
        self.model = os.environ.get("MOSS_MODEL_ID", "moss-minilm")
        self._created = False
        self.stats = {"batches": 0, "docs": 0, "errors": 0, "last_error": ""}

    def available(self) -> bool:
        return bool(self.pid and self.pk)

    # ---- write (batched: each call = 1 Moss op) ----
    async def _add_async(self, docs):
        client = MossClient(self.pid, self.pk)
        try:
            await client.add_docs(self.name, docs)
            return True, "added"
        except Exception as e:
            if not self._created:                       # index may not exist yet
                try:
                    await client.create_index(self.name, docs, self.model)
                    self._created = True
                    return True, "created"
                except Exception as e2:
                    return False, str(e2)
            return False, str(e)

    def add(self, items: list[dict]) -> tuple[bool, str]:
        """items: [{id, text, metadata}]. Pushes the whole batch in one Moss op."""
        if not items:
            return True, "empty"
        if not self.available():
            return False, "no MOSS creds"
        docs = [DocumentInfo(id=i["id"], text=i["text"], metadata=i.get("metadata", {})) for i in items]
        try:
            ok, msg = asyncio.run(self._add_async(docs))
        except Exception as e:
            ok, msg = False, str(e)
        self.stats["batches"] += 1
        if ok:
            self.stats["docs"] += len(docs)
        else:
            self.stats["errors"] += 1
            self.stats["last_error"] = msg[:160]
        return ok, msg

    # ---- read (runtime instant lookup) ----
    def query(self, text: str, top_k: int = 5) -> list[dict]:
        if not self.available():
            return []

        async def _q():
            client = MossClient(self.pid, self.pk)
            try:
                await client.load_index(self.name)
            except Exception:
                pass
            res = await client.query(self.name, text, QueryOptions(top_k=top_k))
            return [{"text": getattr(d, "text", ""), "score": getattr(d, "score", None),
                     "metadata": getattr(d, "metadata", {}) or {}} for d in getattr(res, "docs", [])]
        try:
            return asyncio.run(_q())
        except Exception as e:
            return [{"error": str(e)[:160]}]
