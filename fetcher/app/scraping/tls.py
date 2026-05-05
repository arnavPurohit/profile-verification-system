"""TLS-spoofed HTTP session.

Bare requests/httpx have a JA3 fingerprint that LinkedIn flags on sight.
curl_cffi imitates real Chrome at the TLS and HTTP/2 layers (cipher suites,
extension order, ALPN, settings frames, header order).

This is layer 4 of the 9-layer scraping stack. Below this layer, no amount
of header tweaking will help — the connection is recognized as non-Chrome
before any headers are sent.
"""
from __future__ import annotations

from typing import Any

try:
    from curl_cffi.requests import AsyncSession
    HAVE_CURL_CFFI = True
except ImportError:
    AsyncSession = None  # type: ignore
    HAVE_CURL_CFFI = False


def build_chrome_session(
    cookies: dict[str, str] | None = None,
    user_agent: str | None = None,
    timeout: int = 20,
) -> Any:
    """Return an async HTTP session that fingerprints as recent Chrome on macOS.

    If curl_cffi is unavailable (e.g. minimal install), returns an httpx async
    client instead — works for development but will be flagged by LinkedIn at
    layer 4. Production must use curl_cffi.
    """
    if HAVE_CURL_CFFI:
        sess = AsyncSession(
            impersonate="chrome120",
            timeout=timeout,
        )
        if cookies:
            for k, v in cookies.items():
                sess.cookies.set(k, v, domain=".linkedin.com")
        if user_agent:
            sess.headers["user-agent"] = user_agent
        return sess

    # fallback for environments without curl_cffi
    import httpx

    return httpx.AsyncClient(
        cookies=cookies or {},
        headers={"user-agent": user_agent} if user_agent else None,
        timeout=timeout,
        http2=True,
    )
