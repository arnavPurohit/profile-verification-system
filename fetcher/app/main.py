"""Composition root. The only file that constructs concrete classes."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from .config import Settings, load_settings
from .domain import Account
from .fetchers import FallbackFetcher, FixtureFetcher, HeadlessFetcher, LinkedInFetcher, LoginFetcher, PublicFetcher, VoyagerFetcher
from .middleware.rate_limit import RateLimitMiddleware
from .observability import configure_logging
from .pool.account_pool import AccountPool
from .pool.pool_fetcher import PooledFetcher
from .routes import captures as captures_routes
from .routes import fetch as fetch_routes
from .routes import health as health_routes
from .routes import search as search_routes
from .scraping.tls import build_chrome_session
from .services import CompanyService, ProfileService
from .storage import (
    CapturesRepo,
    CompaniesRepo,
    ProfilesRepo,
    RedisCache,
    build_mongo_db,
    ensure_indexes,
)

log = structlog.get_logger(__name__)


def _build_account(settings: Settings) -> Account:
    cookies = {
        "li_at": settings.linkedin_li_at,
        "JSESSIONID": settings.linkedin_jsessionid,
        "bcookie": settings.linkedin_bcookie,
        "lidc": settings.linkedin_lidc,
    }
    return Account(
        id="primary",
        cookies={k: v for k, v in cookies.items() if v},
        daily_cap=settings.linkedin_daily_cap,
    )


def _build_fetcher(settings: Settings) -> LinkedInFetcher:
    if settings.fetcher_mode == "fixture":
        return FixtureFetcher(settings.fixtures_dir)
    if settings.fetcher_mode == "voyager":
        if not settings.has_voyager_credentials:
            log.warning("voyager.no_credentials_falling_back_to_fixture")
            return FixtureFetcher(settings.fixtures_dir)
        account = _build_account(settings)
        session = build_chrome_session(cookies=account.cookies, user_agent=account.user_agent)
        return VoyagerFetcher(
            session=session,
            account=account,
            min_delay_ms=settings.linkedin_min_delay_ms,
            max_delay_ms=settings.linkedin_max_delay_ms,
        )
    if settings.fetcher_mode == "headless":
        if not settings.has_li_at:
            log.warning("headless.no_li_at_falling_back_to_fixture")
            return FixtureFetcher(settings.fixtures_dir)
        account = _build_account(settings)
        return HeadlessFetcher(account=account, headless=True)
    if settings.fetcher_mode == "login":
        accounts_path = Path(settings.accounts_file)
        if accounts_path.exists():
            pool, passwords = AccountPool.from_file_with_passwords(accounts_path)
            log.info("login.using_pool", accounts=pool.size, file=str(accounts_path))
            return PooledFetcher(pool=pool, passwords=passwords, settings=settings)

        if not settings.has_login_credentials:
            log.warning("login.no_credentials_falling_back_to_fixture")
            return FixtureFetcher(settings.fixtures_dir)
        account = _build_account(settings)
        account.email = settings.linkedin_email
        return LoginFetcher(
            account=account,
            email=settings.linkedin_email,
            password=settings.linkedin_password,
            state_path=settings.linkedin_state_path,
            headless=False,
            min_delay_ms=settings.linkedin_min_delay_ms,
            max_delay_ms=settings.linkedin_max_delay_ms,
            profile_dir=settings.linkedin_playwright_profile_dir or None,
            channel=settings.linkedin_playwright_channel or None,
        )
    if settings.fetcher_mode == "public":
        public = PublicFetcher(
            proxy=settings.public_proxy or None,
            delay_min_ms=settings.public_delay_min_ms,
            delay_max_ms=settings.public_delay_max_ms,
        )
        if settings.has_login_credentials:
            log.info("public.login_fallback_enabled")
            account = _build_account(settings)
            account.email = settings.linkedin_email
            login = LoginFetcher(
                account=account,
                email=settings.linkedin_email,
                password=settings.linkedin_password,
                state_path=settings.linkedin_state_path,
                headless=False,
                min_delay_ms=settings.linkedin_min_delay_ms,
                max_delay_ms=settings.linkedin_max_delay_ms,
                profile_dir=settings.linkedin_playwright_profile_dir or None,
                channel=settings.linkedin_playwright_channel or None,
            )
            return FallbackFetcher(public, login)
        return public
    raise ValueError(f"unknown FETCHER_MODE: {settings.fetcher_mode}")


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()
    db = build_mongo_db(settings.mongo_url, settings.mongo_db)

    profiles = ProfilesRepo(db)
    companies = CompaniesRepo(db)
    captures = CapturesRepo(db)
    fetcher = _build_fetcher(settings)

    redis: RedisCache | None = None
    if settings.redis_url:
        try:
            redis = RedisCache.from_url(settings.redis_url)
            log.info("redis.connected", url=settings.redis_url)
        except Exception:
            log.warning("redis.init_failed", exc_info=True)

    profile_svc = ProfileService(
        fetcher=fetcher, profiles=profiles, captures=captures,
        fresh_days=14, hard_ttl_days=settings.profile_hard_ttl_days,
        parser_version=settings.parser_version,
        redis=redis,
    )
    company_svc = CompanyService(
        fetcher=fetcher, companies=companies, captures=captures,
        fresh_days=60, hard_ttl_days=settings.company_hard_ttl_days,
        parser_version=settings.parser_version,
        redis=redis,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await ensure_indexes(
            db,
            profile_ttl_days=settings.profile_hard_ttl_days,
            company_ttl_days=settings.company_hard_ttl_days,
            capture_ttl_days=settings.capture_hard_ttl_days,
        )
        log.info("fetcher.started", mode=settings.fetcher_mode)
        yield
        if redis:
            await redis.close()

    app = FastAPI(title="Fetcher", lifespan=lifespan)
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=getattr(settings, "rate_limit_rpm", 120),
    )
    app.include_router(health_routes.build(settings))
    app.include_router(fetch_routes.build(profile_svc, company_svc))
    app.include_router(search_routes.build(company_svc))
    app.include_router(captures_routes.build(profiles, companies, captures, parser_version=settings.parser_version))
    return app


app = create_app()
