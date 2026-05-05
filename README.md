# LinkedIn Employment Verification System

Verify whether a person currently works at a company by scraping their LinkedIn profile directly — no third-party data providers.

**Input:** LinkedIn profile URL (messy/malformed OK) + company name (misspelled/rebranded OK)  
**Output:** `yes` / `no` / `uncertain` verdict, confidence score 0–100, structured evidence, explicit uncertainty

---

## Architecture

```
Client (curl / API consumer)
        │
        ▼
  Verifier (Node.js / Fastify)   ← port 3000
  • URL normalization
  • Company resolution (from profile experience)
  • Role matching (URN → fuzzy name → parent/subsidiary)
  • Weighted-rule scoring + evidence list
  • LLM fallback (Claude) for ambiguous matches
  • BullMQ async/batch jobs
        │
        ▼
  Fetcher (Python / FastAPI)     ← port 8001
  • Playwright login-based scraper
  • Account pool with rotation
  • MongoDB cache (profiles, companies, raw captures)
  • Redis hot cache
        │
        ▼
  LinkedIn Voyager API (via authenticated browser session)
```

---

## Prerequisites

- Docker Desktop running
- Python 3.11+
- Node.js 18+
- A LinkedIn account (credentials in `.env`)
- Optional: Anthropic API key (for LLM disambiguation — system works without it)

---

## Setup

### 1. Clone and configure

```bash
git clone <repo>
cd "Verification System"
cp .env.example .env
```

Edit `.env`:

```env
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=yourpassword
ANTHROPIC_API_KEY=sk-ant-...   # optional but recommended
```

### 2. Start MongoDB + Redis

```bash
docker-compose up -d mongo redis
```

### 3. Start the fetcher

```bash
cd fetcher
pip install -e ".[dev]"
python -m playwright install chromium
uvicorn app.main:app --port 8001
```

On first startup the fetcher will open a Chromium window and log in to LinkedIn. This is intentional — it creates a persistent browser session that survives restarts.

### 4. Start the verifier

```bash
cd verifier
npm install
npx tsx src/server.ts
```

---

## Run with Docker (all services)

```bash
docker-compose up --build
```

Services:
- `mongo` — MongoDB on port 27017
- `redis` — Redis on port 6379
- `fetcher` — Python scraper on port 8001
- `verifier` — Node.js API on port 3000

> **Note:** The fetcher's Playwright login requires a display. In Docker, either use `xvfb-run` or run the fetcher natively during development.

---

## Quick test

```bash
# Health checks
curl http://localhost:8001/health
curl http://localhost:3000/health

# Verify employment
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/satyanadella/",
    "company": "Microsoft"
  }' | jq .
```

---

## Run Tests

```bash
cd verifier
# TypeScript type check
npx tsc --noEmit

# Unit tests (41 tests)
npx tsx --test src/tests/url.test.ts \
               src/tests/companyResolver.test.ts \
               src/tests/scorer.test.ts \
               src/tests/roleMatcher.test.ts
```

See [SAMPLE_REQUESTS.md](SAMPLE_REQUESTS.md) for full coverage of edge cases.

---

## API Reference

### `POST /verify` — Synchronous verification

```json
{
  "linkedin_url": "https://www.linkedin.com/in/someone/",
  "company": "Some Company",
  "max_age_days": 14
}
```

`max_age_days` (optional, default 14): treat cached data older than this as stale and re-fetch.

**Response:**

```json
{
  "verdict": "yes",
  "confidence": 75,
  "evidence": [
    { "signal": "current_role_exact_urn", "weight": 65, "detail": "current SWE at Vahan (urn match)" },
    { "signal": "profile_freshness", "weight": 10, "detail": "profile fetched 2 days ago" }
  ],
  "uncertainty": {
    "level": "low",
    "reasons": []
  },
  "resolution": {
    "query": "Vahan",
    "normalized": "vahan",
    "method": "exact",
    "was_aliased": false
  },
  "profile": {
    "name": "Prakhar Pandey",
    "headline": "...",
    "experience": [...]
  },
  "company": {
    "name": "Vahan",
    "urn": "urn:li:fsd_company:...",
    "normalized": "vahan"
  }
}
```

**Verdict values:**
- `yes` — confidence ≥ 70
- `no` — confidence ≤ 30
- `uncertain` — confidence 31–69

### `POST /verify/async` — Queue a job (returns immediately)

```json
{ "linkedin_url": "...", "company": "..." }
```

Returns: `{ "job_id": "...", "status": "queued" }`

### `GET /verify/job/:jobId` — Poll job result

Returns job status (`waiting`, `active`, `completed`, `failed`) and result when done.

### `POST /verify/batch` — Queue multiple jobs

```json
{ "requests": [{ "linkedin_url": "...", "company": "..." }, ...] }
```

Returns: `{ "job_ids": ["...", "..."] }`

---

## Configuration

All config via environment variables (`.env`):

| Variable | Default | Description |
|---|---|---|
| `LINKEDIN_EMAIL` | required | LinkedIn login email |
| `LINKEDIN_PASSWORD` | required | LinkedIn login password |
| `FETCHER_MODE` | `login` | `login`, `voyager`, `headless`, or `fixture` |
| `FETCHER_PORT` | `8001` | Fetcher listen port |
| `FETCHER_WORKERS` | `1` | Uvicorn worker processes |
| `VERIFIER_PORT` | `3000` | Verifier listen port |
| `VERIFIER_CONCURRENCY` | `4` | BullMQ worker concurrency |
| `MONGO_URI` | `mongodb://localhost:27017/verifier` | MongoDB connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `RATE_LIMIT_RPM` | `120` | API rate limit (requests per minute) |
| `ANTHROPIC_API_KEY` | optional | Claude API key for LLM disambiguation |
| `ACCOUNTS_FILE` | `accounts.json` | Path to LinkedIn accounts pool JSON |

---

## Multiple LinkedIn Accounts

For production scale, use an account pool (`accounts.json`):

```json
[
  { "email": "account1@gmail.com", "password": "pass1", "weight": 1.0 },
  { "email": "account2@gmail.com", "password": "pass2", "weight": 1.0 }
]
```

The fetcher rotates accounts automatically, tracking health, request counts, and cooldowns. See [DECISIONS.md](DECISIONS.md) for the full account pool design.

---

## Understanding the Output

### Verdict

| Verdict | Confidence | Meaning |
|---|---|---|
| `yes` | ≥ 70 | Strong evidence they work there now |
| `uncertain` | 31–69 | Evidence exists but incomplete/ambiguous |
| `no` | ≤ 30 | No evidence of current employment |

### Key Evidence Signals

| Signal | Weight | Meaning |
|---|---|---|
| `current_role_exact_urn` | +65 | LinkedIn company URN matches exactly, role is current |
| `current_role_fuzzy_name` | +40 | Company name fuzzy-matches, role is current |
| `profile_freshness` | +10 / −25 | Data freshness bonus/penalty |
| `headline_match` | +15 | Headline mentions the company |
| `ended_recently` | −20 | Role ended <90 days ago |
| `ended_old` | −40 | Role ended >90 days ago |
| `multiple_current_roles` | −10 | Multiple simultaneous current roles (contractor signal) |
| `no_experience_data` | −30 | Profile has no experience entries |

### Uncertainty Levels

- `low` — evidence is clear and consistent
- `medium` — one ambiguity (e.g., stale data, fuzzy match)
- `high` — multiple ambiguities (e.g., no experience, contractor patterns, ambiguous company)

---

## Limitations & Known Issues

1. **LinkedIn anti-bot**: The Playwright login works but LinkedIn can challenge accounts. Use an account pool with > 1 account for reliability.
2. **No shortlink resolution**: `lnkd.in` URLs are returned as-is; the system cannot dereference them offline.
3. **LLM requires credits**: The Anthropic API is paid. Without credits, the system falls back to rules-based scoring gracefully.
4. **Playwright in Docker**: Requires `xvfb` or `--no-sandbox` flags in headless environments.
5. **Profile staleness**: LinkedIn profiles can be months out of date. Always check the `uncertainty` field.

---

## Project Layout

```
Verification System/
├── fetcher/               # Python / FastAPI scraper
│   ├── app/
│   │   ├── fetchers/      # login.py (Playwright), voyager.py, headless.py
│   │   ├── pool/          # account_pool.py, pool_fetcher.py
│   │   ├── storage/       # mongo.py, redis_cache.py
│   │   ├── middleware/    # rate_limit.py
│   │   ├── parsers/       # profile.py, company.py
│   │   ├── services/      # profile_service.py, company_service.py
│   │   └── routes/        # fetch.py
│   └── pyproject.toml
├── verifier/              # Node.js / Fastify verifier
│   └── src/
│       ├── normalize/     # url.ts, company.ts
│       ├── resolve/       # companyResolver.ts
│       ├── match/         # roleMatcher.ts
│       ├── score/         # rules.ts, scorer.ts, verdict.ts
│       ├── llm/           # verifier.ts (AnthropicVerifier, NoopVerifier)
│       ├── pipeline/      # verify.ts
│       ├── queue/         # verifyQueue.ts, verifyWorker.ts
│       ├── middleware/    # rateLimit.ts
│       ├── routes/        # verify.ts
│       └── server.ts
├── docker-compose.yml
├── .env.example
├── accounts.example.json
├── DECISIONS.md
├── CLAUDE.md
└── SAMPLE_REQUESTS.md
```
