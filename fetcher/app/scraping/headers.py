"""Voyager-specific headers. Captured from a real browser session.

Header order matters (HTTP/2 fingerprinting). curl_cffi preserves it; httpx
does not. The dict insertion order here is the wire order.
"""
from __future__ import annotations

import json
from urllib.parse import quote


def derive_csrf_token(jsessionid_cookie: str) -> str:
    """LinkedIn's csrf-token is the JSESSIONID value with surrounding quotes stripped.

    Example: cookie 'JSESSIONID="ajax:1234567890"' → header 'csrf-token: ajax:1234567890'.
    """
    return jsessionid_cookie.strip('"')


def voyager_headers(jsessionid: str, page_instance: str | None = None) -> dict[str, str]:
    csrf = derive_csrf_token(jsessionid)
    page_instance = page_instance or "urn:li:page:d_flagship3_profile_view_base;abc123"
    track = json.dumps(
        {
            "clientVersion": "1.13.20000",
            "mpVersion": "1.13.20000",
            "osName": "web",
            "timezoneOffset": -8,
            "timezone": "America/Los_Angeles",
            "deviceFormFactor": "DESKTOP",
            "mpName": "voyager-web",
            "displayDensity": 2,
            "displayWidth": 2880,
            "displayHeight": 1800,
        },
        separators=(",", ":"),
    )
    return {
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "accept-language": "en-US,en;q=0.9",
        "csrf-token": csrf,
        "x-li-lang": "en_US",
        "x-li-track": track,
        "x-li-page-instance": page_instance,
        "x-restli-protocol-version": "2.0.0",
        "referer": "https://www.linkedin.com/",
    }


def encode_decoration(fields: list[str]) -> str:
    """Voyager uses a 'decoration' DSL to specify which sub-fields of a graph to inline.
    For our purposes a stable opinionated decoration is fine."""
    return quote(",".join(fields), safe="(),")
