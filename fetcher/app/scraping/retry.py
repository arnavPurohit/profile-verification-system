"""Classified retry policy.

Different errors have different right answers:
  - 429 → back off and retry
  - 401 → account dead, do not retry, alert
  - 999 → LinkedIn-specific block, escalate to headless or pause account
  - 5xx → transient, retry with backoff
  - parse failure → no retry, log to schema-drift monitor
  - everything else → no retry

This keeps retry logic out of every fetcher and gives operations one place
to tune behavior.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar


class ErrorClass(str, Enum):
    RATE_LIMITED = "rate_limited"
    ACCOUNT_DEAD = "account_dead"
    LINKEDIN_BLOCK = "linkedin_block"
    TRANSIENT = "transient"
    PARSE_FAILURE = "parse_failure"
    PERMANENT = "permanent"


def classify_error(status: int | None, body_excerpt: str = "") -> ErrorClass:
    if status == 429:
        return ErrorClass.RATE_LIMITED
    if status == 401 or status == 403:
        return ErrorClass.ACCOUNT_DEAD
    if status == 999:
        return ErrorClass.LINKEDIN_BLOCK
    # 3xx with redirects-disabled means LinkedIn is bouncing us to auth.
    # Either cookies are bad or the endpoint we hit is gone.
    if status is not None and 300 <= status < 400:
        return ErrorClass.ACCOUNT_DEAD
    if status is not None and 500 <= status < 600:
        return ErrorClass.TRANSIENT
    if status is None:
        # network-level error; treat as transient
        return ErrorClass.TRANSIENT
    return ErrorClass.PERMANENT


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 1.5
    max_delay_s: float = 30.0
    jitter: float = 0.5  # ±50% multiplicative jitter

    def should_retry(self, cls: ErrorClass, attempt: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        return cls in {ErrorClass.RATE_LIMITED, ErrorClass.TRANSIENT}

    def delay(self, attempt: int) -> float:
        backoff = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        spread = backoff * self.jitter
        return random.uniform(backoff - spread, backoff + spread)


T = TypeVar("T")


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    classify: Callable[[BaseException], ErrorClass],
    policy: RetryPolicy = RetryPolicy(),
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except BaseException as e:
            cls = classify(e)
            if not policy.should_retry(cls, attempt):
                raise
            await asyncio.sleep(policy.delay(attempt))
