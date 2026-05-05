"""Cache-first company retrieval and search."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from ..domain import Company
from ..fetchers import LinkedInFetcher
from ..parsers import parse_company
from ..storage import CapturesRepo, CompaniesRepo, RedisCache

log = structlog.get_logger(__name__)

_REDIS_COMPANY_TTL = 60 * 60 * 24 * 7  # 7 days


class CompanyService:
    def __init__(
        self,
        fetcher: LinkedInFetcher,
        companies: CompaniesRepo,
        captures: CapturesRepo,
        *,
        fresh_days: int = 60,
        hard_ttl_days: int = 365,
        parser_version: str = "1.0.0",
        redis: RedisCache | None = None,
    ):
        self._fetcher = fetcher
        self._companies = companies
        self._captures = captures
        self._fresh_days = fresh_days
        self._hard_ttl_days = hard_ttl_days
        self._parser_version = parser_version
        self._redis = redis

    async def get_or_fetch(self, urn: str) -> tuple[Company, str]:
        if self._redis:
            hit = await self._redis.get_company(urn)
            if hit:
                return Company.model_validate(hit), "cache_fresh"

        cached = await self._companies.get(urn)
        now = datetime.now(timezone.utc)
        if cached:
            age = now - _aware(cached.fetched_at)
            if age < timedelta(days=self._fresh_days):
                if self._redis:
                    await self._redis.set_company(urn, cached.model_dump(mode="json"), _REDIS_COMPANY_TTL)
                return cached, "cache_fresh"
            if age < timedelta(days=self._hard_ttl_days):
                return cached, "cache_stale"
        raw = await self._fetcher.fetch_company(urn)
        company = parse_company(
            raw,
            parser_version=self._parser_version,
            source=getattr(self._fetcher, "source", "voyager"),
            fetched_at=now,
        )
        await self._captures.insert(
            urn=company.urn, kind="company", payload=raw, source=company.source,
        )
        await self._companies.upsert(company)
        if self._redis:
            await self._redis.set_company(urn, company.model_dump(mode="json"), _REDIS_COMPANY_TTL)
        return company, "fetched"

    async def search(self, query: str, limit: int = 5) -> list[Company]:
        # Try local first — if we've cached this name before, surface it cheaply.
        local = await self._companies.find_by_normalized(_norm(query))
        if local:
            return [local]
        # Otherwise hit LinkedIn search
        raws = await self._fetcher.search_companies(query, limit=limit)
        results: list[Company] = []
        now = datetime.now(timezone.utc)
        for raw in raws:
            try:
                company = parse_company(raw, parser_version=self._parser_version, fetched_at=now)
                await self._companies.upsert(company)
                results.append(company)
            except Exception as e:
                log.warning("company.parse_failed", error=str(e))
        return results


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _norm(s: str) -> str:
    return s.strip().lower()
