from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Company(BaseModel):
    urn: str
    name: str
    normalized: str
    aliases: list[str] = Field(default_factory=list)
    website: str | None = None
    industry: str | None = None
    size_band: str | None = None
    hq: str | None = None
    parent_urn: str | None = None
    fetched_at: datetime
    source: Literal["voyager", "extension", "fixture"] = "fixture"
    parser_version: str = "1.0.0"
