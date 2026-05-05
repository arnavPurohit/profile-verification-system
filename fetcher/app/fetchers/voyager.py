"""Authenticated server-side LinkedIn scraper.

Demonstrates the full 9-layer stack we can build in code:
  layer 1 — acquisition (this class)
  layer 2 — identity (Account injected; pool would swap many)
  layer 3 — network (proxy applied to the session if configured)
  layer 4 — fingerprint (TLS via curl_cffi in scraping.tls)
  layer 5 — behavior (per-request jittered delay, daily cap)
  layer 6 — capture (raw response stored upstream)
  layer 7 — storage (caller's repo handles)
  layer 8 — orchestration (caller's queue / scheduler)
  layer 9 — observability (structured logs)

Everything below layer 4 is non-negotiable for sustained scraping; everything
above layer 4 is what separates a 2-week-lifespan account from a 2-year one.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import structlog

from ..domain import Account
from ..scraping.headers import voyager_headers
from ..scraping.ratelimit import sleep_jittered
from ..scraping.retry import (
    ErrorClass,
    RetryPolicy,
    classify_error,
)
from .base import (
    AccountChallengedError,
    FetcherError,
    FetcherUnavailableError,
    LinkedInFetcher,
    RateLimitedError,
)

log = structlog.get_logger(__name__)

VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

_PROFILE_PATH = "/identity/dash/profiles"
_PROFILE_VIEW_PATH = "/identity/profiles"
_COMPANY_SEARCH_PATH = "/search/blended"
_COMPANY_PATH = "/organization/companies"

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)


def _extract_slug_or_urn(input_str: str) -> tuple[str, bool]:
    """Return (identifier, is_urn). is_urn=True means URN, False means public-id slug."""
    if input_str.startswith("urn:"):
        return input_str.split(":")[-1], True
    m = _SLUG_RE.search(input_str)
    if m:
        return m.group(1), False
    return input_str.strip("/"), False


class VoyagerFetcher(LinkedInFetcher):
    """Talks to LinkedIn's internal Voyager API using a single authenticated account.

    Production would swap a single account for a pool; the interface is unchanged.
    """

    def __init__(
        self,
        session: Any,
        account: Account,
        min_delay_ms: int = 2500,
        max_delay_ms: int = 12000,
        retry_policy: RetryPolicy | None = None,
    ):
        self._session = session
        self._account = account
        self._min_delay_ms = min_delay_ms
        self._max_delay_ms = max_delay_ms
        self._retry = retry_policy or RetryPolicy()
        self._lock = asyncio.Lock()
        self._headers = voyager_headers(account.cookies.get("JSESSIONID", ""))

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        ident, is_urn = _extract_slug_or_urn(urn_or_url)
        # /identity/profiles/{publicId or memberUrn}
        path = f"{_PROFILE_VIEW_PATH}/{ident}/profileView"
        return await self._get(path)

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        path = (
            f"{_COMPANY_SEARCH_PATH}?count={limit}&filters=List(resultType-%3ECOMPANIES)"
            f"&keywords={query}&origin=GLOBAL_SEARCH_HEADER&q=all"
        )
        body = await self._get(path)
        elements = body.get("data", {}).get("elements", []) or body.get("elements", [])
        return elements[:limit]

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        ident = urn.split(":")[-1] if urn.startswith("urn:") else urn
        path = f"{_COMPANY_PATH}/{ident}"
        return await self._get(path)

    # ---------- internal ----------

    async def _check_account_health(self) -> None:
        if self._account.state in {"suspended", "retired"}:
            raise FetcherUnavailableError(f"account {self._account.id} is {self._account.state}")
        if self._account.state == "challenged":
            raise AccountChallengedError(f"account {self._account.id} pending CAPTCHA")
        if self._account.daily_used >= self._account.daily_cap:
            raise FetcherUnavailableError(
                f"account {self._account.id} hit daily cap "
                f"({self._account.daily_used}/{self._account.daily_cap})"
            )

    async def _get(self, path: str) -> dict[str, Any]:
        await self._check_account_health()
        url = VOYAGER_BASE + path
        attempt = 0
        while True:
            attempt += 1
            async with self._lock:
                # behavioral pacing: jittered delay between EVERY request,
                # serialized through a per-account lock
                await sleep_jittered(self._min_delay_ms, self._max_delay_ms)
                self._account.daily_used += 1
                self._account.last_used_at = datetime.now(timezone.utc)
                try:
                    resp = await self._session.get(url, headers=self._headers, allow_redirects=False)
                except Exception as e:  # network-level error
                    log.warning("voyager.network_error", path=path, error=str(e))
                    cls = ErrorClass.TRANSIENT
                else:
                    status = getattr(resp, "status_code", None)
                    cls = classify_error(status)
                    if cls == ErrorClass.PERMANENT or status == 200:
                        if status == 200:
                            try:
                                return resp.json()
                            except Exception as e:
                                raise FetcherError(f"failed to parse JSON: {e}") from e
                        raise FetcherError(f"voyager returned {status} on {path}")
                    if cls == ErrorClass.ACCOUNT_DEAD:
                        self._account.state = "suspended"
                        log.error("voyager.account_dead", account=self._account.id)
                        raise FetcherUnavailableError("account suspended (401)")
                    if cls == ErrorClass.LINKEDIN_BLOCK:
                        self._account.state = "challenged"
                        self._account.last_challenge_at = datetime.now(timezone.utc)
                        log.error("voyager.999_block", account=self._account.id)
                        raise AccountChallengedError("LinkedIn 999 block")
                    if cls == ErrorClass.RATE_LIMITED:
                        log.warning("voyager.rate_limited", account=self._account.id)

            if not self._retry.should_retry(cls, attempt):
                if cls == ErrorClass.RATE_LIMITED:
                    raise RateLimitedError("rate limited and out of retries")
                raise FetcherError(f"giving up after {attempt} attempts ({cls})")
            await asyncio.sleep(self._retry.delay(attempt))
