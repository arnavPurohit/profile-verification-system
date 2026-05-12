from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EmploymentType = Literal[
    "full_time", "part_time", "contractor", "advisor",
    "intern", "freelance", "self_employed", "unknown",
]


class Experience(BaseModel):
    company_urn: str | None = None
    company_name: str
    title: str
    start: str | None = None
    end: str | None = None
    is_current: bool = False
    employment_type: EmploymentType = "unknown"
    location: str | None = None
    description: str | None = None


class Profile(BaseModel):
    urn: str
    public_id: str | None = None
    url: str | None = None
    name: str
    headline: str | None = None
    location: str | None = None
    experience: list[Experience] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    profile_last_updated: datetime | None = None
    fetched_at: datetime
    source: Literal["voyager", "extension", "fixture", "login", "public"] = "fixture"
    parser_version: str = "1.0.0"
