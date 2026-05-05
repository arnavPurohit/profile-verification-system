from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AccountState = Literal["warming", "active", "cooled", "challenged", "suspended", "retired"]


class Account(BaseModel):
    """Represents one LinkedIn identity. In the take-home there is exactly one;
    in production the pool would have many. Same shape regardless."""

    id: str
    email: str | None = None
    cookies: dict[str, str] = Field(default_factory=dict)
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    timezone: str = "America/Los_Angeles"
    daily_cap: int = 80
    daily_used: int = 0
    state: AccountState = "active"
    last_challenge_at: datetime | None = None
    last_used_at: datetime | None = None
