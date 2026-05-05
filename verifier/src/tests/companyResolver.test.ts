/**
 * Tests for profile-based company resolver.
 * Run: npx tsx --test src/tests/companyResolver.test.ts
 */
import { strict as assert } from "node:assert";
import { test, describe } from "node:test";
import { resolveCompanyFromProfile } from "../resolve/companyResolver.js";
import type { Profile } from "../domain/index.js";

function makeProfile(companies: Array<{ urn: string; name: string; current?: boolean }>): Profile {
  return {
    urn: "urn:li:fsd_profile:test",
    public_id: "test-user",
    url: "https://www.linkedin.com/in/test-user",
    name: "Test User",
    headline: null,
    location: null,
    experience: companies.map((c, i) => ({
      company_urn: c.urn,
      company_name: c.name,
      title: "Engineer",
      start: "2020-01",
      end: c.current === false ? "2022-01" : null,
      is_current: c.current !== false,
      employment_type: "full_time" as const,
      location: null,
      description: null,
    })),
    education: [],
    profile_last_updated: null,
    fetched_at: new Date().toISOString(),
    source: "fixture",
    parser_version: "1.0.0",
  };
}

describe("resolveCompanyFromProfile", () => {
  test("exact match by normalized name", () => {
    const profile = makeProfile([{ urn: "urn:li:fsd_company:1035", name: "Microsoft" }]);
    const r = resolveCompanyFromProfile("Microsoft", profile);
    assert.equal(r.method, "exact");
    assert.equal(r.company?.name, "Microsoft");
    assert.equal(r.company?.urn, "urn:li:fsd_company:1035");
  });

  test("fuzzy match for alternate branding (Microsoft Corp)", () => {
    const profile = makeProfile([{ urn: "urn:li:fsd_company:1035", name: "Microsoft" }]);
    const r = resolveCompanyFromProfile("Microsoft Corp", profile);
    assert.ok(r.company !== null, "should resolve company");
    assert.equal(r.company?.urn, "urn:li:fsd_company:1035");
  });

  test("alias map: Facebook → Meta", () => {
    const profile = makeProfile([{ urn: "urn:li:fsd_company:10667", name: "Meta" }]);
    const r = resolveCompanyFromProfile("Facebook", profile);
    assert.ok(r.company !== null, "should resolve via alias");
    assert.equal(r.was_aliased, true);
  });

  test("alias map: Alphabet → Google", () => {
    const profile = makeProfile([{ urn: "urn:li:fsd_company:1441", name: "Google" }]);
    const r = resolveCompanyFromProfile("Alphabet", profile);
    assert.ok(r.company !== null, "should resolve via alias");
    assert.equal(r.was_aliased, true);
  });

  test("not_found when company not in profile", () => {
    const profile = makeProfile([{ urn: "urn:li:fsd_company:1035", name: "Microsoft" }]);
    const r = resolveCompanyFromProfile("Fareye", profile);
    assert.equal(r.method, "not_found");
    assert.equal(r.company, null);
  });

  test("not_found for empty experience", () => {
    const profile = makeProfile([]);
    const r = resolveCompanyFromProfile("Microsoft", profile);
    assert.equal(r.method, "not_found");
    assert.equal(r.company, null);
  });

  test("deduplicates companies by URN", () => {
    const profile = makeProfile([
      { urn: "urn:li:fsd_company:1035", name: "Microsoft", current: true },
      { urn: "urn:li:fsd_company:1035", name: "Microsoft", current: false },
    ]);
    const r = resolveCompanyFromProfile("Microsoft", profile);
    assert.equal(r.candidates.length, 1);
  });

  test("picks best match when multiple companies in profile", () => {
    const profile = makeProfile([
      { urn: "urn:li:fsd_company:1035", name: "Microsoft" },
      { urn: "urn:li:fsd_company:1441", name: "Google" },
      { urn: "urn:li:fsd_company:10667", name: "Meta" },
    ]);
    const r = resolveCompanyFromProfile("Google", profile);
    assert.equal(r.company?.name, "Google");
  });
});
