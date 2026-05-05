/**
 * Tests for scoring rules and verdict logic.
 * Run: npx tsx --test src/tests/scorer.test.ts
 */
import { strict as assert } from "node:assert";
import { test, describe } from "node:test";
import { score } from "../score/scorer.js";
import { decide } from "../score/verdict.js";
import type { Profile } from "../domain/index.js";
import type { CompanyResolution } from "../resolve/companyResolver.js";
import type { RoleMatch } from "../match/roleMatcher.js";

const NOW = new Date("2026-05-05T00:00:00Z");

function baseProfile(overrides: Partial<Profile> = {}): Profile {
  return {
    urn: "urn:li:fsd_profile:test",
    public_id: "test",
    url: null,
    name: "Test User",
    headline: null,
    location: null,
    experience: [],
    education: [],
    profile_last_updated: null,
    fetched_at: NOW.toISOString(),
    source: "fixture",
    parser_version: "1.0.0",
    ...overrides,
  };
}

function baseResolution(overrides: Partial<CompanyResolution> = {}): CompanyResolution {
  return {
    query: "Microsoft",
    normalized: "microsoft",
    method: "exact",
    company: {
      urn: "urn:li:fsd_company:1035",
      name: "Microsoft",
      normalized: "microsoft",
      aliases: [],
      website: null,
      industry: null,
      size_band: null,
      hq: null,
      parent_urn: null,
      fetched_at: NOW.toISOString(),
      source: "profile",
      parser_version: "1.0.0",
    },
    candidates: [],
    reason: "exact match",
    was_aliased: false,
    ...overrides,
  };
}

function exactUrnMatch(companyUrn = "urn:li:fsd_company:1035"): RoleMatch {
  return {
    kind: "current_exact_urn",
    experience: {
      company_urn: companyUrn,
      company_name: "Microsoft",
      title: "Software Engineer",
      start: "2023-01",
      end: null,
      is_current: true,
      employment_type: "full_time",
      location: null,
      description: null,
    },
    similarity: 1,
    detail: "current Software Engineer at Microsoft (urn match)",
  };
}

function noMatch(): RoleMatch {
  return { kind: "no_match", experience: null, similarity: 0, detail: "no role matched" };
}

describe("scoring rules", () => {
  test("exact URN match produces high score", () => {
    const profile = baseProfile({
      experience: [{
        company_urn: "urn:li:fsd_company:1035",
        company_name: "Microsoft",
        title: "Software Engineer",
        start: "2023-01",
        end: null,
        is_current: true,
        employment_type: "full_time" as const,
        location: null,
        description: null,
      }],
    });
    const ctx = { profile, resolution: baseResolution(), matches: [exactUrnMatch()], now: NOW };
    const { confidence, evidence } = score(ctx);
    assert.ok(confidence >= 65, `expected >= 65, got ${confidence}`);
    assert.ok(evidence.some(e => e.signal === "current_role_exact_urn"));
  });

  test("no match produces low score", () => {
    const ctx = { profile: baseProfile(), resolution: baseResolution({ method: "not_found", company: null }), matches: [noMatch()], now: NOW };
    const { confidence } = score(ctx);
    assert.ok(confidence <= 10, `expected <= 10, got ${confidence}`);
  });

  test("fresh profile adds positive weight", () => {
    const profile = baseProfile({ fetched_at: NOW.toISOString() });
    const ctx = { profile, resolution: baseResolution(), matches: [exactUrnMatch()], now: NOW };
    const { evidence } = score(ctx);
    const freshness = evidence.find(e => e.signal === "profile_freshness");
    assert.ok(freshness, "freshness signal should exist");
    assert.ok((freshness.weight as number) > 0, "fresh profile should add positive weight");
  });

  test("stale profile (>365d) adds negative weight", () => {
    const staleDate = new Date("2024-01-01T00:00:00Z").toISOString();
    const profile = baseProfile({ fetched_at: staleDate, profile_last_updated: staleDate });
    const ctx = { profile, resolution: baseResolution(), matches: [exactUrnMatch()], now: NOW };
    const { evidence } = score(ctx);
    const freshness = evidence.find(e => e.signal === "profile_freshness");
    assert.ok(freshness, "freshness signal should exist");
    assert.ok((freshness.weight as number) < 0, "stale profile should add negative weight");
  });

  test("multiple current roles adds penalty", () => {
    const profile = baseProfile({
      experience: [
        { company_urn: "urn:li:fsd_company:1035", company_name: "Microsoft", title: "SWE", start: "2023", end: null, is_current: true, employment_type: "full_time", location: null, description: null },
        { company_urn: "urn:li:fsd_company:1441", company_name: "Google", title: "SWE", start: "2023", end: null, is_current: true, employment_type: "full_time", location: null, description: null },
      ],
    });
    const ctx = { profile, resolution: baseResolution(), matches: [exactUrnMatch()], now: NOW };
    const { evidence } = score(ctx);
    assert.ok(evidence.some(e => e.signal === "multiple_current_roles"));
  });

  test("no experience flags unverifiable", () => {
    const profile = baseProfile({ experience: [] });
    const ctx = { profile, resolution: baseResolution(), matches: [noMatch()], now: NOW };
    const { evidence } = score(ctx);
    assert.ok(evidence.some(e => e.signal === "no_experience_data"));
  });

  test("confidence is clamped to 0-100", () => {
    const ctx = { profile: baseProfile(), resolution: baseResolution(), matches: [exactUrnMatch()], now: NOW };
    const { confidence } = score(ctx);
    assert.ok(confidence >= 0 && confidence <= 100);
  });
});

describe("verdict logic", () => {
  test("confidence >= 70 → yes", () => {
    const { verdict } = decide(75, baseResolution(), [exactUrnMatch()]);
    assert.equal(verdict, "yes");
  });

  test("confidence <= 30 → no", () => {
    const { verdict } = decide(20, baseResolution({ method: "not_found", company: null }), [noMatch()]);
    assert.equal(verdict, "no");
  });

  test("confidence in 31-69 → uncertain", () => {
    const { verdict } = decide(50, baseResolution(), [exactUrnMatch()]);
    assert.equal(verdict, "uncertain");
  });

  test("not_found adds uncertainty reason", () => {
    const { uncertainty } = decide(5, baseResolution({ method: "not_found", company: null }), [noMatch()]);
    assert.ok(uncertainty.reasons.some(r => r.includes("not found")));
  });

  test("no experience → unverifiable reason", () => {
    const profile = baseProfile({ experience: [] });
    const { uncertainty } = decide(0, baseResolution({ method: "not_found", company: null }), [noMatch()], profile);
    assert.ok(uncertainty.reasons.some(r => r.includes("unverifiable")));
  });

  test("ambiguous resolution → high uncertainty", () => {
    const res = baseResolution({ method: "ambiguous" });
    const { uncertainty } = decide(50, res, [exactUrnMatch()]);
    assert.ok(uncertainty.level === "high" || uncertainty.reasons.some(r => r.includes("ambiguous")));
  });
});
