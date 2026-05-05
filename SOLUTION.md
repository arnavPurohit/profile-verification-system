# Solution Document — LinkedIn Verification System

**Status:** design draft, pre-implementation. Review and push back before we build.

---

## 1. How I'm reading the brief

The challenge says "how you frame the problem is part of it," so the framing is explicit:

- **The interesting work is judgment, not heroics.** They are not testing whether I can defeat LinkedIn's anti-bot stack in two days. They are testing whether I understand which 10% of the system to build to demonstrate I understand the other 90%.
- **Scraping is the centerpiece**, because the company is scraping-heavy and asked a separate scraping question. The Voyager scraper has to be the most polished piece of code in the repo, not a stub.
- **Inputs are messy on purpose.** URLs are malformed/shortened, company names are ambiguous/rebranded. The system has to be loud about uncertainty rather than confidently wrong.
- **Output is evidence, not a number.** A confidence score of 73 means nothing without the signals behind it. The verdict + score is a summary of the evidence list, not a black-box judgment.

---

## 2. Assumptions (called out explicitly)

1. We cannot use third-party enrichment providers (Apollo, Coresignal, Proxycurl). We *can* use infrastructure providers (proxies, captcha solvers, compute, MongoDB Atlas) — those are network plumbing, not data sources.
2. We cannot get LinkedIn's official API for a take-home. Production roadmap mentions it; we don't pretend to build against it.
3. Real-world scraping requires authenticated sessions. The take-home uses one personal LinkedIn cookie in `.env`; the production path (account pool, residential proxies, fingerprint randomization) is documented, not built.
4. "Currently works at" is fuzzy on purpose: present-dated roles, no end-date, multiple parallel roles, advisor/contractor/stealth. The system handles each with different confidence treatment.
5. Verification is read-heavy: most queries hit cache, not LinkedIn. The architecture optimizes for cache-first.
6. Single-region deployment is sufficient for the take-home; multi-region is a documented scaling step.

---

## 3. Architecture overview

Two services, two languages, chosen for their strengths:

```
                     ┌────────────────────────────────────────┐
                     │  Customer / API consumer               │
                     │  POST /verify { url, company }         │
                     └──────────────┬─────────────────────────┘
                                    │
                     ┌──────────────▼─────────────────────────┐
                     │  Node.js — Verifier (the brain)        │
                     │  • URL + company name normalization    │
                     │  • Company resolution                  │
                     │  • Role matching                       │
                     │  • Weighted-rule scoring + evidence    │
                     │  • Claude calls (fuzzy disambiguation) │
                     └──────────────┬─────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
       ┌──────▼─────┐        ┌──────▼─────┐       ┌──────▼─────┐
       │  MongoDB   │        │  Python —  │       │  Anthropic │
       │  (cache +  │        │  Fetcher   │       │  API       │
       │  audit)    │        │  service   │       └────────────┘
       └────────────┘        └──────┬─────┘
                                    │
                  ┌─────────────────┼─────────────────┐
                  │                 │                 │
           ┌──────▼─────┐    ┌──────▼─────┐    ┌──────▼─────┐
           │ Extension  │    │  Voyager   │    │  Fixtures  │
           │  (XHR      │    │  scraper   │    │  (tests)   │
           │  capture)  │    │  + headless│    │            │
           └──────┬─────┘    └──────┬─────┘    └────────────┘
                  │                 │
                  └────────┬────────┘
                           ▼
                       LinkedIn
```

**Why two languages.** The fetcher lives in the messy world (TLS spoofing, headless browsers, cookie management) where Python's libraries (`curl_cffi`, `playwright`, `httpx`) are mature. The verifier lives in the clean world (API, scoring, orchestration) where TypeScript is comfortable and matches typical sales-intel stacks. They communicate over internal HTTP.

This split also mirrors how it would actually deploy: scraper has a different scaling and failure profile (proxies, account pool, sticky sessions) than the verifier (stateless, horizontal).

---

## 4. Scraping strategy (the centerpiece)

### 4.1 Three fetchers, one interface

```ts
interface LinkedInFetcher {
  fetchProfile(urlOrUrn): RawProfile
  searchCompanies(query): RawCompany[]
  fetchCompany(urn): RawCompany
}
```

| Fetcher | Role | When used |
|---|---|---|
| `ExtensionFetcher` | Reads from a queue of Voyager XHR captures pushed by Chrome extensions. | Primary cache-feeder in production. |
| `VoyagerFetcher` | Authenticated server-side scraper. TLS-spoofed `httpx`/`curl_cffi` calls to `voyager/api/...`. Headless Chromium fallback. | Backfill for profiles the extension hasn't seen. |
| `FixtureFetcher` | Reads stored JSON snapshots from `fixtures/`. | All tests. Reproducible edge cases. |

The verifier never knows which is in use. Tests run against fixtures; demos run against Voyager + extension.

### 4.2 Why the Chrome extension is the primary path

- **User is already authenticated and on the page.** No cookie management, no CSRF dance.
- **Real human IP, fingerprint, behavior.** Lowest detection surface.
- **Hooks `chrome.webRequest.onCompleted` on `*.linkedin.com/voyager/api/*`** — captures the Voyager JSON LinkedIn already sent to the user's tab. Zero extra requests to LinkedIn.
- **Schema-stable.** We get LinkedIn's normalized API format, not minified DOM classes that break weekly.
- **Scales with userbase, not proxy budget.**

Limitations (called out): only profiles users actually visit; cold-start problem; the extension itself is detectable if it grows large enough. These motivate the Voyager fallback.

### 4.3 The Voyager scraper — built properly

This is the code that has to be polished. It demonstrates every pattern a scraping company cares about.

1. **TLS / HTTP/2 fingerprint spoofing.** `curl_cffi` with `impersonate="chrome120"`. Bare `httpx`/`requests` has a JA3 fingerprint LinkedIn flags on sight.
2. **Exact header replication.** `csrf-token` derived from `JSESSIONID`, `x-restli-protocol-version: 2.0.0`, `accept: application/vnd.linkedin.normalized+json+2.1`, `x-li-track`, `x-li-page-instance`. Captured from a real session.
3. **Cookie management.** `li_at`, `JSESSIONID`, `bcookie`, `lidc` persisted; cookie jar shared across requests in a session.
4. **Token-bucket rate limiting** per account with log-normal jitter (not uniform). Hard daily cap.
5. **Retry classification.**
   - `429` → exponential backoff, retry
   - `401` → account dead, alert, do not retry
   - `999` → LinkedIn-specific block, escalate to headless
   - `5xx` → transient, retry with backoff
   - Parse failure → no retry, log to schema-drift monitor
6. **Async, bounded concurrency.** `curl_cffi.requests.AsyncSession` with a semaphore. No connection pool storms.
7. **Robust parser.** Voyager returns a normalized graph (`included` array with `$type` references). Resolver walks the graph, applies sensible defaults for missing fields, records `parser_version` on every parsed row.
8. **Raw + parsed dual storage.** Every response saved to `raw_captures` immutably. Re-parse without re-fetch when the parser improves.
9. **Headless Chromium fallback** (Playwright + `playwright-stealth`) for cookie minting and pages Voyager doesn't expose cleanly.
10. **Structured logging per request.** `{urn, status, latency_ms, account_id, parser_ok, parser_version}`. Grafana-ready.

### 4.4 The 9-layer mental model (production stack)

This is what we *describe* in DECISIONS.md, not what we build. It's how we demonstrate scale-thinking:

| Layer | Concern | Built? |
|---|---|---|
| 1. Acquisition | Extension / Voyager / Headless | Partial (one account, low rate) |
| 2. Identity | Account pool, warming pipeline, state machine | No — described |
| 3. Network | Sticky residential proxies | No — described |
| 4. Fingerprint | TLS, HTTP/2, browser fingerprint per account | Partial (TLS via curl_cffi) |
| 5. Behavior | Daily budgets, time-of-day shaping, jitter | Partial (per-account cap, jitter) |
| 6. Capture | Voyager JSON > hydration blob > DOM | Yes |
| 7. Storage | Raw + parsed, TTL, dedup | Yes (Mongo) |
| 8. Orchestration | Job queue, bin-packing scheduler, backpressure | Minimal (Mongo-backed queue) |
| 9. Observability | Per-account/IP/fingerprint metrics | Minimal (logs + parse-rate metric) |

### 4.5 Generic-scraper demonstration

A separate `generic_fetcher.py` module demonstrates the same patterns (TLS spoofing, retries, rate limiting, robust parsing) against a non-LinkedIn target. Two reasons: shows transferable scraping skill, and gives a place for the unit tests of the scraping primitives to live without requiring a LinkedIn fixture.

---

## 5. Data model & storage — MongoDB

### 5.1 Why MongoDB for this workload

LinkedIn data is deeply nested, schema-drifting, semi-structured JSON; the workload is cache-shaped with TTL semantics. This is exactly Mongo's design center.

- **Native JSON storage.** No `JSONB` indirection or relational decomposition. The Voyager response *is* the document.
- **Schema flexibility.** LinkedIn ships shape changes every few weeks. New fields just appear. No migration pressure.
- **TTL indexes.** `{ expireAfterSeconds }` on `fetched_at` automatically expires stale captures. In Postgres this is partition rotation or `pg_cron` — bolted on, not native.
- **Native sharding.** Capture cache grows to billions of documents at scale. Mongo shards on URN out of the box; Postgres needs Citus.
- **Aggregation pipeline.** Operational analytics ("parse-success rate per fingerprint per day") on semi-structured data is one `$group` stage.
- **Stack alignment.** This company runs on Mongo. Introducing Postgres just to satisfy a personal preference trades one kind of complexity for two.

### 5.2 Where Mongo is weaker, and the answer

| Weakness | Mitigation |
|---|---|
| `$lookup` joins are awkward | Denormalize. Verification rows snapshot the resolved profile + company at decision time. |
| Multi-document transactions are slower | Most writes are single-document. The few that aren't (e.g. counter increments) tolerate eventual consistency. |
| Job queue ergonomics worse than Postgres `SKIP LOCKED` | `findOneAndUpdate` with status flip is fine. Move to Redis if it grows. |
| Fuzzy text search | Atlas Search (Lucene-backed) is excellent for company-name resolution. |

### 5.3 Collections

```
profiles            { urn, url, name, headline, location,
                      experience: [{ company_urn, title, start, end,
                                     is_current, employment_type }],
                      education: [...], fetched_at, source, parser_version }
                    indexes: { urn: 1 }, { fetched_at: 1, TTL=14d }

companies           { urn, name, normalized, aliases: [...],
                      website, industry, size_band, hq, fetched_at }
                    indexes: { urn: 1 }, { normalized: "text" }, TTL=60d

raw_captures        { _id, urn, kind, payload, headers, fetched_at,
                      account_id, http_status }
                    indexes: { urn: 1, fetched_at: -1 }, TTL=30d

verifications       { _id, input: { url, company },
                      verdict: "yes" | "no" | "uncertain",
                      confidence: 0..100,
                      evidence: [{ signal, weight, value, note }],
                      snapshot: { profile, company },
                      scorer_version, created_at }
                    indexes: { created_at: -1 }, { input.url: 1 }

accounts            { _id, email, cookies, fingerprint, proxy,
                      state, daily_used, daily_cap, tz,
                      last_challenge_at }

fetch_jobs          { _id, urn, kind, priority, status, attempts,
                      scheduled_for, account_id, created_at }
                    indexes: { status: 1, scheduled_for: 1 }
```

`raw_captures` is the source of truth. Everything parsed is derivable from it. If the parser improves, we re-derive without re-fetching.

---

## 6. Verification logic

### 6.1 Pipeline

```
POST /verify { url, company }
  ↓
1. Normalize URL    → canonical form, extract slug/URN
2. Normalize name   → lowercase, strip suffixes, alias map
3. Cache lookup     → profiles[urn]; if fresh, skip fetch
4. Resolve company  → URN (LinkedIn entity); fuzzy via Claude if rules tie
5. Match            → profile.experience ⨯ company URN; classify roles
6. Score            → weighted rule sum, capped 0..100
7. Decide           → verdict + evidence list
8. Persist          → verifications collection
9. Respond
```

### 6.2 URL normalization

Handles: `lnkd.in/*` shortlinks, country subdomains (`uk.linkedin.com`), `/sales/people/...`, `/pub/...`, mobile (`m.linkedin.com`), trailing slashes, query params, missing protocol, public ID vs vanity slug, capitalization. Output: canonical `https://www.linkedin.com/in/{slug}`.

### 6.3 Company name normalization

Handles: lowercase, strip legal suffixes (Inc, LLC, Ltd, Pvt, GmbH, S.A.), strip whitespace and punctuation, abbreviation expansion (JPMC → JPMorgan Chase), known aliases (Facebook ↔ Meta, Google ↔ Alphabet, Twitter → X). Aliases live in a seed map; we explicitly do not claim to ship a global company DB.

### 6.4 Company resolution

1. Exact match against `companies.normalized` → done.
2. Alias map hit → done.
3. LinkedIn company search (via Voyager) → top N candidates.
4. If one candidate dominates (token-set ratio > threshold) → done.
5. Else → Claude is asked to disambiguate, given (input string, candidate names, candidate websites/industries). Returns one URN or "ambiguous."
6. "Ambiguous" propagates as a confidence penalty and an evidence item.

This is the **only** place the LLM is in the path. The score itself is rules.

### 6.5 Role matching

For the resolved company URN, scan `profile.experience`:

- **Exact URN match on a current role** (no end-date, or end-date in future) → strongest positive signal.
- **Name fuzzy match on current role with no URN** → medium positive.
- **Recent-but-ended role** → weak positive, may indicate stale profile.
- **Role at a parent/subsidiary** → medium positive with evidence note.
- **Multiple current roles** → ambiguity penalty (advisor + employee is common but lowers certainty).
- **Employment type = contractor/advisor/intern** → signal flag, doesn't kill the verdict but is surfaced.
- **No matching role** → negative.

### 6.6 Confidence scoring (weighted rules, illustrative)

| Signal | Weight |
|---|---|
| Current role, exact company URN match | +50 |
| Current role, fuzzy name match (no URN) | +25 |
| Profile freshness (last update < 90d) | +10 |
| Profile freshness (90–365d) | 0 |
| Profile freshness (> 365d) | -10 |
| Role at parent/subsidiary | +15 |
| Recently ended role at company (< 90d) | +5 |
| Multiple current roles | -10 |
| Role employment type = contractor/advisor | -5 (with flag) |
| Headline mentions company | +5 |
| Headline contradicts experience | -15 |
| Company resolution ambiguous | -10 |
| No matching role | -50 |

Sum, clamp 0..100, map:
- `≥ 70` → `verdict: "yes"`
- `≤ 30` → `verdict: "no"`
- otherwise → `verdict: "uncertain"`

Every signal that fired goes into `evidence[]` with weight + value + note. The score is reproducible from the evidence; nothing is hidden.

### 6.7 Where Claude is and is not used

**Used:** company name disambiguation when rules tie; optional natural-language one-line summary of the verdict.

**Not used:** producing the score, deciding the verdict, parsing LinkedIn responses, anything else.

This keeps the system auditable: a customer asking "why did you say yes?" gets a deterministic evidence list, not "the LLM said so."

---

## 7. API

### 7.1 Endpoint

```
POST /verify
{
  "url": "linkedin.com/in/john-smith-abc",
  "company": "Meta"
}
```

### 7.2 Response

```json
{
  "verdict": "yes",
  "confidence": 82,
  "evidence": [
    { "signal": "current_role_exact_urn", "weight": 50,
      "value": "urn:li:company:10667", "note": "Software Engineer, Meta, since 2022-03" },
    { "signal": "company_alias_hit", "weight": 0,
      "value": "Facebook → Meta", "note": "Resolved via alias map" },
    { "signal": "profile_freshness", "weight": 10,
      "value": "21d", "note": "Last updated 2026-04-13" },
    { "signal": "single_current_role", "weight": 0, "value": true }
  ],
  "person": {
    "name": "John Smith",
    "headline": "Software Engineer at Meta",
    "location": "Menlo Park, CA",
    "current_roles": [...],
    "past_roles": [...],
    "inferred_seniority": "mid",
    "profile_last_updated": "2026-04-13T...",
    "data_freshness_days": 21,
    "source": "extension_capture"
  },
  "company": {
    "urn": "urn:li:company:10667",
    "name": "Meta",
    "normalized": "meta",
    "aliases": ["Facebook"],
    "website": "meta.com",
    "industry": "Internet",
    "size_band": "10001+"
  },
  "uncertainty": [],
  "scorer_version": "1.0.0",
  "request_id": "..."
}
```

### 7.3 Sync vs async

For the take-home: **sync**. `POST /verify` blocks on the cache lookup + (if needed) Voyager fetch. p50 fast, p99 slow.

Production: **async** with `POST /verify` returning a `job_id` and `GET /verify/:id` or webhook for the result. Designed for, not built — the fetch step is already a queueable job.

---

## 8. Tests that matter

The interesting cases, not the happy path. Each is a fixture + an expected verdict.

| # | Case | Expected |
|---|---|---|
| 1 | Person at company, exact match, fresh profile | `yes`, high conf |
| 2 | Rebrand (Facebook → Meta) | `yes`, alias evidence |
| 3 | Parent vs subsidiary (YouTube → Google) | `yes` with parent-match note |
| 4 | Multiple current roles (employee + advisor) | `yes`, ambiguity penalty |
| 5 | Stale profile (last edit 2021) saying "current" | `uncertain`, freshness penalty |
| 6 | Recently ended role | `no` or `uncertain` |
| 7 | Contractor at company | `yes` with flag, lower conf |
| 8 | Person not at company | `no` |
| 9 | Malformed URL (`lnkd.in/abc`) | normalized then verified |
| 10 | Company name typo ("Mteea") | resolved via fuzzy + LLM |
| 11 | Ambiguous company ("Apple" → Inc. vs Bank) | LLM disambiguation, evidence shows reasoning |
| 12 | Stealth role / no listed company | `uncertain`, surfaced |
| 13 | Voyager 999 block (account flagged) | graceful failure, fallback path or pending |
| 14 | Schema drift (parser misses a field) | logged, partial data, no crash |

---

## 9. Scaling roadmap (described, not built)

In rough leverage order:

1. **Account pool with warming pipeline.** State machine: warming → active → cooled → challenged → suspended → retired. New accounts replace banned ones automatically. Warming = weeks of organic-looking activity before the account joins the work pool.
2. **Sticky residential proxies per account.** One IP per account, persistent for weeks-months. Geo-pinned to the account's claimed location.
3. **Browser fingerprint randomization per account.** Coherent fingerprints (matching timezone, fonts, hardware) held constant per account. `puppeteer-extra-plugin-stealth` or equivalent.
4. **Behavioral pacing.** Daily budget per account (~80–150 profile views), log-normal jitter, working-hours-only in account's timezone, weekend slowdown.
5. **Bin-packing scheduler.** Assigns fetch jobs across accounts respecting daily budget × time-of-day curve × geography preference. Pauses accounts at risk of ban.
6. **Async API + job queue.** Redis or SQS. `POST /verify` returns immediately; result via webhook.
7. **Federated capture cache.** Extension captures from one customer help verify leads for all customers. This is the actual moat (Apollo/Lusha network effect).
8. **Stale-while-revalidate.** Always serve cached fast, refresh in background if stale.
9. **Schema-drift monitoring.** Per-field parse-success rate per day. Alert when LinkedIn changes shape; freeze deploys; auto-suggest parser candidates.
10. **Ban prediction model.** Train on per-account telemetry to detect decay 24–72h before ban, rest the account proactively.
11. **Observability stack.** Per-account ban rate, per-fingerprint detection rate, $/verified-profile, parser success rate per field, queue depth, p95 fetch latency.
12. **Multi-region.** Repeat the stack in EU/APAC for regional account + IP coherence.

Each item maps to a concrete failure mode I can name and explain.

---

## 10. Trade-offs (explicit)

| Decision | Alternative | Why this choice |
|---|---|---|
| MongoDB | Postgres | Drift-heavy nested JSON, TTL-native, sharding-native, matches company stack. Postgres JSONB works but is "Mongo with friction." |
| Two services (Py + Node) | Monolith in one language | Scraper has different scaling/failure profile. Mirrors how it deploys. Plays to each language's library strengths. |
| Hybrid scoring (rules + LLM only for fuzzy resolution) | Pure rules / pure LLM | Rules give auditable, reproducible scores; LLM only where rules genuinely tie. Customer can defend the verdict. |
| Extension primary + Voyager fallback | Voyager only | Lowest detection surface, scales with userbase. Voyager handles cold-start. |
| Sync API | Async API | Simpler for the take-home. Fetch step already designed as a queueable job, so async is a swap, not a redesign. |
| One LinkedIn account, env-flagged | Account pool | Take-home scope. Pool is the single biggest production work item, documented explicitly. |
| TLS spoofing via `curl_cffi` | Bare `httpx` / `requests` | LinkedIn fingerprints JA3. Bare libs fail at layer 1. Non-negotiable. |
| Raw + parsed storage | Parsed only | Parsers will improve and LinkedIn will drift. Re-derivability is worth the storage cost. |

---

## 11. Build order

1. Mongo schema + collections + TTL indexes
2. Python: Voyager scraper (TLS, headers, cookies, retries, parser, async)
3. Python: generic-scraper module + tests on a non-LinkedIn target
4. Python: fixture-based tests for the parser
5. Python: minimal Mongo-backed job queue + per-account daily cap
6. Python: HTTP API exposing fetch + search + company endpoints
7. Node: URL + company-name normalization + alias map
8. Node: company resolution (exact → fuzzy → Claude tiebreaker)
9. Node: role matcher
10. Node: weighted-rule scorer + evidence assembly
11. Node: `POST /verify` endpoint
12. Tests for the full pipeline against fixtures (the 14 cases above)
13. Chrome extension (manifest v3, `webRequest` capture, ships to backend)
14. README (setup, run, test) + DECISIONS.md (this doc, polished)

---

## 12. Repo structure

```
verification-system/
├── README.md
├── DECISIONS.md                          # this doc, polished for delivery
├── docker-compose.yml                    # mongo + python + node services
├── fixtures/                             # captured Voyager JSON, by case name
│   ├── profile_meta_employee.json
│   ├── profile_facebook_rebrand.json
│   ├── profile_multi_role.json
│   ├── profile_stale_2021.json
│   └── company_search_apple_ambiguous.json
├── fetcher/                              # Python service
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                       # FastAPI
│   │   ├── voyager.py                    # core LinkedIn scraper
│   │   ├── headless.py                   # Playwright fallback
│   │   ├── extension_intake.py           # POST /captures from Chrome ext
│   │   ├── fixtures.py                   # FixtureFetcher
│   │   ├── parser.py                     # Voyager graph → DTO
│   │   ├── generic.py                    # generic scraper demo
│   │   ├── ratelimit.py                  # token bucket + jitter
│   │   ├── retries.py                    # classified retry
│   │   ├── tls.py                        # curl_cffi wrapper
│   │   ├── accounts.py                   # cookie + state mgmt
│   │   ├── queue.py                      # mongo-backed job queue
│   │   └── db.py                         # mongo client + collections
│   └── tests/
├── verifier/                             # Node/TS service
│   ├── package.json
│   ├── src/
│   │   ├── server.ts                     # Fastify
│   │   ├── normalize/
│   │   │   ├── url.ts
│   │   │   └── company.ts
│   │   ├── aliases.ts                    # seed map
│   │   ├── resolve.ts                    # company resolution
│   │   ├── match.ts                      # role matching
│   │   ├── score.ts                      # weighted rules
│   │   ├── llm.ts                        # Anthropic client (disambig only)
│   │   ├── fetcherClient.ts              # talks to Python service
│   │   └── db.ts
│   └── tests/
├── extension/                            # Chrome MV3 extension
│   ├── manifest.json
│   ├── background.js                     # webRequest listeners
│   └── README.md
└── samples/
    ├── happy_path.http                   # sample requests, more than happy paths
    ├── rebrand.http
    ├── multi_role.http
    ├── stale_profile.http
    ├── ambiguous_company.http
    └── not_at_company.http
```

---

## 13. What's deliberately out of scope

- Account pool, warming pipeline, residential proxies (described, not built).
- Multi-region deployment.
- Customer auth, multi-tenancy, billing.
- Streaming / realtime — async batch is the correct shape.
- A global company alias DB — seed map of ~50 cases only.
- Captcha-solver integration.
- Federated capture cache across customers (described as the production moat).

---

## 14. Two phrases for the interview

**On MongoDB:**
> "The cache layer is billions of nested, drift-heavy JSON documents with TTL semantics — that's exactly Mongo's shape. JSONB in Postgres works but loses native sharding and TTL indexes, and your team already operates Mongo at scale, so introducing Postgres just trades one kind of complexity for two."

**On scraping difficulty:**
> "It's not the HTTP requests — it's the seven layers underneath: TLS fingerprint, HTTP/2 fingerprint, header order, cookie continuity, behavioral pacing, account warming, proxy stickiness. A naive `requests.get` fails at layer one. The interesting design work is the account pool and the ban-prediction loop, not the parser."

---

## Open questions for review

1. Is the scope right? Anything I'm over-building or under-building?
2. The Chrome extension — minimal but real, or describe-only?
3. Sync API vs async-with-job-id — does sync feel too simple for the role they're hiring for?
4. How much of the "production roadmap" should be code vs prose? Right now it's heavily prose; some parts (job queue, per-account cap) are code.
5. Anything about the company's stack or interview style I should adjust for?
