/**
 * Tests for role matching logic.
 * Run: npx tsx --test src/tests/roleMatcher.test.ts
 */
import { strict as assert } from "node:assert";
import { test, describe } from "node:test";
import { matchRoles } from "../match/roleMatcher.js";
import type { Company, Profile } from "../domain/index.js";

const NOW = new Date("2026-05-05T00:00:00Z");

const MICROSOFT: Company = {
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
};

function makeProfile(experience: Profile["experience"]): Profile {
  return {
    urn: "urn:li:fsd_profile:test",
    public_id: "test",
    url: null,
    name: "Test",
    headline: null,
    location: null,
    experience,
    education: [],
    profile_last_updated: null,
    fetched_at: NOW.toISOString(),
    source: "fixture",
    parser_version: "1.0.0",
  };
}

describe("matchRoles", () => {
  test("returns no_match when company is null", () => {
    const profile = makeProfile([]);
    const matches = matchRoles(profile, null, NOW);
    assert.equal(matches[0]?.kind, "no_match");
  });

  test("current_exact_urn when URNs match and role is current", () => {
    const profile = makeProfile([{
      company_urn: "urn:li:fsd_company:1035",
      company_name: "Microsoft",
      title: "SWE",
      start: "2022-01",
      end: null,
      is_current: true,
      employment_type: "full_time",
      location: null,
      description: null,
    }]);
    const matches = matchRoles(profile, MICROSOFT, NOW);
    assert.equal(matches[0]?.kind, "current_exact_urn");
    assert.equal(matches[0]?.similarity, 1);
  });

  test("current_fuzzy_name when name matches but no URN", () => {
    const profile = makeProfile([{
      company_urn: null,
      company_name: "Microsoft Corporation",
      title: "PM",
      start: "2023",
      end: null,
      is_current: true,
      employment_type: "full_time",
      location: null,
      description: null,
    }]);
    const matches = matchRoles(profile, MICROSOFT, NOW);
    assert.equal(matches[0]?.kind, "current_fuzzy_name");
  });

  test("ended_recent_at_company for role that ended within 90 days", () => {
    const recentEnd = new Date(NOW.getTime() - 30 * 86400000);
    const endStr = `${recentEnd.getFullYear()}-${String(recentEnd.getMonth() + 1).padStart(2, "0")}`;
    const profile = makeProfile([{
      company_urn: "urn:li:fsd_company:1035",
      company_name: "Microsoft",
      title: "SWE",
      start: "2022-01",
      end: endStr,
      is_current: false,
      employment_type: "full_time",
      location: null,
      description: null,
    }]);
    const matches = matchRoles(profile, MICROSOFT, NOW);
    assert.equal(matches[0]?.kind, "ended_recent_at_company");
  });

  test("ended_old_at_company for role that ended >90 days ago", () => {
    const profile = makeProfile([{
      company_urn: "urn:li:fsd_company:1035",
      company_name: "Microsoft",
      title: "SWE",
      start: "2018-01",
      end: "2020-01",
      is_current: false,
      employment_type: "full_time",
      location: null,
      description: null,
    }]);
    const matches = matchRoles(profile, MICROSOFT, NOW);
    assert.equal(matches[0]?.kind, "ended_old_at_company");
  });

  test("no_match when company not in experience", () => {
    const profile = makeProfile([{
      company_urn: "urn:li:fsd_company:9999",
      company_name: "Fareye",
      title: "SWE",
      start: "2022",
      end: null,
      is_current: true,
      employment_type: "full_time",
      location: null,
      description: null,
    }]);
    const matches = matchRoles(profile, MICROSOFT, NOW);
    assert.equal(matches[0]?.kind, "no_match");
  });
});
