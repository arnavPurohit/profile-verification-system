"""Mongo client + index setup. The only place that imports motor."""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


def build_mongo_db(url: str, db_name: str) -> AsyncIOMotorDatabase:
    client = AsyncIOMotorClient(url, uuidRepresentation="standard")
    return client[db_name]


async def ensure_indexes(
    db: AsyncIOMotorDatabase,
    *,
    profile_ttl_days: int,
    company_ttl_days: int,
    capture_ttl_days: int,
) -> None:
    """Create the indexes the system depends on, including TTL for cache expiry.

    Tolerates Mongo being unreachable so the fetcher can start without it
    (login mode only needs the browser, not the cache).
    """
    try:
        await db.profiles.create_index("urn", unique=True)
        await db.profiles.create_index("fetched_at", expireAfterSeconds=profile_ttl_days * 86400)
        await db.profiles.create_index([("name", "text"), ("headline", "text")])

        await db.companies.create_index("urn", unique=True)
        await db.companies.create_index("normalized")
        await db.companies.create_index("aliases")
        await db.companies.create_index("fetched_at", expireAfterSeconds=company_ttl_days * 86400)

        await db.raw_captures.create_index([("urn", 1), ("fetched_at", -1)])
        await db.raw_captures.create_index("fetched_at", expireAfterSeconds=capture_ttl_days * 86400)

        await db.fetch_jobs.create_index([("status", 1), ("scheduled_for", 1)])
        await db.fetch_jobs.create_index("urn")

        await db.verifications.create_index("created_at")
        await db.verifications.create_index("input.url")
    except Exception:
        import structlog
        structlog.get_logger(__name__).warning(
            "mongo.indexes_skipped",
            msg="Could not create indexes — Mongo may be unreachable. Caching will fail.",
        )
