"""Login-based browser fetcher.

Logs into LinkedIn with Playwright (persistent Chromium profile), then loads
profile/company data via the **Voyager HTTP API** using ``BrowserContext.request``.

We intentionally do **not** ``page.goto`` public profile URLs like ``/in/...``:
Chromium often hits ``net::ERR_TOO_MANY_REDIRECTS`` there under automation even
when the account is valid. The API client reuses the same cookies as the
browser, so it stays consistent with a real session.

Optional: ``LINKEDIN_PLAYWRIGHT_CHANNEL=chrome`` to use installed Google Chrome.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from ..domain import Account
from ..scraping.headers import voyager_headers
from ..scraping.ratelimit import sleep_jittered
from .base import AccountChallengedError, FetcherError, LinkedInFetcher

log = structlog.get_logger(__name__)

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)
_DEBUG_DIR = "/tmp/login-fetcher-debug"
VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

_AUTH_WALL_PATTERNS = ("/login", "/authwall", "/checkpoint", "/challenge")

_INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    window.chrome = {runtime: {}};
    const orig = navigator.permissions.query;
    navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : orig(params);
"""


def _extract_slug_or_urn(input_str: str) -> tuple[str, bool]:
    """Return (identifier, is_urn). Mirrors VoyagerFetcher."""
    if input_str.startswith("urn:"):
        return input_str.split(":")[-1], True
    m = _SLUG_RE.search(input_str)
    if m:
        return m.group(1), False
    return input_str.strip("/"), False


def _is_auth_redirect(url: str) -> bool:
    return any(pat in url.lower() for pat in _AUTH_WALL_PATTERNS)


class LoginFetcher(LinkedInFetcher):
    """Playwright login + Voyager API via context.request (no /in/ page navigation)."""

    source = "login"

    def __init__(
        self,
        account: Account,
        email: str,
        password: str,
        state_path: str | Path = "linkedin_state.json",
        headless: bool = False,
        min_delay_ms: int = 2500,
        max_delay_ms: int = 12000,
        profile_dir: str | Path | None = None,
        channel: str | None = None,
    ):
        self._account = account
        self._email = email
        self._password = password
        self._state_path = Path(state_path).resolve()
        self._headless = headless
        self._min_delay_ms = min_delay_ms
        self._max_delay_ms = max_delay_ms
        if profile_dir:
            self._user_data_dir = Path(profile_dir).resolve()
        else:
            self._user_data_dir = (self._state_path.parent / ".linkedin-playwright-profile").resolve()
        ch = (channel or "").strip()
        self._playwright_channel: str | None = ch or None
        self._lock = asyncio.Lock()
        self._pw = None
        self._context = None
        self._stealth_fn = None
        self._logged_in = False

    async def _ensure_playwright(self) -> None:
        if self._pw is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        try:
            from playwright_stealth import stealth_async

            self._stealth_fn = stealth_async
        except ImportError:
            self._stealth_fn = None

    async def _launch_persistent_context(self) -> None:
        """Attach a Chromium profile directory (cookies + storage like a real browser)."""
        await self._ensure_playwright()
        assert self._pw is not None
        self._user_data_dir.mkdir(parents=True, exist_ok=True)

        pc_kw: dict[str, Any] = {
            "user_data_dir": str(self._user_data_dir),
            "headless": self._headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1440,900",
            ],
            "user_agent": self._account.user_agent,
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
            "timezone_id": self._account.timezone,
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
        }
        if self._playwright_channel:
            pc_kw["channel"] = self._playwright_channel

        self._context = await self._pw.chromium.launch_persistent_context(**pc_kw)
        if not self._stealth_fn:
            await self._context.add_init_script(_INIT_SCRIPT)

        log.info(
            "login.persistent_profile_ready",
            user_data=str(self._user_data_dir),
            channel=self._playwright_channel or "chromium-bundled",
        )

    async def _human_type(self, page: Any, selector: str, text: str) -> None:
        await page.click(selector)
        await page.wait_for_timeout(random.randint(200, 600))
        for char in text:
            await page.type(selector, char, delay=random.randint(50, 160))
        await page.wait_for_timeout(random.randint(300, 800))

    async def _validate_session_via_feed(self) -> bool:
        """Return True if the on-disk Chromium profile already has a signed-in LinkedIn session."""
        log.info("login.checking_saved_profile")
        assert self._context is not None
        page = await self._context.new_page()
        if self._stealth_fn:
            await self._stealth_fn(page)

        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(2000)
            final_url = page.url
            if _is_auth_redirect(final_url):
                log.warning("login.profile_session_invalid", url=final_url)
                return False
            log.info("login.session_valid", url=final_url)
            return True
        except Exception as e:
            log.warning("login.session_check_failed", error=str(e))
            return False
        finally:
            await page.close()

    async def _do_login(self) -> None:
        """Perform a full email/password login flow (uses persistent context)."""
        log.info("login.starting", email=self._email, headless=self._headless)

        if self._headless:
            log.warning(
                "login.headless_warning",
                msg="Headed login is safer for CAPTCHA/verification prompts.",
            )

        assert self._context is not None
        page = await self._context.new_page()
        if self._stealth_fn:
            await self._stealth_fn(page)

        try:
            await page.goto(
                "https://www.linkedin.com/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await page.wait_for_timeout(random.randint(1500, 3000))

            current = page.url
            if "/feed" in current:
                log.info("login.already_signed_in", url=current)
                await self._context.storage_state(path=str(self._state_path))
                log.info("login.success", storage_state_backup=str(self._state_path))
                await page.close()
                return

            if "/login" not in current and "/authwall" not in current:
                sign_in = page.locator('a[href*="login"], a[href*="signin"], a:has-text("Sign in")')
                if await sign_in.count() > 0:
                    await sign_in.first.click()
                    await page.wait_for_timeout(random.randint(1000, 2500))
                else:
                    await page.goto(
                        "https://www.linkedin.com/login",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )

            await page.wait_for_timeout(random.randint(800, 2000))

            current = page.url
            if "/feed" in current:
                log.info("login.already_signed_in_after_redirect", url=current)
                await self._context.storage_state(path=str(self._state_path))
                await page.close()
                return

            email_selector = "#username"
            await page.wait_for_selector(email_selector, timeout=15_000)
            await self._human_type(page, email_selector, self._email)

            await page.wait_for_timeout(random.randint(400, 1200))

            password_selector = "#password"
            await self._human_type(page, password_selector, self._password)

            await page.wait_for_timeout(random.randint(500, 1500))

            submit = page.locator(
                '[data-litms="login-submit"], '
                'button[type="submit"], '
                'button:has-text("Sign in")'
            )
            await submit.first.hover()
            await page.wait_for_timeout(random.randint(200, 600))
            await submit.first.click()

            log.info(
                "login.waiting_for_auth",
                msg="Complete any verification shown in the browser window.",
            )

            try:
                await page.wait_for_url("**/feed/**", timeout=120_000)
            except Exception:
                await page.wait_for_timeout(5000)
                final = page.url
                if _is_auth_redirect(final):
                    os.makedirs(_DEBUG_DIR, exist_ok=True)
                    await page.screenshot(path=f"{_DEBUG_DIR}/login_failed.png")
                    raise AccountChallengedError(
                        f"Login did not complete — stuck at {final}. "
                        f"Screenshot saved to {_DEBUG_DIR}/login_failed.png"
                    )
                log.info("login.landed_on_non_feed", url=final)

            await page.wait_for_timeout(random.randint(2000, 4000))
            await self._context.storage_state(path=str(self._state_path))
            log.info("login.success", storage_state_backup=str(self._state_path))

        except AccountChallengedError:
            raise
        except Exception as e:
            os.makedirs(_DEBUG_DIR, exist_ok=True)
            try:
                await page.screenshot(path=f"{_DEBUG_DIR}/login_error.png")
            except Exception:
                pass
            raise FetcherError(f"Login flow failed: {e}") from e
        finally:
            await page.close()

    async def _ensure_logged_in(self) -> None:
        if self._logged_in and self._context:
            return

        if self._context is None:
            await self._launch_persistent_context()

        assert self._context is not None

        restored = await self._validate_session_via_feed()
        if not restored:
            await self._do_login()

        self._logged_in = True

    async def _discard_session_and_relogin(self) -> None:
        log.warning("login.discarding_session_relogin")
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                log.warning("login.context_close_error", error=str(e))
            self._context = None

        self._logged_in = False

        try:
            self._state_path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("login.unlink_state_failed", error=str(e))

        shutil.rmtree(self._user_data_dir, ignore_errors=True)
        self._user_data_dir.mkdir(parents=True, exist_ok=True)

        await self._launch_persistent_context()
        await self._do_login()
        self._logged_in = True

    async def _jsession_value(self) -> str:
        assert self._context is not None
        cookies = await self._context.cookies("https://www.linkedin.com")
        for c in cookies:
            if c["name"] == "JSESSIONID":
                return c["value"]
        return ""

    async def _warm_cookie_jar_if_needed(self) -> None:
        """One feed visit so JSESSIONID exists for Voyager (should already after login)."""
        if await self._jsession_value():
            return
        assert self._context is not None
        page = await self._context.new_page()
        if self._stealth_fn:
            await self._stealth_fn(page)
        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(2000)
        finally:
            await page.close()

    async def _voyager_get(
        self,
        path: str,
        *,
        page_instance: str | None = None,
    ) -> dict[str, Any]:
        """GET Voyager using the browser context's cookie jar (no /in/ navigation)."""
        await self._ensure_logged_in()
        assert self._context is not None

        url = VOYAGER_BASE + path
        async with self._lock:
            await sleep_jittered(self._min_delay_ms, self._max_delay_ms)
            self._account.daily_used += 1
            self._account.last_used_at = datetime.now(timezone.utc)

            for attempt in range(2):
                await self._warm_cookie_jar_if_needed()
                jsession = await self._jsession_value()
                if not jsession:
                    raise FetcherError(
                        "No JSESSIONID in browser after login. "
                        f"Remove {self._user_data_dir} and sign in again."
                    )

                headers = dict(voyager_headers(jsession, page_instance=page_instance))
                headers["user-agent"] = self._account.user_agent

                try:
                    resp = await self._context.request.get(
                        url,
                        headers=headers,
                        timeout=90_000,
                        max_redirects=0,
                    )
                except Exception as e:
                    log.warning("login.voyager_request_error", error=str(e), attempt=attempt)
                    if attempt == 0:
                        await self._discard_session_and_relogin()
                        continue
                    raise FetcherError(f"Voyager request failed for {path}: {e}") from e

                status = resp.status
                if status == 200:
                    try:
                        return await resp.json()
                    except Exception as e:
                        raise FetcherError(f"Voyager response is not JSON for {path}: {e}") from e

                body_excerpt = (await resp.text())[:400].replace("\n", " ")

                if status in (401, 403) or 300 <= status < 400:
                    log.warning(
                        "login.voyager_auth_or_redirect",
                        status=status,
                        path=path,
                        excerpt=body_excerpt,
                        attempt=attempt,
                    )
                    if attempt == 0:
                        await self._discard_session_and_relogin()
                        continue
                    raise AccountChallengedError(
                        f"Voyager returned {status} for {path} — session rejected. {body_excerpt}"
                    )

                if status == 429:
                    raise FetcherError(f"Rate limited (429) on {path}")

                raise FetcherError(f"Voyager HTTP {status} on {path}: {body_excerpt}")

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        ident, _is_urn = _extract_slug_or_urn(urn_or_url)
        endpoints = [
            f"/identity/dash/profiles?q=memberIdentity&memberIdentity={ident}"
            "&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93",
            f"/identity/dash/profiles?q=memberIdentity&memberIdentity={ident}"
            "&decorationId=com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-19",
            f"/identity/profiles/{ident}/profileView",
        ]
        last_err: FetcherError | None = None
        for path in endpoints:
            try:
                data = await self._voyager_get(path)
                if isinstance(data, dict) and data.get("data", {}).get("status") in (410, 404):
                    continue
                return data
            except FetcherError as e:
                last_err = e
                if "410" in str(e) or "404" in str(e):
                    continue
                raise
        raise last_err or FetcherError(f"All Voyager profile endpoints failed for {ident}")

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Company search is not used by the verification pipeline (company
        resolution is done locally from profile experience data). Kept as a
        no-op to satisfy the LinkedInFetcher protocol."""
        return []

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        ident = urn.split(":")[-1] if urn.startswith("urn:") else urn
        path = f"/organization/companies/{ident}"
        return await self._voyager_get(path)

    async def close(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
