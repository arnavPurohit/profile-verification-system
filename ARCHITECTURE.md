# Architecture, SOLID application, and testability

This doc explains *why* the code is laid out the way it is — the folder structure, how each SOLID principle shows up in the codebase, and how the structure makes tests trivially writable on every function.

We do not commit tests in this take-home (explicitly out of scope), but the structure is built so any reviewer can write them without touching production code.

---

## 1. Folder structure (and the reason for each boundary)

```
verification-system/
├── fetcher/                  Python service. Owns LinkedIn data acquisition.
│   └── app/
│       ├── domain/           Pure data types. No IO, no logic.
│       ├── fetchers/         LinkedInFetcher protocol + concrete implementations.
│       ├── parsers/          Raw Voyager JSON → domain types.
│       ├── scraping/         Generic primitives (TLS, retry, ratelimit, headers).
│       ├── generic/          Demo of scraping primitives on a non-LinkedIn target.
│       ├── storage/          Mongo repositories (one per collection).
│       ├── queue/            Job queue (Mongo-backed for the take-home).
│       ├── observability/    Structured logging.
│       ├── routes/           FastAPI route handlers (thin; delegate to services).
│       ├── config.py         Settings (env-driven, single source).
│       └── main.py           Composition root: wires DI, mounts routes.
│
├── verifier/                 Node/TS service. Owns verdict and scoring logic.
│   └── src/
│       ├── domain/           Pure types: Profile, Company, Verification, Evidence.
│       ├── normalize/        Pure functions: normalizeUrl, normalizeCompanyName.
│       ├── resolve/          Company resolution stages (exact, fuzzy, LLM).
│       ├── match/            Role-to-company matching (pure).
│       ├── score/            Rule registry + scorer + verdict mapper (pure).
│       ├── fetcher/          HTTP client to the Python fetcher service.
│       ├── llm/              Anthropic SDK wrapper (used only by resolve/).
│       ├── storage/          Mongo repository for verifications audit log.
│       ├── pipeline/         Verify pipeline (composes everything end-to-end).
│       ├── routes/           Fastify route handlers (thin).
│       ├── config.ts         Settings.
│       └── server.ts         Composition root.
│
├── extension/                Chrome MV3.
│   ├── manifest.json
│   └── background.js         webRequest listener → POST /captures.
│
├── fixtures/                 Voyager JSON snapshots, by case name.
│   ├── profiles/
│   └── companies/
│
└── samples/                  .http files: more-than-happy-path example requests.
```

### Why these boundaries (not arbitrary)

- **`domain/` is pure.** Domain types know nothing about Mongo, HTTP, LinkedIn, or FastAPI. They can be imported anywhere without dragging IO.
- **`fetchers/` is the seam.** All LinkedIn data flows through one interface. Tests swap implementations; production wires the real one. This is the most important boundary in the system.
- **`parsers/` is separate from `fetchers/`.** Parsing is pure transformation; fetching is IO. Splitting them means we re-parse `raw_captures` without re-fetching when the parser improves.
- **`scraping/` holds primitives, not LinkedIn-specific logic.** Token-bucket rate limiter, classified retry, TLS-spoofing client wrapper — reusable for any scraper. `generic/` proves it by using these against a non-LinkedIn target.
- **`storage/` is a thin repository layer.** One file per collection. Routes never touch Mongo directly; they go through repositories.
- **`routes/` is thin.** Handlers delegate to services. No business logic lives in HTTP handlers.
- **`pipeline/` (verifier) composes.** The verify flow is one file you can read top-to-bottom and see the entire decision process.
- **`main.py` / `server.ts` are composition roots.** The only place where concrete classes are wired together. Everything else depends on abstractions.

---

## 2. SOLID — concrete, where each principle shows up

### S — Single Responsibility

Every module does exactly one thing. Examples:

- `normalize/url.ts` — only normalizes URLs. No IO, no DB, no LinkedIn.
- `normalize/company.ts` — only normalizes company names. Strips suffixes, applies aliases.
- `score/rules.ts` — declares the rule registry. Does not score; does not match.
- `score/scorer.ts` — applies the rules to a (profile, company, match) triple. Does not decide verdict.
- `score/verdict.ts` — maps a confidence score to "yes / no / uncertain". Nothing else.
- `parsers/voyager_graph.py` — resolves Voyager's normalized graph references. Doesn't know what a Profile is.
- `parsers/profile.py` — turns a resolved graph into a Profile. Doesn't fetch, doesn't store.

If any module grew a second responsibility, it'd be split. The smell-test: can you describe what the module does in one sentence with no "and"?

### O — Open / Closed

Two clear extensions points:

**Adding a new fetcher (new data source) requires zero changes to the verifier.**
```python
# fetcher/app/fetchers/base.py
class LinkedInFetcher(Protocol):
    async def fetch_profile(self, urn: str) -> RawProfile: ...
    async def search_companies(self, q: str) -> list[RawCompany]: ...
    async def fetch_company(self, urn: str) -> RawCompany: ...
```
Concrete implementations: `VoyagerFetcher`, `FixtureFetcher`, `ExtensionIntakeFetcher`. Add a fourth (`OfficialApiFetcher`, `CrawlerFetcher`) by creating a class that satisfies the protocol. The route layer picks one based on config.

**Adding a new scoring signal requires zero changes to the scorer.**
```ts
// verifier/src/score/rules.ts
export const RULES: ScoringRule[] = [
  currentRoleExactUrn,
  currentRoleFuzzyName,
  profileFreshness,
  parentSubsidiary,
  multipleCurrentRoles,
  // add a new rule here. The scorer iterates the registry.
];
```
Each rule is a pure function `(ctx) => Evidence | null`. The scorer doesn't know what they do — it just sums weights and collects evidence. Adding "education match" or "skills overlap" means appending one rule object.

### L — Liskov Substitution

`FixtureFetcher`, `VoyagerFetcher`, and `ExtensionIntakeFetcher` are interchangeable from the caller's perspective. They return the same shape, raise the same error types, and satisfy the same invariants. The verifier never inspects which one is in use.

This is what makes fixture-based tests possible — you swap the implementation in the composition root, and every other module behaves identically.

### I — Interface Segregation

The fetcher protocol is *narrow*. Three methods, all coherent. We do not lump in unrelated things like "store profile" or "log metric" — those live elsewhere. A consumer that only needs `searchCompanies` doesn't have to know about `fetchProfile`.

In the verifier, `LlmDisambiguator` is a separate interface from a generic `LlmClient`. The resolver only needs `disambiguate(input, candidates) → Choice`. It doesn't get a god-object Anthropic client.

### D — Dependency Inversion

High-level modules depend on protocols, not concretions. Examples:

- `pipeline/verify.ts` depends on `LinkedInFetcherClient` (interface), `CompanyResolver` (interface), `Scorer` (function), `VerificationsRepo` (interface). It does not import `axios`, `MongoClient`, or `@anthropic-ai/sdk` directly.
- `resolve/companyResolver.ts` depends on `LlmDisambiguator` (interface). It does not import the Anthropic SDK.
- `routes/fetch.py` depends on `LinkedInFetcher` and `ProfilesRepo` (protocols). It does not import `curl_cffi` or `motor`.

Concrete classes are wired together exactly once, in the composition root (`main.py` / `server.ts`). Tests construct the same dependencies with fakes.

---

## 3. Testability

Every function in this codebase can be unit-tested without spinning up Mongo, hitting LinkedIn, or calling Anthropic. Here's how the structure enforces that.

### Rule 1 — Pure functions where possible

These modules contain *only* pure functions:

- `verifier/src/normalize/url.ts`
- `verifier/src/normalize/company.ts`
- `verifier/src/match/roleMatcher.ts`
- `verifier/src/score/rules.ts`
- `verifier/src/score/scorer.ts`
- `verifier/src/score/verdict.ts`
- `fetcher/app/parsers/voyager_graph.py`
- `fetcher/app/parsers/profile.py`
- `fetcher/app/parsers/company.py`

Pure means: same input → same output, no IO, no global state, no time-of-day dependence. Tests are `assertEqual(fn(input), expected)`. No mocking required.

### Rule 2 — Inject IO at the boundary

Anything that touches the network or DB takes its dependencies in the constructor / function arguments:

```ts
// verifier/src/pipeline/verify.ts
export const buildVerifyPipeline = (deps: {
  fetcher: LinkedInFetcherClient;
  resolver: CompanyResolver;
  repo: VerificationsRepo;
  now: () => Date;
}) => async (input: VerifyInput): Promise<VerifyResult> => { ... };
```

Tests pass fakes:
```ts
const verify = buildVerifyPipeline({
  fetcher: fakeFetcher(...),
  resolver: fakeResolver(...),
  repo: fakeRepo(),
  now: () => new Date('2026-05-04'),
});
```

Even `now` is injected — so freshness scoring is deterministic in tests.

### Rule 3 — Repositories not direct DB access

```python
# fetcher/app/storage/profiles.py
class ProfilesRepo:
    def __init__(self, db): self.db = db
    async def get(self, urn) -> Profile | None: ...
    async def upsert(self, profile: Profile) -> None: ...
```

Routes call `repo.get(urn)`, never `mongo.profiles.find_one(...)`. Tests provide an in-memory repo:

```python
class FakeProfilesRepo:
    def __init__(self): self.store = {}
    async def get(self, urn): return self.store.get(urn)
    async def upsert(self, p): self.store[p.urn] = p
```

### Rule 4 — Fetchers swap by config

```python
# fetcher/app/main.py
def build_fetcher(settings) -> LinkedInFetcher:
    if settings.mode == "fixture":
        return FixtureFetcher(settings.fixtures_dir)
    if settings.mode == "voyager":
        return VoyagerFetcher(account=load_account(), client=tls_client())
    raise ValueError(settings.mode)
```

Integration tests run with `mode=fixture` and never hit the network. Demo runs with `mode=voyager` (or `mode=fixture` for offline reliability).

### Rule 5 — Time and randomness are injected

Anywhere we use `Date.now()`, `random()`, or `uuid()`, we accept it as a dependency:

```ts
type Clock = () => Date;
type Rng = () => number;
```

Tests pin clocks to known timestamps and seed RNGs. Nothing is flaky.

### Rule 6 — Logs are observable, not inspected

Tests do not assert log contents. Logs are for operations. Behavior is asserted on return values and repository state.

---

## 4. What testing each layer would look like

For the reviewer who wants to know what tests *would* be written:

| Layer | Test type | Example |
|---|---|---|
| `normalize/url.ts` | Unit, pure | `normalizeUrl('lnkd.in/abc') === 'https://www.linkedin.com/in/abc'` |
| `normalize/company.ts` | Unit, pure | `normalizeCompany('Meta Platforms, Inc.') === 'meta'` (post-alias) |
| `match/roleMatcher.ts` | Unit, pure | Given a profile + company URN, returns expected role classification |
| `score/rules.ts` | Unit, pure | Each rule fires/doesn't fire on hand-crafted contexts |
| `score/scorer.ts` | Unit, pure | Given evidence list, returns expected confidence |
| `parsers/profile.py` | Unit, pure | Fixture JSON → expected `Profile` |
| `fetchers/fixture.py` | Unit | Loads a fixture file and returns parsed shape |
| `fetchers/voyager.py` | Unit with HTTP mock | Mocked `curl_cffi` returns canned response → fetcher returns expected shape |
| `storage/profiles.py` | Integration with Mongo | Real Mongo (testcontainers), upsert + get round-trip |
| `pipeline/verify.ts` | Integration with fakes | Fixture fetcher + fake resolver + in-memory repo, end-to-end |
| `routes/verify.ts` | HTTP test | `POST /verify` with fixture-backed pipeline returns expected JSON |

Every test in this list is a 5-30 line file. None requires LinkedIn access, none requires real Mongo for the unit tier, none flakes on time.

---

## 5. The composition root pattern

The only place where concrete classes are constructed and wired:

**`fetcher/app/main.py`**
```python
def create_app(settings: Settings) -> FastAPI:
    db = MongoClient(settings.mongo_url)
    profiles_repo = ProfilesRepo(db)
    captures_repo = CapturesRepo(db)
    companies_repo = CompaniesRepo(db)
    fetcher = build_fetcher(settings)
    return mount_routes(profiles_repo, captures_repo, companies_repo, fetcher)
```

**`verifier/src/server.ts`**
```ts
async function bootstrap() {
  const db = await mongoClient(config.mongoUrl);
  const verificationsRepo = new VerificationsRepo(db);
  const fetcherClient = new HttpFetcherClient(config.fetcherUrl);
  const llm = new AnthropicDisambiguator(config.anthropicApiKey);
  const resolver = new CompanyResolver({ fetcher: fetcherClient, llm });
  const verify = buildVerifyPipeline({ fetcher: fetcherClient, resolver, repo: verificationsRepo, now: () => new Date() });
  return mountServer({ verify });
}
```

Two files. Everything else in the codebase imports interfaces and pure functions. This is what makes the system extendable, testable, and refactorable.

---

## TL;DR

- **Folder boundaries** mirror responsibility boundaries. No module does two things.
- **Fetchers + repositories + LLM** are interfaces. Concrete classes live behind one composition root each.
- **Pure logic** (normalize, match, score, parse, verdict) has no IO. Tests are `assertEqual`.
- **IO modules** take their dependencies as arguments. Tests pass fakes.
- **Time, randomness, network, DB** are all injected. Nothing flakes.
- **No tests committed** in this repo (out of scope), but every file in the codebase can be tested without changes.
