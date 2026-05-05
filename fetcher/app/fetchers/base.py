"""LinkedInFetcher protocol — the seam between IO and the rest of the system.

Every consumer of LinkedIn data depends on this protocol, not on a concrete
implementation. This is what makes fixture-based testing trivial and what
lets us swap acquisition strategies (Voyager / Extension / Official API)
without touching the verifier.
"""
from __future__ import annotations

from typing import Any, Protocol


class FetcherError(Exception):
    """Base for all fetcher failures."""


class RateLimitedError(FetcherError):
    """LinkedIn returned 429 or signaled a soft throttle."""


class AccountChallengedError(FetcherError):
    """Account hit a CAPTCHA / verification wall and needs human intervention."""


class FetcherUnavailableError(FetcherError):
    """No working acquisition path is currently available (no creds, quota exhausted)."""


class LinkedInFetcher(Protocol):
    """Narrow interface — three coherent operations. Interface Segregation by design."""

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        """Return raw Voyager-shaped profile JSON for the identifier."""
        ...

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return raw Voyager-shaped company search results."""
        ...

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        """Return raw Voyager-shaped company JSON."""
        ...
