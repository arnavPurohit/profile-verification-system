# DECISIONS.md

Design rationale for the LinkedIn Employment Verification System. This is a live document that captures *why* things are the way they are — not just what was built.

---

## How I read the brief

The challenge says "how you frame the problem is part of it," so the framing is explicit.

**Three constraints are absolute:**
1. Data comes from LinkedIn directly — no third-party enrichment (Apollo, Proxycurl, Coresignal, etc.)
2. Inputs are messy on purpose — URLs malformed, company names ambiguous/rebranded
3. Design for scale, not just correctness

**Two things are intentionally under-specified:**
1. What "currently works at" means when someone has contractor roles, stealth roles, advisor positions, or multiple simultaneous employers
2. How to handle the gap between what LinkedIn shows and ground truth (profiles are user-maintained and frequently stale)

**My interpretation:**
- "Currently" means: has an open-ended (no `end` date) role that LinkedIn marks as current — with nuance for roles ending recently (<90 days)
- "Verification" means: producing a structured evidence list with explicit uncertainty, not a binary yes/no. Three verdicts: `yes`, `no`, `uncertain`
- The system should be loud about what it doesn't know, not confidently wrong

---

## What was built

### Input handling

**URL normalization** (`verifier/src/normalize/url.ts`) — pure function, no IO:
- Adds `https://` if missing
- Strips country/mobile subdomains (`in.linkedin.com`, `m.linkedin.com`)
- Handles `/pub/`, `/sales/people/`, `/in/` formats
- Strips tracking query params (`?trk=...`, `?src=...`)
- Lowercases slugs, strips trailing slashes
- Handles extra path segments after the slug (`/in/slug/details/experience/`)
- Returns the canonical URL + slug for fetching

**Company name normalization** (`verifier/src/normalize/company.ts`):
- Strips legal suffixes (Inc, Ltd, Corp, LLC, GmbH, etc.)
- Lowercase + token sort for fuzzy matching
- Alias map for known rebrands: `facebook → meta`, `alphabet → google`, `twitter → x`
- `was_aliased` flag propagated to response so callers can see when normalization fired

### Data acquisition

**Playwright login fetcher** (`fetcher/app/fetchers/login.py`):
- Full browser login with human-like typing delays
- Persistent browser context (`launch_persistent_context`) — survives restarts without re-login
- Voyager API called via `BrowserContext.request.get` (in-browser, not a separate HTTP client) — this inherits cookies and session headers automatically
- Uses `FullProfileWithEntities-93` decoration for complete profile data

**Account pool** (`fetcher/app/pool/`):
- Multiple accounts loaded from `accounts.json`
- Weighted random selection for acquisition
- Per-account health tracking: request count, last used, cooldown periods
- Daily reset of request counters
- `PooledFetcher` wraps pool + `LoginFetcher` — the verifier sees a single interface

**Caching layer** (two-tier):
- Redis: hot cache, 1-hour TTL, best-effort (silently fails if unavailable)
- MongoDB: warm cache, 90-day TTL via `expireAfterSeconds` index, raw + parsed docs stored separately

### Company resolution

**From profile experience only** (`verifier/src/resolve/companyResolver.ts`):

LinkedIn company search was implemented and then deliberately removed. The search API returned "blended" results with a shape mismatch, and more importantly, it was unnecessary: the profile already contains the company data we need (name + URN) in `experience` entries.

Resolution stages:
1. Normalize query (strip suffixes, apply alias map)
2. Build candidate `Company` objects from unique `experience` entries (deduplicated by URN)
3. Exact match on normalized name → `method: "exact"`
4. Fuzzy match (token-set ratio) → `method: "fuzzy_dominant"` if gap is clear, `"ambiguous"` if gap is too narrow
5. `method: "not_found"` if no candidate scores ≥ 0.5

### Role matching

**`roleMatcher.ts`** — pure function:
- `current_exact_urn` — both have same URN, role is current → weight +65
- `current_fuzzy_name` — name fuzzy-matches ≥ 0.6, role is current → weight +40
- `current_parent_subsidiary` — profile URN matches company's `parent_urn` → weight +35
- `ended_recent_at_company` — role ended < 90 days ago → weight −20
- `ended_old_at_company` — role ended > 90 days ago → weight −40
- `no_match` — nothing matched → weight 0 (other rules penalize)

### Scoring

**Rule registry** (`verifier/src/score/rules.ts`) — open/closed pattern. Each rule is a pure function, append to `RULES` to add signals:

| Signal | Weight | Trigger |
|---|---|---|
| `current_role_exact_urn` | +65 | URN match, current |
| `current_role_fuzzy_name` | +40 | Name fuzzy match, current |
| `headline_match` | +15 | Headline contains company name |
| `profile_freshness` | +10 / −10 / −25 | Fresh / 60–365d / >365d |
| `ended_recently` | −20 | Role ended < 90d |
| `ended_old` | −40 | Role ended > 90d |
| `multiple_current_roles` | −10 | 2+ simultaneous current roles |
| `no_experience_data` | −30 | Profile has no experience entries |
| `timeline_overlap` | −15 | Significant overlapping roles |
| `impossible_dates` | −20 | Start > end, or start year < 1950 |
| `headline_experience_conflict` | −15 | Headline contradicts experience |

### Verdict

`yes` if confidence ≥ 70, `no` if ≤ 30, `uncertain` otherwise.

Uncertainty level: `low` (0 issues), `medium` (1 issue), `high` (2+ issues or verdict is uncertain).

Uncertainty reasons are explicit human-readable strings in the response — not hidden.

### LLM as smart fallback

`AnthropicVerifier` (`verifier/src/llm/verifier.ts`) is called **only** when:
- `resolution.method === "ambiguous"` (fuzzy gap too narrow to pick)
- `resolution.method === "not_found"` with low confidence (maybe LLM can see something rules missed)

It is **not** called when:
- Exact name or alias match
- URN match + dominant fuzzy score (already high-confidence)

LLM output:
- `verdict` — independent assessment
- `confidenceAdjustment` — added to rules score (−30 to +30)
- `issues` — fed into `uncertainty.reasons`
- `reasoning` — for debugging/audit

The rules score is always primary. The LLM adjusts it, never replaces it. This maintains auditability.

`NoopVerifier` is wired when no API key is set — system works without Anthropic.

### Scalability infrastructure

**BullMQ queue** (`verifier/src/queue/`):
- `POST /verify/async` — enqueues job, returns `job_id` immediately
- `GET /verify/job/:jobId` — polls result
- `POST /verify/batch` — enqueues N jobs
- Configurable concurrency via `VERIFIER_CONCURRENCY`

**API rate limiting** (both services):
- Redis-backed sliding window (per IP)
- Falls back to in-memory if Redis unavailable
- Default: 120 req/min

**Multi-worker**:
- Fetcher: Uvicorn `--workers N` (env `FETCHER_WORKERS`)
- Verifier: BullMQ concurrency (env `VERIFIER_CONCURRENCY`)

---

## What's deliberately out of scope

| Item | Why | Production path |
|---|---|---|
| Account warming pipeline | Weeks of organic activity; not weekend-scoped | State machine: warming → active → cooled → challenged → suspended → retired |
| Residential proxy fleet | Provider-dependent ops, ~$5–15/GB | Sticky IPs per account, geo-pinned |
| Browser fingerprint randomization | Real effort, covered conceptually | Per-account canvas/WebGL/font fingerprints held constant |
| Behavioral pacing | Daily cap in code; full curve isn't | Working-hours-only, weekend dropoff, jitter |
| LinkedIn company search | API shape mismatch, resolution from profile is sufficient | Re-enable if URN enrichment is needed |
| Webhook delivery | Queue exists, HTTP layer is sync for now | `POST /verify` → 202 + job_id → webhook on completion |
| Federated profile cache | Multi-tenant schema needed | The actual moat: Customer A's captures answer B's queries |
| CAPTCHA solver | Manual challenges only for now | Anti-detect browsers + solver services |
| Schema drift auto-detection | Per-field parse-rate metric exists, ML doesn't | Alert + parser freeze when field success rate drops |
| End-to-end integration tests | Pure unit tests are the right scope | Fixture-based integration tests with recorded Voyager responses |

---

## The assumptions made

1. **"Currently works at" means LinkedIn profile says so.** We are not checking payroll, email, or HR records. LinkedIn is the source of truth — and we are explicit about this via uncertainty when profiles are stale.

2. **A person can have multiple "current" roles.** Consultants, advisors, board members, stealth founders — all legitimate. We don't collapse these; we flag them as a source of uncertainty and let the caller decide.

3. **Company name normalization is a best-effort operation, not a database lookup.** We do not call LinkedIn company search (removed after observing API shape issues). We resolve from profile experience data only, supplemented by a hardcoded alias map and LLM fallback.

4. **Stale profiles are verifiable but with reduced confidence.** We do not refuse to answer on stale data. We apply a freshness penalty and surface the staleness in uncertainty reasons. The caller can set `max_age_days` to force a re-fetch.

5. **LinkedIn URNs are stable identifiers.** Once we have a company URN from a profile experience entry, matching by URN is authoritative. Name matching is a fallback for when URNs are absent.

6. **The extension capture path (not built, described) is the production primary.** In production, when a customer's sales reps browse LinkedIn naturally, their extension captures profiles for free freshness. The Playwright scraper is the fallback. This changes the cost structure dramatically.

7. **Single-region, single-database for the take-home.** Multi-region is documented in the scaling roadmap.

---

## Trade-offs

| Decision | Alternative | Reasoning |
|---|---|---|
| Two services (Python + Node) | Monolith | Fetcher has different scaling/failure profile (proxies, accounts, browser state) than verifier (stateless, horizontal). Python has the best LinkedIn scraping libraries; Node has the best API server DX. |
| Rules + LLM hybrid | Pure LLM | Rules are auditable, reproducible, cheap. LLM only where rules tie or fail. A confidence score without an evidence list is untrustworthy. |
| Company resolution from profile | LinkedIn company search API | Search API returned wrong shape; resolution from experience is simpler, more reliable, and doesn't need an extra network call. |
| Three verdicts (yes/no/uncertain) | Binary yes/no | Binary verdict forces false precision. "Uncertain" is the honest answer for ambiguous inputs and lets callers do their own filtering. |
| MongoDB | Postgres | Drift-heavy nested JSON, TTL-native, sharding-native. Raw Voyager JSON stored as-is; Postgres JSONB works but loses native TTL and sharding. |
| Async job queue (BullMQ) | Blocking sync | Sync is simpler for demos; BullMQ enables batch, retry, and > 1 worker. Both exist — `POST /verify` is sync, `POST /verify/async` is queued. |
| In-process rate limiting | API gateway | Sufficient for this scope; production would add Kong/Nginx in front. |
| Persistent browser context | Storage state JSON | Persistent context survives browser restarts without re-exporting state. More robust under crash-restart cycles. |
| Pure functions for normalize/match/score | Stateful objects | Pure functions are trivially testable without mocks. 41 unit tests run in < 200ms with no external services. |

---

## Scalability path (rough leverage order)

1. **Crowd-sourced freshness via extension.** When customers browse LinkedIn, extension captures profiles for free. Eliminates most Playwright scraping.
2. **Account pool with warming pipeline.** State machine, daily budget caps (~100 views/account/day), automatic account rotation.
3. **Sticky residential proxies per account.** One IP per account, geo-pinned, persistent weeks-months.
4. **Behavioral pacing.** Working-hours-only, time-of-day curve, weekend slowdown.
5. **Async API + webhooks.** `POST /verify` → 202 + job_id, result via webhook. Queue already exists.
6. **Federated profile cache.** Profiles fetched for Customer A answer Customer B's queries. The real network-effect moat.
7. **Schema drift monitoring.** Per-field parse-success rate; alert when field drops; freeze deploys.
8. **Ban prediction model.** Train on per-account telemetry; rest account 24–72h before predicted ban.
9. **Multi-region.** EU/APAC account pools + proxies for geographic coherence.

---

## Failure modes at scale

| # | Failure | Symptom | When |
|---|---|---|---|
| 1 | Account ban velocity > warming rate | Pool shrinks, throughput drops | Always |
| 2 | Soft challenges (CAPTCHA, "verify you") | Account goes silent | 10–30%/month |
| 3 | Commercial Use Limit | 429 after ~150 views/day/account | Daily |
| 4 | Schema drift | Parse rate drops | Weekly small, monthly large |
| 5 | Detection rollout | Pool → banned overnight | Quarterly |
| 6 | Cookie / session expiry | 401s | Every 2–12 weeks |
| 7 | Stale profiles | Confident wrong answer | Continuous |
| 8 | LLM credit exhaustion | 400s, graceful fallback | When balance runs out |
| 9 | Redis unavailable | Falls to MongoDB; slower but functional | Infra issue |
| 10 | MongoDB slow | p99 verification latency balloons | Under write load |

---

## The evaluation criteria — answered directly

**How you handle ambiguity:**
- Three-verdict system. Explicit uncertainty reasons. `was_aliased`, `was_normalized` in response.
- Multiple resolution methods: `exact`, `alias`, `fuzzy_dominant`, `ambiguous`, `not_found`.
- LLM called specifically for the ambiguous case.

**How you think about messy inputs:**
- URL normalizer handles 12+ malformation patterns as pure function, tested with 14 cases.
- Company alias map handles known rebrands without external lookups.
- `was_normalized: true` in response tells callers the input was cleaned.

**Judgment under under-specification:**
- "Currently works at" is fuzzy — we handle contractors (multiple current roles flagged), advisors (same), stale data (freshness penalty), and stealth roles (low confidence, explicit uncertainty).
- We do not pretend these edge cases don't exist.

**Trade-off quality:**
- See the trade-offs table above. Each decision is documented with the alternative and the reasoning, not just asserted.

**Scalability of design:**
- Redis hot cache + MongoDB warm cache + BullMQ queue + account pool + rate limiting all implemented.
- 9-layer production scraping stack documented; layers 1, 4 (partial), 6, 7, 8 (partial) built.

**Whether implementation matches stated reasoning:**
- Pure functions for normalize/match/score → 41 unit tests, zero external dependencies in tests.
- LLM as fallback only → rules score is always primary, LLM adjusts it.
- Explicit uncertainty → `uncertainty.reasons` is a structured list, not a flag.

---

## Frontend design (React) — built to match backend scale

The frontend is not built yet, but the design decisions here are intentional so the UI can scale alongside the backend without a rewrite.

### Core principle: async-first UI

The backend supports both sync (`POST /verify`) and async (`POST /verify/async` + `GET /verify/job/:id`) verification. The frontend must be built around the async path — not the sync one — because at scale, sync requests will time out under load.

**Pattern:** Submit → immediate job card with spinner → poll until done → show result. Never block the UI waiting for a single verification.

### Component structure

```
<App>
  ├── <VerifyForm>          — URL + company name inputs, submit button
  ├── <JobList>             — live list of in-progress + completed verifications
  │   └── <JobCard>         — single job: status, spinner, result when ready
  │       ├── <VerdictBadge>    — yes (green) / no (red) / uncertain (amber)
  │       ├── <ConfidenceBar>   — 0–100 score, color-coded
  │       ├── <EvidenceList>    — collapsible list of signal + weight + detail
  │       └── <UncertaintyBox>  — only shown when level != "low"
  └── <BatchUpload>         — CSV upload → bulk submit → job list
```

### State management

Use **React Query** (TanStack Query), not Redux or Zustand:

- `useMutation` for `POST /verify/async` — submit, get `job_id`, add to list
- `useQuery` with `refetchInterval: 2000` for each `GET /verify/job/:id` — auto-polls until `status === "completed"` then stops
- Built-in caching, deduplication, and background refresh — exactly what polling needs
- No manual `setInterval` / `clearInterval` management

This directly maps to BullMQ's job lifecycle on the backend.

### Batch / CSV upload

For high-volume use (hundreds of verifications at once):

1. User uploads a CSV: `linkedin_url, company`
2. Frontend parses client-side (no server upload needed)
3. Calls `POST /verify/batch` with the full array → gets back `job_ids[]`
4. Adds all job cards to the list simultaneously
5. Each card polls independently — no single request blocks others

This works because `POST /verify/batch` is O(1) from the API's perspective (just enqueues jobs) and BullMQ handles the actual concurrency.

### URL input UX — normalize in the browser too

Run `normalizeUrl()` (a pure function — copy or share via a small npm package) on the input **before** submitting. Show the user what the URL was cleaned to:

```
Input:  in.linkedin.com/in/SatyaNadella/?trk=nav
Cleaned: https://www.linkedin.com/in/satyanadella
```

This prevents wasted API calls on obviously malformed inputs and gives the user immediate feedback.

### Evidence display — audit-first

The key design principle: **confidence score is a summary, evidence list is the truth.** The UI should make the evidence list the primary display, not a collapsed footnote.

```
✅ YES — 75% confidence

Evidence
  +65  Current role exact URN match → Software Engineer at Microsoft (2014–present)
  +15  Headline contains "Microsoft"
  +10  Profile fetched 1 day ago

Uncertainty: low — no issues detected
```

This directly mirrors the backend's `evidence[]` array structure. Never just show "75%".

### Rate limiting feedback

The backend returns `429` when the API rate limit is hit (`RATE_LIMIT_RPM`). The frontend should:
- Catch `429` responses
- Show a toast: "Too many requests — slowing down"
- Implement client-side exponential backoff before retrying
- Never silently swallow rate limit errors

### Trade-offs for this design

| Decision | Alternative | Why |
|---|---|---|
| React Query polling | WebSocket / SSE | Simpler infra; polling at 2s is fine for verification latency. Switch to SSE if p50 drops below 1s. |
| Async-first (job card pattern) | Sync + loading spinner | Sync times out under load; async survives network blips and page refreshes |
| Client-side CSV parsing | Server-side upload | Keeps batch submit stateless; no file storage needed |
| Evidence-first UI | Score-first UI | A number without context is untrustworthy; evidence is the audit trail |
| Copy `normalizeUrl` to frontend | Shared package | Avoids build tooling complexity for a take-home; in production, publish as `@company/linkedin-utils` |
