"""Single source of settings, pulled from env. Imported by the composition root only."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "verification"
    redis_url: str = "redis://localhost:6379/0"

    fetcher_port: int = 8001
    fetcher_mode: Literal["fixture", "voyager", "headless", "login"] = "fixture"
    fixtures_dir: Path = Path("./fixtures")

    linkedin_email: str = ""
    linkedin_password: str = ""
    linkedin_li_at: str = ""
    linkedin_jsessionid: str = ""
    linkedin_bcookie: str = ""
    linkedin_lidc: str = ""
    linkedin_daily_cap: int = 80
    linkedin_min_delay_ms: int = 2500
    linkedin_max_delay_ms: int = 12000
    linkedin_state_path: str = "linkedin_state.json"
    linkedin_playwright_profile_dir: str = ""
    linkedin_playwright_channel: str = ""

    accounts_file: str = "accounts.json"

    profile_hard_ttl_days: int = 90
    company_hard_ttl_days: int = 365
    capture_hard_ttl_days: int = 30

    redis_url: str = "redis://localhost:6379/0"
    rate_limit_rpm: int = 120
    accounts_file: str = "accounts.json"

    parser_version: str = "1.0.0"

    @property
    def has_voyager_credentials(self) -> bool:
        return bool(self.linkedin_li_at and self.linkedin_jsessionid)

    @property
    def has_li_at(self) -> bool:
        return bool(self.linkedin_li_at)

    @property
    def has_login_credentials(self) -> bool:
        return bool(self.linkedin_email and self.linkedin_password)


def load_settings() -> Settings:
    return Settings()
