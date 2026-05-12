"""FallbackFetcher — tries each fetcher in order, moving to the next on FetcherError."""
from __future__ import annotations

from typing import Any

import structlog

from .base import FetcherError, LinkedInFetcher

log = structlog.get_logger(__name__)


class FallbackFetcher:
    """Wraps an ordered list of fetchers. On FetcherError, tries the next one."""

    def __init__(self, *fetchers: LinkedInFetcher) -> None:
        self._fetchers = fetchers

    async def _try_each(self, method: str, *args: Any, **kwargs: Any) -> Any:
        last_err: Exception = FetcherError("no fetchers configured")
        for fetcher in self._fetchers:
            try:
                return await getattr(fetcher, method)(*args, **kwargs)
            except FetcherError as e:
                log.warning(
                    "fallback.trying_next",
                    failed=type(fetcher).__name__,
                    reason=str(e),
                    next=type(self._fetchers[self._fetchers.index(fetcher) + 1]).__name__
                    if self._fetchers.index(fetcher) + 1 < len(self._fetchers)
                    else "none",
                )
                last_err = e
        raise last_err

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        return await self._try_each("fetch_profile", urn_or_url)

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return await self._try_each("search_companies", query, limit)

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        return await self._try_each("fetch_company", urn)

    async def close(self) -> None:
        for fetcher in self._fetchers:
            if hasattr(fetcher, "close"):
                try:
                    await fetcher.close()
                except Exception:
                    pass
