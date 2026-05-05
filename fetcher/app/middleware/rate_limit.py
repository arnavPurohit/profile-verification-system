"""API-level rate limiting middleware for FastAPI.

Uses an in-memory sliding window counter per client IP. When Redis is available,
state is shared across workers via Redis; otherwise falls back to per-process
in-memory tracking (still useful for single-worker deploys).
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

log = structlog.get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        *,
        requests_per_minute: int = 60,
        redis_client: Any = None,
    ):
        super().__init__(app)
        self._rpm = requests_per_minute
        self._redis = redis_client
        self._local: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in ("/health", "/docs", "/openapi.json"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{client_ip}"
        now = time.time()
        window_start = now - 60

        if self._redis:
            allowed = await self._check_redis(key, now, window_start)
        else:
            allowed = self._check_local(key, now, window_start)

        if not allowed:
            return Response(
                content='{"error":"rate_limit_exceeded","retry_after_seconds":60}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "60"},
            )

        return await call_next(request)

    async def _check_redis(self, key: str, now: float, window_start: float) -> bool:
        try:
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, 120)
            results = await pipe.execute()
            count = results[1]
            return count < self._rpm
        except Exception:
            return self._check_local(key, now, window_start)

    def _check_local(self, key: str, now: float, window_start: float) -> bool:
        timestamps = self._local[key]
        self._local[key] = [t for t in timestamps if t > window_start]
        if len(self._local[key]) >= self._rpm:
            return False
        self._local[key].append(now)
        return True
