"""Public (no-login) LinkedIn fetcher.

Two-stage strategy, no login required:

1. Try LinkedIn directly with a Googlebot UA (works for high-profile public
   figures like Satya Nadella whose pages are crawled and indexed).
2. Fall back to DuckDuckGo HTML search — parse the search snippet for the
   matching linkedin.com/in/<slug> result. Works for any profile that appears
   in search results, including regular users.

No Playwright, no session cookies, no login.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json as _json
import random
import re
from datetime import datetime, timezone
from typing import Any

import structlog

from ..scraping.ratelimit import sleep_jittered
from ..scraping.tls import build_chrome_session
from .base import FetcherError, LinkedInFetcher

log = structlog.get_logger(__name__)

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)
_AUTH_WALL = ("/login", "/authwall", "/checkpoint", "/challenge")

_GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# Rotate across realistic desktop UAs so repeated requests look like different users.
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.9,hi;q=0.8",
    "en-US,en;q=0.8",
]

def _random_headers(referer: str | None = None) -> dict[str, str]:
    ua = random.choice(_USER_AGENTS)
    h = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


def _slug_of(value: str) -> str:
    if value.startswith("urn:"):
        return value.split(":")[-1]
    m = _SLUG_RE.search(value)
    return m.group(1) if m else value.strip("/")


# ── Stage 1: LinkedIn direct (Googlebot UA) ───────────────────────────────────

def _parse_og(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for prop, content in re.findall(
        r'<meta[^>]+property=["\']og:(\w+)["\'][^>]+content=["\']([^"\']*)["\']',
        html, re.I,
    ):
        out[prop] = content
    return out


def _parse_name_and_headline(og_title: str) -> tuple[str, str]:
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", og_title).strip()
    parts = title.split(" - ", 1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (title, "")


def _parse_company_and_location(og_desc: str) -> tuple[str, str]:
    company = location = ""
    m = re.search(r"Experience:\s*([^·\n]+)", og_desc)
    if m:
        company = m.group(1).strip()
    m = re.search(r"Location:\s*([^·\n]+)", og_desc)
    if m:
        location = m.group(1).strip()
    return company, location


# ── JSON-LD structured data ───────────────────────────────────────────────────

def _parse_json_ld(html: str) -> dict[str, str]:
    """Extract company/location from LinkedIn's embedded schema.org/Person JSON-LD."""
    out: dict[str, str] = {}
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        try:
            data = _json.loads(m.group(1))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "Person":
            continue
        works_for = data.get("worksFor")
        if isinstance(works_for, dict):
            out["company"] = works_for.get("name", "")
        elif isinstance(works_for, list) and works_for:
            out["company"] = works_for[0].get("name", "")
        addr = data.get("address", {})
        if isinstance(addr, dict) and addr.get("addressLocality"):
            out["location"] = addr["addressLocality"]
        if data.get("jobTitle"):
            out["jobTitle"] = data["jobTitle"]
    return out


def _parse_html_company(html: str) -> str:
    """
    Last-resort: scrape company name from LinkedIn's public HTML.
    LinkedIn changes class names often; we try several known patterns.
    """
    patterns = [
        # Experience section company subtitle (most reliable when visible)
        r'experience-item__subtitle[^>]*>\s*(.*?)\s*</[a-z]+>',
        # Top-card first subline (company tag on logged-out profile cards)
        r'top-card-layout__first-subline[^>]*>\s*(.*?)\s*</[a-z]+>',
        r'top-card__subline-item[^>]*>\s*(.*?)\s*</[a-z]+>',
        # Sidebar entity (the "Workday" badge visible in the screenshot)
        r'primary-subtitle[^>]*>\s*(.*?)\s*</[a-z]+>',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.S | re.I)
        if m:
            text = html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
            if text and len(text) < 120:
                return text
    return ""


# ── Stage 2: Yahoo search snippet ─────────────────────────────────────────────

def _yahoo_search(html: str, target_slug: str) -> dict[str, str] | None:
    """
    Yahoo wraps result URLs in redirect links like:
      RU=https%3a%2f%2fin.linkedin.com%2fin%2fumang-sinha/
    Find the redirect block for the exact slug, then grab the title and
    snippet from the 2000-char window that follows it.
    """
    slug_lower = target_slug.lower()
    # URL-encoded form: in%2fumang-sinha (note: %2f = /)
    needles = [
        f"linkedin.com%2fin%2f{slug_lower}/",  # Yahoo redirect URL encoded
        f"linkedin.com/in/{slug_lower}",        # plain (display URL)
    ]

    idx = -1
    for needle in needles:
        idx = html.lower().find(needle)
        if idx != -1:
            break

    if idx == -1:
        return None

    # look forward up to 2000 chars for title + snippet
    window = html[idx: idx + 2000]

    title_m = re.search(r'class="[^"]*fz-20[^"]*"[^>]*>(.*?)</span>', window, re.S)
    snip_m  = re.search(r'class="[^"]*fc-dustygray[^"]*"[^>]*>(.*?)</p', window, re.S)

    if not title_m:
        return None

    title = html_lib.unescape(re.sub(r'<[^>]+>', '', title_m.group(1))).strip()
    snip  = html_lib.unescape(re.sub(r'<[^>]+>', '', snip_m.group(1) if snip_m else "")).strip()
    return {"title": title, "snippet": snip}


def _parse_search_result(title: str, snippet: str) -> tuple[str, str, str, str]:
    """
    title:   "Umang Sinha - SDE 2 @Vahan.ai (YC'19) | BITS Pilani'23 | LinkedIn"
    snippet: "... Experience: Vahan.ai · Location: Greater Bengaluru Area · ..."
    Returns: (name, headline, company, location)
    """
    clean = re.sub(r"\s*[|\-–]\s*LinkedIn\s*$", "", title, flags=re.I).strip()
    parts = clean.split(" - ", 1)
    name     = parts[0].strip()
    headline = parts[1].strip() if len(parts) > 1 else clean

    company = location = ""
    m = re.search(r"Experience:\s*([^·\n]+)", snippet)
    if m:
        company = m.group(1).strip()
    m = re.search(r"Location:\s*([^·\n]+)", snippet)
    if m:
        location = m.group(1).strip()

    return name, headline, company, location


# ── Shared result builder ─────────────────────────────────────────────────────

def _build_result(slug: str, name: str, headline: str, company: str, location: str, source: str) -> dict[str, Any]:
    """
    Return a fixture-shaped dict so parse_profile's fast-path triggers and
    the experience entry is preserved through to the verifier pipeline.
    """
    experience: list[dict[str, Any]] = []
    # LinkedIn og:title is often "Name - Company | LinkedIn", so headline == company name
    # when og:description lacks an explicit "Experience:" field.
    effective_company = company or headline
    if effective_company:
        experience.append({
            "company_urn": None,
            "company_name": effective_company,
            "title": headline,
            "start": None,
            "end": None,
            "is_current": True,
            "employment_type": "unknown",
            "location": location,
            "description": None,
        })
    return {
        # fixture-shape: parse_profile picks this up via the "experience" + "name" shortcut
        "name": name or slug,
        "public_id": slug,
        "urn": f"urn:li:member:{slug}",
        "url": f"https://www.linkedin.com/in/{slug}",
        "headline": headline,
        "location": location,
        "experience": experience,
        "education": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "public",
        "parser_version": "1.0.0",
    }


# ── Fetcher ───────────────────────────────────────────────────────────────────

class PublicFetcher(LinkedInFetcher):
    """No-login fetcher: Googlebot UA → LinkedIn direct, fallback to Yahoo search.

    Anti-detection measures built in:
    - Log-normal jittered delay (30–60 s) before each live HTTP request
    - User-agent rotation across 6 realistic desktop strings
    - Randomised Accept-Language and referer headers per request
    - Optional proxy (any http/https/socks5 URL)
    """

    source = "public"

    def __init__(
        self,
        proxy: str | None = None,
        delay_min_ms: int = 30_000,
        delay_max_ms: int = 60_000,
    ) -> None:
        self._proxy = proxy
        self._delay_min_ms = delay_min_ms
        self._delay_max_ms = delay_max_ms
        self._session = build_chrome_session()

    async def _get(self, url: str, headers: dict, **kwargs) -> Any:
        """Shared request wrapper: delay → get (with optional proxy)."""
        log.info("public.delay", min_s=self._delay_min_ms // 1000, max_s=self._delay_max_ms // 1000)
        await sleep_jittered(self._delay_min_ms, self._delay_max_ms)
        kw: dict[str, Any] = {"headers": headers, "allow_redirects": True, **kwargs}
        if self._proxy:
            kw["proxy"] = self._proxy
        return await self._session.get(url, **kw)

    async def _warm_up_session(self) -> None:
        """
        Visit linkedin.com homepage first to get li_sugr/bcookie/lidc cookies.
        These cookies signal a returning browser — without them LinkedIn's CDN
        returns 999 for profiles that aren't crawlable by Googlebot.
        """
        try:
            ua = random.choice(_USER_AGENTS)
            await self._session.get(
                "https://www.linkedin.com/",
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Upgrade-Insecure-Requests": "1",
                },
                allow_redirects=True,
                timeout=10,
            )
            log.info("public.session_warmed")
        except Exception as e:
            log.warning("public.warmup_failed", error=str(e))

    def _extract_profile_data(self, html: str) -> tuple[str, str, str, str]:
        """Pull name/headline/company/location from HTML using all available methods."""
        og = _parse_og(html)
        name, headline = ("", "")
        if og.get("title"):
            name, headline = _parse_name_and_headline(og["title"])
        company, location = _parse_company_and_location(og.get("description", ""))
        if not company:
            ld = _parse_json_ld(html)
            company = ld.get("company", "")
            if not location:
                location = ld.get("location", "")
        if not company:
            company = _parse_html_company(html)
        return name, headline, company, location

    async def fetch_profile(self, urn_or_url: str) -> dict[str, Any]:
        slug = _slug_of(urn_or_url)

        # ── Stage 1: LinkedIn direct via Googlebot UA ──────────────────────
        log.info("public.stage1_linkedin_direct", slug=slug, proxy=bool(self._proxy))
        try:
            resp = await self._get(
                f"https://www.linkedin.com/in/{slug}/",
                headers={
                    "User-Agent": _GOOGLEBOT_UA,
                    "Accept": "text/html",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.google.com/",
                },
            )
            final_url = str(resp.url)
            if resp.status_code == 200 and not any(p in final_url for p in _AUTH_WALL):
                name, headline, company, location = self._extract_profile_data(resp.text)
                if name:
                    log.info("public.stage1_success", name=name, company=company)
                    return _build_result(slug, name, headline, company, location, "linkedin_direct")
            log.info("public.stage1_blocked", status=resp.status_code, url=final_url)
        except Exception as e:
            log.warning("public.stage1_error", error=str(e))

        # ── Stage 1.5: Chrome UA + session warm-up (gated profiles) ───────
        # Mimics a real browser navigating from Google: warm up session on
        # linkedin.com to get li_sugr/bcookie/lidc cookies, then visit the
        # profile with those cookies + Google referer. LinkedIn's CDN treats
        # cookie-bearing requests as returning browsers and serves the page.
        log.info("public.stage1_5_warmed_chrome", slug=slug)
        try:
            await self._warm_up_session()
            ua = random.choice(_USER_AGENTS)
            google_referer = f"https://www.google.com/search?q={slug.replace('-', '+')}+linkedin"
            resp = await self._get(
                f"https://www.linkedin.com/in/{slug}/",
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
                    "Accept-Encoding": "gzip, deflate, br",
                    "Referer": google_referer,
                    "DNT": "1",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            final_url = str(resp.url)
            if resp.status_code == 200:
                name, headline, company, location = self._extract_profile_data(resp.text)
                if name:
                    log.info("public.stage1_5_success", name=name, company=company)
                    return _build_result(slug, name, headline, company, location, "linkedin_direct")
                log.warning("public.stage1_5_no_data", status=resp.status_code, url=final_url)
            else:
                log.info("public.stage1_5_blocked", status=resp.status_code, url=final_url)
        except Exception as e:
            log.warning("public.stage1_5_error", error=str(e))

        # ── Stage 2: Yahoo search snippet ──────────────────────────────────
        log.info("public.stage2_yahoo", slug=slug)
        try:
            # Referer: looks like we came from Google search
            google_referer = f"https://www.google.com/search?q={slug.replace('-', '+')}+linkedin"
            resp = await self._get(
                "https://search.yahoo.com/search",
                headers=_random_headers(referer=google_referer),
                params={"p": f"{slug.replace('-', ' ')} site:linkedin.com/in"},
            )
            if resp.status_code == 200:
                hit = _yahoo_search(resp.text, slug)
                if hit:
                    name, headline, company, location = _parse_search_result(hit["title"], hit["snippet"])
                    log.info("public.stage2_success", name=name, company=company)
                    return _build_result(slug, name, headline, company, location, "yahoo_snippet")
                log.warning("public.stage2_no_match", slug=slug)
            else:
                log.warning("public.stage2_bad_status", status=resp.status_code)
        except Exception as e:
            log.warning("public.stage2_error", error=str(e))

        raise FetcherError(
            f"Could not find public data for '{slug}'. "
            "Profile may not be indexed or the slug is incorrect."
        )

    async def search_companies(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return []

    async def fetch_company(self, urn: str) -> dict[str, Any]:
        raise FetcherError("PublicFetcher does not support company lookups.")

    async def close(self) -> None:
        try:
            result = self._session.close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
