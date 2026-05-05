"""Composes fetcher + parser + repo into the cache-first profile retrieval flow.

This is where stale-while-revalidate logic lives: cache hit → return; soft-stale
→ return + queue refresh; hard-stale or miss → block on fetch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from ..domain import Profile
from ..fetchers import FetcherError, LinkedInFetcher
from ..parsers import parse_profile
from ..storage import CapturesRepo, ProfilesRepo, RedisCache

log = structlog.get_logger(__name__)

_REDIS_PROFILE_TTL = 60 * 60 * 24  # 1 day


class ProfileService:
    def __init__(
        self,
        fetcher: LinkedInFetcher,
        profiles: ProfilesRepo,
        captures: CapturesRepo,
        *,
        fresh_days: int = 14,
        hard_ttl_days: int = 90,
        parser_version: str = "1.0.0",
        redis: RedisCache | None = None,
    ):
        self._fetcher = fetcher
        self._profiles = profiles
        self._captures = captures
        self._fresh_days = fresh_days
        self._hard_ttl_days = hard_ttl_days
        self._parser_version = parser_version
        self._redis = redis

    async def get_or_fetch(
        self, urn_or_url: str, *, max_age_days: int | None = None,
    ) -> tuple[Profile, str]:
        """Return (profile, source) where source ∈ {'cache_fresh', 'cache_stale', 'fetched'}.

        If *max_age_days* is set, treat anything older as hard-stale (force re-fetch).
        """
        if self._redis:
            hit = await self._redis.get_profile(urn_or_url)
            if hit:
                return Profile.model_validate(hit), "cache_fresh"

        cached = await self._profiles.get_by_url_or_slug(urn_or_url) if "/" in urn_or_url or "linkedin" in urn_or_url else await self._profiles.get(urn_or_url)
        now = datetime.now(timezone.utc)
        if cached:
            age = now - _aware(cached.fetched_at)
            effective_fresh = min(self._fresh_days, max_age_days) if max_age_days else self._fresh_days
            effective_hard = min(self._hard_ttl_days, max_age_days) if max_age_days else self._hard_ttl_days
            if age < timedelta(days=effective_fresh):
                if self._redis:
                    await self._redis.set_profile(urn_or_url, cached.model_dump(mode="json"), _REDIS_PROFILE_TTL)
                return cached, "cache_fresh"
            if age < timedelta(days=effective_hard):
                log.info("profile.soft_stale", urn=cached.urn, age_days=age.days)
                return cached, "cache_stale"

        raw = await self._fetcher.fetch_profile(urn_or_url)
        profile = parse_profile(
            raw,
            parser_version=self._parser_version,
            source=getattr(self._fetcher, "source", "voyager"),
            fetched_at=now,
        )
        await self._captures.insert(
            urn=profile.urn, kind="profile", payload=raw, source=profile.source,
        )
        await self._profiles.upsert(profile)
        if self._redis:
            await self._redis.set_profile(urn_or_url, profile.model_dump(mode="json"), _REDIS_PROFILE_TTL)
        return profile, "fetched"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
