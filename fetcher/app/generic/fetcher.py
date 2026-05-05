"""Generic-target scraper. Same primitives, different target.

This is here to demonstrate that the scraping infrastructure is not coupled
to LinkedIn. Any new scraping target reuses TokenBucket, classify_error,
RetryPolicy, build_chrome_session — just by injecting a different host /
header set / parser.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..scraping.ratelimit import TokenBucket, sleep_jittered
from ..scraping.retry import (
    ErrorClass,
    RetryPolicy,
    classify_error,
)
from ..scraping.tls import build_chrome_session


@dataclass
class GenericScraper:
    """Compose the scraping primitives for any target.

    Caller supplies:
      - base_url: where to send requests
      - parser: callable from raw response body to a domain object
      - headers: any per-target headers (auth, user-agent variants, etc.)

    The scraper handles: TLS spoofing, jittered pacing, classified retries,
    daily-cap enforcement.
    """

    base_url: str
    parser: Callable[[str], Any]
    headers: dict[str, str] | None = None
    min_delay_ms: int = 1000
    max_delay_ms: int = 4000
    daily_cap: int = 500
    requests_today: int = 0
    bucket: TokenBucket | None = None
    retry: RetryPolicy | None = None

    def __post_init__(self) -> None:
        self.bucket = self.bucket or TokenBucket(capacity=2, refill_per_second=0.5)
        self.retry = self.retry or RetryPolicy()

    async def fetch(self, path: str) -> Any:
        if self.requests_today >= self.daily_cap:
            raise RuntimeError(f"daily cap reached: {self.requests_today}/{self.daily_cap}")
        await self.bucket.take()
        await sleep_jittered(self.min_delay_ms, self.max_delay_ms)
        self.requests_today += 1

        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        sess = build_chrome_session(user_agent=(self.headers or {}).get("user-agent"))
        attempt = 0
        try:
            while True:
                attempt += 1
                try:
                    resp = await sess.get(url, headers=self.headers or {})
                    status = getattr(resp, "status_code", None)
                    if status == 200:
                        return self.parser(resp.text)
                    cls = classify_error(status)
                except Exception:
                    cls = ErrorClass.TRANSIENT
                if not self.retry.should_retry(cls, attempt):
                    raise RuntimeError(f"giving up after {attempt} attempts ({cls})")
                import asyncio
                await asyncio.sleep(self.retry.delay(attempt))
        finally:
            close = getattr(sess, "aclose", None) or getattr(sess, "close", None)
            if close:
                try:
                    await close()
                except TypeError:
                    close()
