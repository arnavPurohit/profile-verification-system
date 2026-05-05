"""Append-only storage of raw Voyager responses.

Source of truth: every parsed Profile/Company is derivable from a raw_capture.
When the parser improves we can re-derive without re-fetching.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorDatabase


class CapturesRepoProtocol(Protocol):
    async def insert(self, *, urn: str, kind: str, payload: dict[str, Any], source: str) -> None: ...
    async def latest(self, urn: str) -> dict[str, Any] | None: ...


class CapturesRepo:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db.raw_captures

    async def insert(
        self,
        *,
        urn: str,
        kind: str,
        payload: dict[str, Any],
        source: str,
        account_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        await self._col.insert_one({
            "urn": urn,
            "kind": kind,
            "payload": payload,
            "source": source,
            "account_id": account_id,
            "http_status": http_status,
            "fetched_at": datetime.now(timezone.utc),
        })

    async def latest(self, urn: str) -> dict[str, Any] | None:
        doc = await self._col.find_one({"urn": urn}, sort=[("fetched_at", -1)])
        if doc:
            doc.pop("_id", None)
        return doc
