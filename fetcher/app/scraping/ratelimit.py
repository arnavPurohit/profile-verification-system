"""Token-bucket rate limiter and log-normal jittered delay.

Two reasons it matters: per-account daily caps are non-negotiable for not getting
banned, and log-normal jitter (not uniform) is what makes traffic look human.
A scraper that hits every 30±5s is more bot-like than one with a long tail.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Simple async token bucket. Refills `rate` tokens per second up to `capacity`.

    Pure-time logic; no IO. Tests can drive it with a fake clock by passing now()."""

    capacity: int
    refill_per_second: float
    tokens: float = 0.0
    last_refill: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.last_refill = time.monotonic()

    def _refill(self, now: float) -> None:
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now

    async def take(self, n: int = 1) -> None:
        while True:
            now = time.monotonic()
            self._refill(now)
            if self.tokens >= n:
                self.tokens -= n
                return
            deficit = n - self.tokens
            wait = deficit / self.refill_per_second
            await asyncio.sleep(wait)


def jittered_delay(min_ms: int, max_ms: int) -> float:
    """Log-normal-shaped delay between min_ms and max_ms (in seconds).

    A real human's between-action timing has a long right tail: most actions
    are fast, occasional ones are slow (got distracted, read the page, etc.).
    Uniform jitter doesn't have that shape and is itself a detection signal.
    """
    if min_ms >= max_ms:
        return min_ms / 1000.0
    # Sample log-normal, scale to [min_ms, max_ms]
    # mu chosen so the median lands ~30% of the way through the range.
    log_sample = random.lognormvariate(mu=0.0, sigma=0.7)
    # clip the long tail so we never wait absurdly long
    log_sample = min(log_sample, 4.0)
    fraction = log_sample / 4.0  # 0..1
    delay_ms = min_ms + fraction * (max_ms - min_ms)
    return delay_ms / 1000.0


async def sleep_jittered(min_ms: int, max_ms: int) -> None:
    await asyncio.sleep(jittered_delay(min_ms, max_ms))


# small helper exposed for tests / observability
def lognormal_quantile(p: float) -> float:
    """Inverse CDF helper used in unit tests to assert distribution shape."""
    # For unit tests, not used in production paths
    return math.exp(0.0 + 0.7 * math.sqrt(2) * _erfinv(2 * p - 1))


def _erfinv(x: float) -> float:
    # Approximate inverse error function (Winitzki).
    a = 0.147
    ln = math.log(1 - x * x)
    first = 2 / (math.pi * a) + ln / 2
    return math.copysign(math.sqrt(math.sqrt(first * first - ln / a) - first), x)
