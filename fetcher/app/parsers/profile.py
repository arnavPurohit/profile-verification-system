"""Raw Voyager response → domain Profile.

Robustness rules:
  - Missing fields default sensibly (None or empty list), never crash.
  - Unknown employment_type → "unknown"; we surface it, don't drop it.
  - Every parsed Profile records parser_version so we can re-parse later.

Pure function. No IO.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ..domain import Experience, Profile

EMPLOYMENT_TYPE_MAP = {
    "FULL_TIME": "full_time",
    "PART_TIME": "part_time",
    "CONTRACT": "contractor",
    "FREELANCE": "freelance",
    "INTERNSHIP": "intern",
    "SELF_EMPLOYED": "self_employed",
    "APPRENTICESHIP": "intern",
}


def _date_str(d: dict[str, Any] | None) -> str | None:
    if not d:
        return None
    y = d.get("year")
    m = d.get("month")
    if y and m:
        return f"{y:04d}-{m:02d}"
    if y:
        return f"{y:04d}"
    return None


def _is_current(end: dict[str, Any] | None, today: date | None = None) -> bool:
    if not end:
        return True
    today = today or datetime.now(timezone.utc).date()
    y = end.get("year")
    m = end.get("month") or 12
    if not y:
        return True
    end_date = date(y, m, 1)
    return end_date >= today.replace(day=1)


def _employment_type(raw: str | None) -> str:
    if not raw:
        return "unknown"
    return EMPLOYMENT_TYPE_MAP.get(raw.upper(), "unknown")


def _heuristic_advisor(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(k in t for k in ("advisor", "advising", "board member", "investor"))


def parse_profile(
    raw: dict[str, Any],
    *,
    parser_version: str = "1.0.0",
    source: str = "voyager",
    fetched_at: datetime | None = None,
) -> Profile:
    """Tolerant parser. Accepts Voyager's variety of profile shapes.

    Supports two input formats:
      - Voyager 'profileView' shape (data + included graph)
      - Pre-parsed fixture shape (already a Profile dict)
    """
    fetched_at = fetched_at or datetime.now(timezone.utc)

    # Pre-parsed fixture shortcut — fixtures may store domain-shaped objects directly.
    if "experience" in raw and isinstance(raw.get("experience"), list) and "name" in raw:
        return Profile(
            urn=raw.get("urn") or raw["public_id"],
            public_id=raw.get("public_id"),
            url=raw.get("url"),
            name=raw["name"],
            headline=raw.get("headline"),
            location=raw.get("location"),
            experience=[Experience(**e) for e in raw["experience"]],
            education=raw.get("education", []),
            profile_last_updated=_parse_dt(raw.get("profile_last_updated")),
            fetched_at=_parse_dt(raw.get("fetched_at")) or fetched_at,
            source=raw.get("source", source),
            parser_version=raw.get("parser_version", parser_version),
        )

    # Voyager-shape parse
    from .voyager_graph import collect, resolve_graph

    g = resolve_graph(raw)
    by_urn = g["_by_urn"]

    profile_objs = collect(by_urn, ".identity.profile.Profile") or collect(by_urn, "Profile")
    if not profile_objs:
        # last-resort: assume root has the fields
        root = g["_root"]
        first_name = root.get("firstName", "")
        last_name = root.get("lastName", "")
        return Profile(
            urn=root.get("entityUrn", "urn:unknown"),
            public_id=root.get("publicIdentifier"),
            name=f"{first_name} {last_name}".strip() or "Unknown",
            headline=root.get("headline"),
            location=root.get("locationName"),
            experience=[],
            fetched_at=fetched_at,
            source=source,
            parser_version=parser_version,
        )

    p = profile_objs[0]
    first_name = p.get("firstName", "")
    last_name = p.get("lastName", "")
    name = f"{first_name} {last_name}".strip() or p.get("name") or "Unknown"

    experiences: list[Experience] = []
    seen_urns: set[str] = set()
    all_positions = collect(by_urn, ".identity.profile.Position") + collect(by_urn, "Position")
    for pos in all_positions:
        pos_urn = pos.get("entityUrn")
        if pos_urn:
            if pos_urn in seen_urns:
                continue
            seen_urns.add(pos_urn)
        company_urn = pos.get("companyUrn") or pos.get("*company")
        company_name = pos.get("companyName") or pos.get("company", {}).get("name", "")
        title = pos.get("title", "")
        start = pos.get("dateRange", {}).get("start") if isinstance(pos.get("dateRange"), dict) else pos.get("startDate")
        end = pos.get("dateRange", {}).get("end") if isinstance(pos.get("dateRange"), dict) else pos.get("endDate")
        is_current = _is_current(end)
        emp_type = _employment_type(pos.get("employmentType") or pos.get("employmentTypeUrn"))
        if emp_type == "unknown" and _heuristic_advisor(title):
            emp_type = "advisor"

        experiences.append(
            Experience(
                company_urn=company_urn,
                company_name=company_name or "Unknown",
                title=title,
                start=_date_str(start),
                end=_date_str(end),
                is_current=is_current,
                employment_type=emp_type,
                location=pos.get("locationName"),
                description=pos.get("description"),
            )
        )

    return Profile(
        urn=p.get("entityUrn", "urn:unknown"),
        public_id=p.get("publicIdentifier"),
        url=f"https://www.linkedin.com/in/{p['publicIdentifier']}" if p.get("publicIdentifier") else None,
        name=name,
        headline=p.get("headline"),
        location=p.get("locationName") or p.get("geoLocationName"),
        experience=experiences,
        education=collect(by_urn, "Education"),
        profile_last_updated=_parse_dt(p.get("lastModified") or p.get("profileLastUpdatedAt")),
        fetched_at=fetched_at,
        source=source,
        parser_version=parser_version,
    )


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        # Voyager uses millisecond epochs
        seconds = v / 1000.0 if v > 1e12 else v
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
