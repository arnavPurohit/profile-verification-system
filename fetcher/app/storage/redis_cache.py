"""Thin Redis caching layer for profiles and companies."""
from __future__ import annotations

import json

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)


class RedisCache:
    def __init__(self, client: Redis) -> None:
        self._r = client

    @classmethod
    def from_url(cls, url: str) -> RedisCache:
        client = Redis.from_url(url, decode_responses=True)
        return cls(client)

    async def get_profile(self, key: str) -> dict | None:
        return await self._get(f"profile:{key}")

    async def set_profile(self, key: str, profile_dict: dict, ttl_seconds: int) -> None:
        await self._set(f"profile:{key}", profile_dict, ttl_seconds)

    async def get_company(self, key: str) -> dict | None:
        return await self._get(f"company:{key}")

    async def set_company(self, key: str, company_dict: dict, ttl_seconds: int) -> None:
        await self._set(f"company:{key}", company_dict, ttl_seconds)

    async def delete(self, key: str) -> None:
        try:
            await self._r.delete(key)
        except Exception:
            log.warning("redis.delete_failed", key=key, exc_info=True)

    async def close(self) -> None:
        try:
            await self._r.aclose()
        except Exception:
            log.warning("redis.close_failed", exc_info=True)

    async def _get(self, full_key: str) -> dict | None:
        try:
            raw = await self._r.get(full_key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            log.warning("redis.get_failed", key=full_key, exc_info=True)
            return None

    async def _set(self, full_key: str, data: dict, ttl_seconds: int) -> None:
        try:
            await self._r.set(full_key, json.dumps(data, default=str), ex=ttl_seconds)
        except Exception:
            log.warning("redis.set_failed", key=full_key, exc_info=True)
