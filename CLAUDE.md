# CLAUDE.md — Building on Top of This System

This document is for developers who want to extend, modify, or build new features on top of this LinkedIn verification system. It covers the LLM integration specifically and how to change or replace it.

---

## How LLM is Used

The LLM (Claude via Anthropic API) is used as a **smart fallback** — not as the primary verification engine.

The pipeline is:

```
1. Normalize URL → fetch profile from LinkedIn
2. Resolve company from profile experience (pure fuzzy match — no LLM)
3. Match roles (pure function — no LLM)
4. Score evidence (pure rules — no LLM)
5. IF resolution.method === "ambiguous" OR match.kind === "no_match" with low confidence:
       → Call LlmVerifier for disambiguation + confidence adjustment
6. Merge LLM output into final score and uncertainty reasons
```

The LLM is **never** used to produce the primary confidence score. It adjusts an already-computed score and adds issues to the uncertainty list.

---

## Key Files

| File | What it does |
|---|---|
| `verifier/src/llm/verifier.ts` | `LlmVerifier` interface + `AnthropicVerifier` + `NoopVerifier` |
| `verifier/src/pipeline/verify.ts` | Where and when the LLM is called |
| `verifier/src/llm/anthropic.ts` | Lower-level Anthropic client helpers |
| `verifier/src/server.ts` | Composition root — where `AnthropicVerifier` is constructed |

---

## The `LlmVerifier` Interface

```typescript
export interface LlmVerificationResult {
  verdict: "yes" | "no" | "uncertain";
  confidenceAdjustment: number;   // -30 to +30 — added to rules score
  normalizedCompany: string | null;
  issues: string[];               // fed into uncertainty.reasons
  reasoning: string;              // for debugging / audit
}

export interface LlmVerifier {
  verify(
    profile: Profile,
    companyQuery: string,
    resolution: CompanyResolution,
  ): Promise<LlmVerificationResult>;
}
```

To add a new LLM provider (OpenAI, Gemini, local Ollama), implement this interface and swap it in `server.ts`.

---

## The Anthropic Prompt

`AnthropicVerifier.verify()` sends a structured JSON prompt to Claude with:

1. **Full profile data** — experience entries with URNs, titles, dates, employer names
2. **Company query** — the raw string the caller provided
3. **Resolution result** — what fuzzy matching found (if anything)
4. **Instructions** — asks for verdict, confidence adjustment (−30 to +30), normalized company name, issue list, and chain-of-thought reasoning

The prompt is in `verifier/src/llm/verifier.ts` in the `buildPrompt()` function. Edit it directly to change LLM behavior.

### Prompt engineering notes

- Response format is strict JSON (enforced via `response_format` or system prompt prefix)
- The LLM is given explicit examples of the `issues` field so it returns machine-readable strings
- Temperature is `0` for reproducibility — the same input should return the same result
- Model: `claude-3-5-haiku-20241022` (fast, cheap, sufficient for structured JSON tasks)

To use a different model, change `model` in `AnthropicVerifier`:

```typescript
const response = await this.client.messages.create({
  model: "claude-opus-4-5",  // or any other Anthropic model
  max_tokens: 512,
  ...
});
```

---

## When the LLM is Called

In `verifier/src/pipeline/verify.ts`:

```typescript
const isStrongMatch =
  resolution.method === "exact" ||
  resolution.method === "alias" ||
  (resolution.method === "fuzzy_dominant" && matches[0]?.kind === "current_exact_urn");

if (!isStrongMatch && llmVerifier) {
  const llmResult = await llmVerifier.verify(profile, input.company, resolution);
  // merge confidenceAdjustment into confidence
  // merge issues into uncertainty.reasons
}
```

**The LLM is NOT called when:**
- Company matched by exact name or alias → deterministic result
- URN match + fuzzy dominant → high-confidence rules-based result

**The LLM IS called when:**
- `resolution.method === "ambiguous"` — multiple candidates, fuzzy gap too small to pick one
- `resolution.method === "not_found"` — nothing matched in profile experience

---

## Disabling the LLM

Set `ANTHROPIC_API_KEY` to an empty string or remove it from `.env`. The server automatically falls back to `NoopVerifier`:

```typescript
const llmVerifier = config.anthropicApiKey
  ? new AnthropicVerifier(config.anthropicApiKey)
  : new NoopVerifier();
```

`NoopVerifier` returns `{ verdict: "uncertain", confidenceAdjustment: 0, issues: [], reasoning: "LLM disabled" }` — the pipeline continues with rules-based scoring only.

---

## Adding New Scoring Rules

All scoring logic lives in `verifier/src/score/rules.ts`. Each rule is a pure function:

```typescript
const myNewRule: ScoringRule = (ctx) => {
  // ctx.profile  — full Profile object
  // ctx.resolution — CompanyResolution
  // ctx.matches  — RoleMatch[]
  // ctx.now      — Date
  if (/* condition */) return null; // rule doesn't fire
  return {
    signal: "my_new_signal",
    weight: 15,       // positive = increases confidence, negative = decreases
    detail: "human-readable explanation for evidence list",
  };
};

// Register it
export const RULES: ScoringRule[] = [
  // ... existing rules ...
  myNewRule,
];
```

Rules are additive — just append to the `RULES` array. The scorer sums all weights and clamps to [0, 100].

---

## Adding New Company Aliases

Company name normalization lives in `verifier/src/normalize/company.ts`. The alias map resolves common rebranding:

```typescript
const ALIAS_MAP: Record<string, string> = {
  facebook: "meta",
  "facebook inc": "meta",
  alphabet: "google",
  "google llc": "google",
  // add more here:
  "twitter": "x",
  "x corp": "x",
};
```

When the caller provides "Facebook", `normalizeCompany("Facebook")` returns `{ normalized: "meta", was_aliased: true, raw: "facebook" }`.

---

## Adding New URL Patterns

URL normalization lives in `verifier/src/normalize/url.ts`. The normalizer handles `/in/`, `/pub/`, and `/sales/people/` paths. To add a new pattern:

```typescript
// Example: handle /showcase/ pages (company sub-pages)
const showcaseMatch = path.match(/^\/showcase\/([^/]+)/i);
if (showcaseMatch) slug = showcaseMatch[1]!;
```

---

## Extending the API

The verifier API is a Fastify server. Routes live in `verifier/src/routes/verify.ts`.

To add a new endpoint:

```typescript
fastify.get("/verify/stats", async (req, reply) => {
  return { total_verified: 0 }; // wire to MongoDB
});
```

Register the route in `server.ts`:

```typescript
await fastify.register(verifyRoutes, { pipeline, llmVerifier });
```

---

## Changing the Fetcher

The fetcher has a `LinkedInFetcher` protocol (duck-typed in Python):

```python
class LinkedInFetcher(Protocol):
    async def fetch_profile(self, ident: str) -> dict: ...
    async def search_companies(self, query: str, limit: int = 5) -> list[dict]: ...
```

To swap the scraping backend:
1. Implement a new class that satisfies this protocol
2. Change `FETCHER_MODE` in `.env`
3. Update `fetcher/app/main.py` to instantiate your class

Currently supported modes:
- `login` — Playwright + persistent browser context (default)
- `voyager` — curl-cffi TLS-spoofed HTTP with cookies
- `headless` — Playwright with XHR interception
- `fixture` — loads static JSON files for tests

---

## Caching Behavior

### Redis (hot cache, TTL 1h)
Profile and company objects are stored in Redis by URN. Redis is best-effort — if unavailable, the system falls through to MongoDB.

```python
# Fetcher: redis_cache.py
await redis_cache.set_profile(urn, profile_dict, ttl=3600)
```

### MongoDB (warm cache, TTL 90d)
Full profile documents with `expireAfterSeconds` TTL index. Raw Voyager responses also stored for re-parsing.

### Cache bypass
Pass `max_age_days: 1` in the verify request to force a fresh fetch if data is older than 1 day.

---

## Observability

The system logs structured JSON via `pino` (verifier) and Python's `logging` (fetcher). Each log line includes:

- `event` — what happened
- `level` — info / warn / error
- `timestamp` — ISO 8601
- `urn` / `slug` — profile identifier (where relevant)
- `duration_ms` — timing for key operations

To add custom metrics, hook into the pipeline at `verifier/src/pipeline/verify.ts`.

---

## Testing

```bash
cd verifier

# Type-check
npx tsc --noEmit

# Unit tests (pure functions, no external services needed)
npx tsx --test src/tests/url.test.ts \
               src/tests/companyResolver.test.ts \
               src/tests/scorer.test.ts \
               src/tests/roleMatcher.test.ts
```

Tests are pure functions — no MongoDB, Redis, LinkedIn, or Anthropic connections needed.

For integration tests, use the fixture fetcher:

```bash
FETCHER_MODE=fixture uvicorn app.main:app --port 8001
```

Then run the full verify pipeline against known fixture profiles.

---

## Cost Model (Anthropic)

LLM calls are only made when fuzzy matching is weak. In practice:

- **~30% of requests** trigger the LLM (ambiguous or not-found)
- **~70% of requests** are resolved by pure rules (exact URN match or dominant fuzzy match)

At $0.25/M input tokens and ~800 tokens/call:
- Cost per LLM call: ~$0.0002
- At 1M verifications/day with 30% LLM rate: ~$60/day

To reduce costs further:
1. Cache LLM results by `(normalized_company, profile_urn)` key
2. Use `claude-haiku` (already default) instead of Sonnet or Opus
3. Increase the `isStrongMatch` threshold to call LLM less often
