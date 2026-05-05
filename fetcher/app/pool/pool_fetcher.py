from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from ..config import Settings
from ..domain import Account
from ..fetchers.base import AccountChallengedError, FetcherError, RateLimitedError
from ..fetchers.login import LoginFetcher
from .account_pool import AccountPool

log = structlog.get_logger(__name__)


class PooledFetcher:
    """Wraps multiple LoginFetcher instances behind a single LinkedInFetcher interface.

    Each account in the pool gets its own lazily-initialised LoginFetcher with a
    dedicated Playwright persistent profile directory.
    """

    source = "pool"

    def __init__(self, pool: AccountPool, passwords: dict[str, str], settings: Settings):
        self._pool = pool
        self._passwords = passwords
        self._settings = settings
        self._fetchers: dict[str, LoginFetcher] = {}

    def _get_or_create_fetcher(self, account: Account) -> LoginFetcher:
        if account.id in self._fetchers:
            return self._fetchers[account.id]

        profile_dir = Path(
            self._settings.linkedin_playwright_profile_dir or ".linkedin-profiles"
        ) / account.id

        fetcher = LoginFetcher(
            account=account,
            email=account.email or "",
            password=self._passwords.get(account.id, ""),
            state_path=Path(f".linkedin-state-{account.id}.json"),
            headless=False,
            min_delay_ms=self._settings.linkedin_min_delay_ms,
            max_delay_ms=self._settings.linkedin_max_delay_ms,
            profile_dir=profile_dir,
            channel=self._settings.linkedin_playwright_channel or None,
        )
        self._fetchers[account.id] = fetcher
        log.info("pool_fetcher.created", account=account.id, email=account.email)
        return fetcher

    async def _execute_with_pool(self, method: str, *args: Any, **kwargs: Any) -> Any:
        account = await self._pool.acquire()
        fetcher = self._get_or_create_fetcher(account)
        try:
            result = await getattr(fetcher, method)(*args, **kwargs)
            await self._pool.release(account, success=True)
            return result
        except AccountChallengedError as e:
            await self._pool.release(account, success=False, error=str(e))
            raise
        except RateLimitedError as e:
            await self._pool.release(account, success=False, error=str(e))
            raise
        except FetcherError as e:
            await self._pool.release(account, success=False, error=str(e))
            raise

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        return await self._execute_with_pool("fetch_profile", urn_or_url)

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return await self._execute_with_pool("search_companies", query, limit)

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        return await self._execute_with_pool("fetch_company", urn)

    async def close(self) -> None:
        for fetcher in self._fetchers.values():
            try:
                await fetcher.close()
            except Exception as e:
                log.warning("pool_fetcher.close_error", error=str(e))
        self._fetchers.clear()
