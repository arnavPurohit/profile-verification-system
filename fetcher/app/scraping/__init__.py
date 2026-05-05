"""Generic scraping primitives. No LinkedIn-specific logic — these are reusable
for any scraper. The generic_fetcher demo proves it by using these against a
non-LinkedIn target.
"""
from .ratelimit import TokenBucket, jittered_delay
from .retry import classify_error, RetryPolicy
from .tls import build_chrome_session
from .headers import voyager_headers

__all__ = [
    "TokenBucket",
    "jittered_delay",
    "classify_error",
    "RetryPolicy",
    "build_chrome_session",
    "voyager_headers",
]
