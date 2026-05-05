"""Mongo-backed job queue.

For the take-home this is sufficient. Production would swap to Redis/SQS;
the interface stays the same. Used for stale-while-revalidate refresh jobs
and for queueing fetches when the verifier requests a profile we don't have.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class FetchJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    urn: str
    kind: str  # 'profile' | 'company' | 'company_search'
    priority: int = 5
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    scheduled_for: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JobQueue:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db.fetch_jobs

    async def enqueue(self, *, urn: str, kind: str, priority: int = 5, delay_seconds: int = 0) -> FetchJob:
        scheduled_for = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        job = FetchJob(urn=urn, kind=kind, priority=priority, scheduled_for=scheduled_for)
        await self._col.insert_one(job.model_dump(mode="json"))
        return job

    async def claim_next(self) -> FetchJob | None:
        """Atomically claim the next pending job whose scheduled time has arrived."""
        now = datetime.now(timezone.utc)
        doc = await self._col.find_one_and_update(
            {"status": JobStatus.PENDING.value, "scheduled_for": {"$lte": now}},
            {"$set": {"status": JobStatus.RUNNING.value}, "$inc": {"attempts": 1}},
            sort=[("priority", 1), ("scheduled_for", 1)],
            return_document=True,
        )
        if not doc:
            return None
        doc.pop("_id", None)
        return FetchJob.model_validate(doc)

    async def mark_done(self, job_id: str) -> None:
        await self._col.update_one(
            {"id": job_id},
            {"$set": {"status": JobStatus.DONE.value}},
        )

    async def mark_failed(self, job_id: str, error: str) -> None:
        await self._col.update_one(
            {"id": job_id},
            {"$set": {"status": JobStatus.FAILED.value, "last_error": error}},
        )
