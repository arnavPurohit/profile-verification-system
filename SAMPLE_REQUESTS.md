# Sample Requests

All requests hit the verifier at `http://localhost:3000`. The verifier talks to the fetcher at `http://localhost:8001`. Both must be running (see README).

---

## Happy path — clean URL, unambiguous company

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/prakhar-pandey-59a79616a/",
    "company": "Vahan"
  }' | jq .
```

**Expected:** `verdict: "yes"`, `confidence >= 65`, `uncertainty.level: "low"`

---

## Happy path — exact urn match with different profile

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/anuj-pundir29/",
    "company": "Vahan"
  }' | jq .
```

**Expected:** `verdict: "yes"`, `confidence >= 70`

---

## Non-happy path — person does NOT work at company

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/anuj-pundir29/",
    "company": "Fareye"
  }' | jq .
```

**Expected:** `verdict: "no"`, `confidence <= 20`, evidence shows "no role matched"

---

## Non-happy path — messy URL (country subdomain + trailing slash + tracking params)

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://in.linkedin.com/in/satyanadella/?trk=nav_responsive_tab_profile&src=some_campaign",
    "company": "Microsoft"
  }' | jq .
```

URL is normalized to `https://www.linkedin.com/in/satyanadella` before fetching.  
**Expected:** `verdict: "yes"`, `was_normalized: true` in response

---

## Non-happy path — old /pub/ URL format

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/pub/satya-nadella/12/345/678",
    "company": "Microsoft"
  }' | jq .
```

Old LinkedIn public profile format, still resolved correctly.

---

## Non-happy path — company name variant / rebranding (Facebook → Meta)

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/zuck/",
    "company": "Facebook"
  }' | jq .
```

"Facebook" is in the alias map → resolves to "Meta" in profile experience.  
**Expected:** `was_aliased: true` in resolution, verdict: "yes"

---

## Non-happy path — ambiguous company name, LLM disambiguates

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/example-user/",
    "company": "Apple",
    "max_age_days": 30
  }' | jq .
```

"Apple" is ambiguous (Apple Inc, Apple Bank, Apple Records). If the profile has multiple candidates that fuzzy-match similarly, the LLM will be called to disambiguate.  
**Expected:** `resolution.method: "ambiguous"` or `"llm"`, `uncertainty.level: "high"` if LLM not available.

---

## Non-happy path — contractor / advisor (multiple current roles)

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/example-contractor/",
    "company": "SomeStartup"
  }' | jq .
```

If the profile has 3+ simultaneous current roles (typical for consultants), `multiple_current_roles` evidence fires with a penalty weight, and uncertainty level rises.  
**Expected:** evidence contains `multiple_current_roles` signal, `uncertainty.level: "medium"` or `"high"`

---

## Non-happy path — stale profile (data older than 1 year)

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/someone-who-hasnt-updated/",
    "company": "OldCompany"
  }' | jq .
```

`profile_freshness` rule applies negative weight for data > 365 days old. Uncertainty reasons include `"profile data is X days old — may be stale"`.  
**Expected:** `uncertainty.reasons` contains staleness warning

---

## Non-happy path — force re-fetch (bypass cache)

```bash
curl -s -X POST http://localhost:3000/verify \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/prakhar-pandey-59a79616a/",
    "company": "Vahan",
    "max_age_days": 1
  }' | jq .
```

`max_age_days: 1` means "treat data older than 1 day as stale." Forces a fresh LinkedIn fetch.

---

## Async verification (non-blocking)

```bash
# Enqueue
JOB=$(curl -s -X POST http://localhost:3000/verify/async \
  -H 'content-type: application/json' \
  -d '{
    "linkedin_url": "https://www.linkedin.com/in/prakhar-pandey-59a79616a/",
    "company": "Vahan"
  }')
echo $JOB

JOB_ID=$(echo $JOB | jq -r '.job_id')

# Poll result (may take a few seconds)
sleep 5
curl -s http://localhost:3000/verify/job/$JOB_ID | jq .
```

**Expected:** immediate `202` with `job_id`, then result after poll.

---

## Batch verification (multiple profiles at once)

```bash
curl -s -X POST http://localhost:3000/verify/batch \
  -H 'content-type: application/json' \
  -d '{
    "requests": [
      { "linkedin_url": "https://www.linkedin.com/in/prakhar-pandey-59a79616a/", "company": "Vahan" },
      { "linkedin_url": "https://www.linkedin.com/in/anuj-pundir29/", "company": "Vahan" },
      { "linkedin_url": "https://www.linkedin.com/in/satyanadella/", "company": "Microsoft" }
    ]
  }' | jq .
```

**Expected:** array of `job_id`s. Poll each one individually.

---

## Health checks

```bash
# Fetcher
curl -s http://localhost:8001/health | jq .

# Verifier
curl -s http://localhost:3000/health | jq .
```

Both should return `{"status": "ok"}` with additional metadata (mode, llm_enabled, etc.)

---

## Direct fetcher call (bypass verifier)

```bash
# Fetch a profile directly (hits LinkedIn Voyager API via browser session)
curl -s -X POST http://localhost:8001/fetch/profile \
  -H 'content-type: application/json' \
  -d '{"urn_or_url": "https://www.linkedin.com/in/satyanadella/"}' | jq .
```
