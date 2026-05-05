"""Resolve Voyager's normalized graph format.

Voyager returns JSON like:
  {
    "data": { "*element": "urn:li:fsd_profile:abc" },
    "included": [
      { "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
        "entityUrn": "urn:li:fsd_profile:abc", "firstName": "...", ... },
      { "$type": "com.linkedin.voyager.dash.organization.Company",
        "entityUrn": "urn:li:fsd_company:1234", ... },
      ...
    ]
  }

References between objects are by URN. This module walks the graph and
resolves references inline so downstream parsers can ignore the indirection.

Pure function — no IO, no global state.
"""
from __future__ import annotations

from typing import Any


def resolve_graph(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a dict mapping URN → object, plus the entry point under '_root'."""
    included = raw.get("included", []) or []
    by_urn: dict[str, dict[str, Any]] = {}
    for obj in included:
        urn = obj.get("entityUrn") or obj.get("*entityUrn")
        if urn:
            by_urn[urn] = obj

    data = raw.get("data", raw)
    return {"_root": data, "_by_urn": by_urn}


def follow(by_urn: dict[str, Any], ref: Any) -> Any:
    """Resolve a reference into the included graph. Returns ref unchanged if
    it isn't a URN string we know."""
    if isinstance(ref, str) and ref.startswith("urn:") and ref in by_urn:
        return by_urn[ref]
    return ref


def collect(by_urn: dict[str, Any], type_suffix: str) -> list[dict[str, Any]]:
    """Return all included objects whose $type ends with the given suffix."""
    return [
        obj for obj in by_urn.values()
        if isinstance(obj.get("$type"), str) and obj["$type"].endswith(type_suffix)
    ]
