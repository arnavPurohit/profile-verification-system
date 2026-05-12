"""FallbackFetcher — tries each fetcher in order, moving to the next on FetcherError."""
from __future__ import annotations

from typing import Any

import structlog

from .base import FetcherError, LinkedInFetcher

log = structlog.get_logger(__name__)


def _to_public_shape(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Strip a full login/voyager profile down to what public mode would return:
    - Only current experience entries, no dates, no URNs
    - No education
    - source stays as whatever the underlying fetcher set (login/voyager)
      but data_shape matches public — verifier treats it the same way
    """
    current_exp = [
        {
            "company_urn": None,
            "company_name": e.get("company_name") or e.get("companyName", ""),
            "title": e.get("title", ""),
            "start": None,
            "end": None,
            "is_current": True,
            "employment_type": "unknown",
            "location": e.get("location", ""),
            "description": None,
        }
        for e in raw.get("experience", [])
        if e.get("is_current") or e.get("timePeriod", {}).get("endDate") is None
    ]

    return {
        **raw,
        "experience": current_exp,
        "education": [],
        "source": "public",
    }


class FallbackFetcher:
    """
    Wraps an ordered list of fetchers. On FetcherError moves to the next one.
    When falling back past the first fetcher, normalises the result to
    public-profile shape so callers can't distinguish the acquisition path.
    """

    def __init__(self, *fetchers: LinkedInFetcher) -> None:
        self._fetchers = fetchers

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        last_err: Exception = FetcherError("no fetchers configured")
        for i, fetcher in enumerate(self._fetchers):
            try:
                raw = await fetcher.fetch_profile(urn_or_url)
                if i > 0:
                    log.info(
                        "fallback.normalising_to_public",
                        fetcher=type(fetcher).__name__,
                    )
                    return _to_public_shape(raw)
                return raw
            except FetcherError as e:
                next_name = (
                    type(self._fetchers[i + 1]).__name__
                    if i + 1 < len(self._fetchers)
                    else "none"
                )
                log.warning(
                    "fallback.trying_next",
                    failed=type(fetcher).__name__,
                    reason=str(e),
                    next=next_name,
                )
                last_err = e
        raise last_err

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        last_err: Exception = FetcherError("no fetchers configured")
        for fetcher in self._fetchers:
            try:
                return await fetcher.search_companies(query, limit)
            except FetcherError as e:
                last_err = e
        raise last_err

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        last_err: Exception = FetcherError("no fetchers configured")
        for fetcher in self._fetchers:
            try:
                return await fetcher.fetch_company(urn)
            except FetcherError as e:
                last_err = e
        raise last_err

    async def close(self) -> None:
        for fetcher in self._fetchers:
            if hasattr(fetcher, "close"):
                try:
                    await fetcher.close()
                except Exception:
                    pass
