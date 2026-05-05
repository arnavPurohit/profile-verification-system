"""Raw Voyager response → domain Company. Pure."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..domain import Company


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    for suffix in (
        ", inc.", " inc.", ", inc", " inc", ", llc", " llc", ", ltd", " ltd",
        " gmbh", " s.a.", " sa", " plc", " pvt", " pvt.", " private limited",
        " corp.", " corp", " co.", " corporation", " company",
    ):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return "".join(ch for ch in s if ch.isalnum() or ch == " ").strip().replace("  ", " ")


def parse_company(
    raw: dict[str, Any],
    *,
    parser_version: str = "1.0.0",
    source: str = "voyager",
    fetched_at: datetime | None = None,
) -> Company:
    fetched_at = fetched_at or datetime.now(timezone.utc)

    # Pre-parsed fixture shortcut
    if "name" in raw and "urn" in raw and "industry" in raw:
        normalized = raw.get("normalized") or _normalize_name(raw["name"])
        return Company(
            urn=raw["urn"],
            name=raw["name"],
            normalized=normalized,
            aliases=raw.get("aliases", []),
            website=raw.get("website"),
            industry=raw.get("industry"),
            size_band=raw.get("size_band"),
            hq=raw.get("hq"),
            parent_urn=raw.get("parent_urn"),
            fetched_at=fetched_at,
            source=raw.get("source", source),
            parser_version=raw.get("parser_version", parser_version),
        )

    # Voyager shape
    from .voyager_graph import collect, resolve_graph

    g = resolve_graph(raw)
    by_urn = g["_by_urn"]
    companies = collect(by_urn, "organization.Company") or collect(by_urn, "Company")
    if not companies:
        root = g["_root"]
        return Company(
            urn=root.get("entityUrn", "urn:unknown"),
            name=root.get("name", "Unknown"),
            normalized=_normalize_name(root.get("name", "")),
            fetched_at=fetched_at,
            source=source,
            parser_version=parser_version,
        )
    c = companies[0]
    name = c.get("name", "")
    return Company(
        urn=c.get("entityUrn", "urn:unknown"),
        name=name,
        normalized=_normalize_name(name),
        aliases=c.get("aliases", []),
        website=c.get("website") or (c.get("websiteUrl", {}) or {}).get("url"),
        industry=(c.get("industry") or [{}])[0].get("name") if isinstance(c.get("industry"), list) else c.get("industry"),
        size_band=c.get("staffCountRange") or c.get("companySize"),
        hq=(c.get("headquarter") or {}).get("city"),
        parent_urn=c.get("parentCompanyUrn"),
        fetched_at=fetched_at,
        source=source,
        parser_version=parser_version,
    )
