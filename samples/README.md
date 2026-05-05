# Sample requests

Eight scenarios covering more than the happy path. Each maps to a fixture in
`fixtures/profiles/` and an expected verdict.

| File | Scenario | Expected |
|---|---|---|
| `01_happy_path.http` | John Smith currently at Meta, exact match, fresh | `verdict: yes`, high confidence |
| `02_rebrand.http` | Priya at "Facebook" but URN matches Meta | `verdict: yes`, alias evidence |
| `03_multi_role.http` | Carlos: Stripe full-time + advisor at another co | `verdict: yes`, multi-role penalty |
| `04_stale_profile.http` | Sara's profile last updated 2021, says "current" Google | `uncertain` or `yes` with freshness penalty |
| `05_ended_role.http` | Amir ended his Stripe role 60 days ago | `no` or `uncertain` |
| `06_contractor.http` | Leo is a research consultant at Anthropic | `yes` with non-full-time flag |
| `07_not_at_company.http` | Jane at Coinbase, asked about Stripe | `verdict: no` |
| `08_ambiguous_company.http` | Tara at "Apple" — Inc vs Bank | `yes` for Apple Inc; LLM disambiguation if enabled |
| `09_messy_url.http` | Same profile via a `lnkd.in` shortlink-ish input | normalized then verified |

Run any of them with curl, httpie, or VS Code's REST client.

## Quick run with curl

```bash
curl -X POST http://localhost:8000/verify \
  -H 'content-type: application/json' \
  -d '{"url": "https://www.linkedin.com/in/john-meta", "company": "Meta"}'
```
