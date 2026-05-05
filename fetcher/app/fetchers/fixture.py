"""Reads pre-captured Voyager JSON from disk. Used by tests and offline demos.

Liskov: indistinguishable from VoyagerFetcher to any caller. Same input shape,
same output shape, same errors for missing entities.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import FetcherError, LinkedInFetcher


_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)


def _profile_key(urn_or_url: str) -> str:
    """Reduce a URL or URN to the fixture filename slug."""
    if urn_or_url.startswith("urn:"):
        return urn_or_url.split(":")[-1]
    m = _SLUG_RE.search(urn_or_url)
    if m:
        return m.group(1)
    return urn_or_url.strip("/").lower()


class FixtureFetcher(LinkedInFetcher):
    def __init__(self, fixtures_dir: Path):
        self._dir = Path(fixtures_dir)
        self._profiles_dir = self._dir / "profiles"
        self._companies_dir = self._dir / "companies"

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        key = _profile_key(urn_or_url)
        path = self._profiles_dir / f"{key}.json"
        if not path.exists():
            raise FetcherError(f"no fixture for profile '{key}' at {path}")
        with path.open() as f:
            return json.load(f)

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        norm = query.strip().lower().replace(" ", "_")
        path = self._companies_dir / f"_search_{norm}.json"
        if not path.exists():
            # fallback: synthesize from any matching company file
            results = []
            for fp in self._companies_dir.glob("*.json"):
                if fp.name.startswith("_search_"):
                    continue
                with fp.open() as f:
                    data = json.load(f)
                names = [data.get("name", ""), *data.get("aliases", [])]
                if any(query.lower() in n.lower() for n in names if n):
                    results.append(data)
                    if len(results) >= limit:
                        break
            return results
        with path.open() as f:
            return json.load(f)[:limit]

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        key = urn.split(":")[-1] if urn.startswith("urn:") else urn
        path = self._companies_dir / f"{key}.json"
        if not path.exists():
            raise FetcherError(f"no fixture for company '{key}' at {path}")
        with path.open() as f:
            return json.load(f)
