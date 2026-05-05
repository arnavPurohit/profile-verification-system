"""Headless-browser fetcher.

Navigates to the profile page using the account's cookies and intercepts
the Voyager XHRs the page makes during its own load. We don't need to
guess endpoints — whatever modern paths LinkedIn currently uses, the
page hits them, and we observe the responses.

This is the "browser is the user-agent" pattern: server-side Playwright
runs as a real Chromium with real cookies, exactly the same way a logged-in
user's browser would. Fingerprint and behavior are still Playwright, so
this is more detectable than the extension path — but it works without
the user having to do anything except paste a URL.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import structlog

from ..domain import Account
from .base import (
    AccountChallengedError,
    FetcherError,
    FetcherUnavailableError,
    LinkedInFetcher,
)

log = structlog.get_logger(__name__)

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)
# Broad: capture every Voyager XHR. We pick the largest as the main profile
# payload after the page settles. Modern LinkedIn uses /voyager/api/graphql
# alongside REST paths and we don't want to guess which one carries data.
_VOYAGER_PROFILE_RE = re.compile(r"/voyager/api/", re.I)
_VOYAGER_COMPANY_RE = re.compile(r"/voyager/api/", re.I)
_DEBUG_DIR = "/tmp/headless-debug"


def _slug_of(input_str: str) -> str:
    if input_str.startswith("urn:"):
        return input_str.split(":")[-1]
    m = _SLUG_RE.search(input_str)
    if m:
        return m.group(1)
    return input_str.strip("/")


class HeadlessFetcher(LinkedInFetcher):
    source = "voyager"

    def __init__(self, account: Account, headless: bool = True):
        self._account = account
        self._headless = headless
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._context = None
        self._stealth = None

    async def _ensure_context(self) -> None:
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-blink-features=AutomationControlled",
                "--ignore-certificate-errors",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--window-size=1440,900",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=self._account.user_agent,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id=self._account.timezone,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
        )
        # playwright-stealth hides all automation fingerprints LinkedIn checks
        try:
            from playwright_stealth import stealth_async as _stealth_async
            self._stealth = _stealth_async
        except ImportError:
            self._stealth = None
            # Fallback: manual overrides for the most obvious tells
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = {runtime: {}};
                const orig = navigator.permissions.query;
                navigator.permissions.query = (params) =>
                    params.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : orig(params);
            """)
        cookies = [
            {"name": k, "value": v, "domain": ".linkedin.com", "path": "/"}
            for k, v in self._account.cookies.items()
            if v
        ]
        if cookies:
            await self._context.add_cookies(cookies)
        log.info("headless.context_ready", cookie_count=len(cookies))

    async def _check_account_health(self) -> None:
        if self._account.state in {"suspended", "retired"}:
            raise FetcherUnavailableError(f"account {self._account.id} is {self._account.state}")
        if self._account.state == "challenged":
            raise AccountChallengedError(f"account {self._account.id} pending CAPTCHA")
        if self._account.daily_used >= self._account.daily_cap:
            raise FetcherUnavailableError(
                f"account {self._account.id} hit daily cap "
                f"({self._account.daily_used}/{self._account.daily_cap})"
            )

    async def _capture_from_url(self, page_url: str, match_re: re.Pattern) -> dict[str, Any]:
        import os

        await self._check_account_health()
        await self._ensure_context()
        os.makedirs(_DEBUG_DIR, exist_ok=True)

        async with self._lock:
            self._account.daily_used += 1
            self._account.last_used_at = datetime.now(timezone.utc)
            page = await self._context.new_page()
            if self._stealth:
                await self._stealth(page)
            captures: list[dict[str, Any]] = []
            seen_urls: list[tuple[int, str]] = []  # (status, url) for diagnostics

            async def on_response(response):
                u = response.url
                if not match_re.search(u):
                    return
                seen_urls.append((response.status, u))
                if response.status != 200:
                    return
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype:
                    return
                try:
                    data = await response.json()
                    captures.append({"url": u, "data": data})
                except Exception as e:
                    log.warning("headless.json_parse_failed", url=u, error=str(e))

            page.on("response", on_response)
            try:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)

                final_url = page.url
                # Save diagnostics regardless of success.
                slug_for_file = re.sub(r"[^A-Za-z0-9_-]", "_", page_url)[-80:]
                shot_path = f"{_DEBUG_DIR}/{slug_for_file}.png"
                html_path = f"{_DEBUG_DIR}/{slug_for_file}.html"
                try:
                    await page.screenshot(path=shot_path, full_page=False)
                    html = await page.content()
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(html)
                except Exception as e:
                    log.warning("headless.diagnostics_save_failed", error=str(e))

                log.info(
                    "headless.page_settled",
                    requested=page_url,
                    final=final_url,
                    voyager_xhrs=len(seen_urls),
                    captured=len(captures),
                    sample_xhrs=[u for _, u in seen_urls[:5]],
                    screenshot=shot_path,
                )

                if "checkpoint" in final_url or "/login" in final_url or "/authwall" in final_url:
                    self._account.state = "challenged"
                    raise AccountChallengedError(
                        f"redirected to {final_url} — cookies likely invalid"
                    )
            except AccountChallengedError:
                raise
            except Exception as e:
                raise FetcherError(f"page load failed for {page_url}: {e}") from e
            finally:
                await page.close()

        if not captures:
            raise FetcherError(
                f"no Voyager XHR captured at {page_url}; "
                f"saw {len(seen_urls)} matching URLs total. See {_DEBUG_DIR} inside the container."
            )

        main = max(captures, key=lambda c: len(str(c["data"])))
        log.info("headless.capture_selected", url=main["url"], size=len(str(main["data"])))
        return main["data"]

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        slug = _slug_of(urn_or_url)
        url = f"https://www.linkedin.com/in/{slug}/"
        return await self._capture_from_url(url, _VOYAGER_PROFILE_RE)

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        # Stub: build one candidate from the query string. The profile parser
        # provides company URNs from the experience array, so the matcher's
        # fuzzy-name path lights up without a real LinkedIn search round-trip.
        # Production would hit /voyager/api/typeahead/hits.
        norm = re.sub(r"[^a-z0-9 ]", "", query.lower()).strip()
        return [
            {
                "urn": f"urn:li:fsd_company:unresolved_{norm.replace(' ', '_')}",
                "name": query,
                "normalized": norm,
                "aliases": [],
                "website": None,
                "industry": None,
                "size_band": None,
                "hq": None,
                "parent_urn": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "headless_stub",
                "parser_version": "1.0.0",
            }
        ][:limit]

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        ident = urn.split(":")[-1] if urn.startswith("urn:") else urn
        url = f"https://www.linkedin.com/company/{ident}/"
        return await self._capture_from_url(url, _VOYAGER_COMPANY_RE)

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
