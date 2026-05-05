from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from ..domain import Account
from ..fetchers.base import FetcherUnavailableError

log = structlog.get_logger(__name__)

_COOLDOWN_MINUTES = 15


class AccountPool:
    def __init__(self, accounts: list[Account], *, source: str = "accounts.json"):
        self._accounts = accounts
        self._source = source
        self._lock = asyncio.Lock()

    @classmethod
    def from_file(cls, path: str | Path) -> AccountPool:
        path = Path(path)
        raw: list[dict[str, Any]] = json.loads(path.read_text())
        accounts = [
            Account(
                id=f"pool-{i}",
                email=entry["email"],
                daily_cap=entry.get("daily_cap", 80),
                state="active",
            )
            for i, entry in enumerate(raw)
        ]
        log.info("pool.loaded", count=len(accounts), source=str(path))
        return cls(accounts, source=str(path))

    @classmethod
    def from_file_with_passwords(cls, path: str | Path) -> tuple[AccountPool, dict[str, str]]:
        path = Path(path)
        raw: list[dict[str, Any]] = json.loads(path.read_text())
        accounts: list[Account] = []
        passwords: dict[str, str] = {}
        for i, entry in enumerate(raw):
            acct_id = f"pool-{i}"
            accounts.append(
                Account(
                    id=acct_id,
                    email=entry["email"],
                    daily_cap=entry.get("daily_cap", 80),
                    state="active",
                )
            )
            passwords[acct_id] = entry["password"]
        log.info("pool.loaded", count=len(accounts), source=str(path))
        return cls(accounts, source=str(path)), passwords

    @property
    def size(self) -> int:
        return len(self._accounts)

    async def acquire(self) -> Account:
        async with self._lock:
            now = datetime.now(timezone.utc)
            best: Account | None = None
            best_ratio = float("inf")

            for acct in self._accounts:
                if acct.state in ("challenged", "suspended"):
                    continue
                if acct.state == "cooldown" and acct.last_challenge_at:
                    cooldown_until = acct.last_challenge_at + timedelta(minutes=_COOLDOWN_MINUTES)
                    if now < cooldown_until:
                        continue
                    acct.state = "active"

                if acct.daily_used >= acct.daily_cap:
                    continue

                ratio = acct.daily_used / acct.daily_cap if acct.daily_cap > 0 else float("inf")
                if ratio < best_ratio:
                    best_ratio = ratio
                    best = acct

            if best is None:
                raise FetcherUnavailableError(
                    f"No accounts available in pool ({self._source}, size={len(self._accounts)})"
                )

            log.debug(
                "pool.acquired",
                account=best.id,
                email=best.email,
                used=best.daily_used,
                cap=best.daily_cap,
            )
            return best

    async def release(self, account: Account, *, success: bool, error: str | None = None) -> None:
        async with self._lock:
            if success:
                account.daily_used += 1
                account.last_used_at = datetime.now(timezone.utc)
                return

            if error is None:
                return

            err_lower = error.lower()
            if "rate" in err_lower or "429" in err_lower:
                account.state = "cooldown"
                account.last_challenge_at = datetime.now(timezone.utc)
                log.warning("pool.cooldown", account=account.id, reason=error)
            elif "challenge" in err_lower or "captcha" in err_lower:
                account.state = "challenged"
                account.last_challenge_at = datetime.now(timezone.utc)
                log.warning("pool.challenged", account=account.id, reason=error)
            else:
                log.warning("pool.error", account=account.id, reason=error)

    async def reset_daily_counts(self) -> None:
        async with self._lock:
            for acct in self._accounts:
                acct.daily_used = 0
            log.info("pool.daily_reset", count=len(self._accounts))
