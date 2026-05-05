/**
 * Company resolution: user-provided company name → Company entity.
 *
 * Resolves entirely from the profile's experience entries -- no LinkedIn
 * company search API call needed.  Stages:
 *
 *   1. Normalize the query (strip suffixes, apply alias map)
 *   2. Build candidate Company objects from profile experience entries
 *   3. Fuzzy-match (token-set ratio) each candidate against the query
 *   4. Pick the best match or mark as not_found / ambiguous
 *
 * Pure function (given a profile). No external IO.
 */
import type { Company, Experience, Profile } from "../domain/index.js";
import { normalizeCompany, tokenSetRatio } from "../normalize/company.js";

export type ResolutionMethod =
  | "alias"
  | "exact"
  | "fuzzy_dominant"
  | "ambiguous"
  | "not_found";

export interface CompanyResolution {
  query: string;
  normalized: string;
  method: ResolutionMethod;
  company: Company | null;
  candidates: Company[];
  reason: string;
  was_aliased: boolean;
}

const FUZZY_DOMINANT_THRESHOLD = 0.5;
const FUZZY_GAP_THRESHOLD = 0.15;

/**
 * Resolve company from profile experience entries.
 * No network calls -- works entirely from already-scraped profile data.
 */
export function resolveCompanyFromProfile(
  query: string,
  profile: Profile,
): CompanyResolution {
  const norm = normalizeCompany(query);

  const candidates = buildCandidatesFromExperience(profile.experience);

  if (candidates.length === 0) {
    return {
      query,
      normalized: norm.normalized,
      method: "not_found",
      company: null,
      candidates: [],
      reason: "profile has no experience entries to match against",
      was_aliased: norm.was_aliased,
    };
  }

  const exact = candidates.find((c) => c.normalized === norm.normalized);
  if (exact) {
    return {
      query,
      normalized: norm.normalized,
      method: norm.was_aliased ? "alias" : "exact",
      company: exact,
      candidates,
      reason: norm.was_aliased
        ? `aliased "${query}" → "${norm.normalized}", exact match in profile experience`
        : "exact match on normalized name in profile experience",
      was_aliased: norm.was_aliased,
    };
  }

  const scored = candidates
    .map((c) => ({
      company: c,
      score: Math.max(
        tokenSetRatio(norm.normalized, c.normalized),
        ...c.aliases.map((a) => tokenSetRatio(norm.normalized, a.toLowerCase())),
      ),
    }))
    .sort((a, b) => b.score - a.score);

  const top = scored[0]!;
  const second = scored[1];

  const gap = top.score - (second?.score ?? 0);
  if (top.score >= FUZZY_DOMINANT_THRESHOLD && gap >= FUZZY_GAP_THRESHOLD) {
    return {
      query,
      normalized: norm.normalized,
      method: "fuzzy_dominant",
      company: top.company,
      candidates,
      reason: `fuzzy match score=${top.score.toFixed(2)} gap=${gap.toFixed(2)} from profile experience`,
      was_aliased: norm.was_aliased,
    };
  }

  if (top.score >= FUZZY_DOMINANT_THRESHOLD) {
    return {
      query,
      normalized: norm.normalized,
      method: "ambiguous",
      company: top.company,
      candidates,
      reason: `best fuzzy score=${top.score.toFixed(2)} but gap=${gap.toFixed(2)} is narrow — ambiguous`,
      was_aliased: norm.was_aliased,
    };
  }

  return {
    query,
    normalized: norm.normalized,
    method: "not_found",
    company: null,
    candidates,
    reason: `no experience entry matched "${norm.normalized}" (best score=${top.score.toFixed(2)})`,
    was_aliased: norm.was_aliased,
  };
}

/**
 * Build Company objects from unique companies found in profile experience.
 * Groups by company_urn (or company_name if no URN) to deduplicate.
 */
function buildCandidatesFromExperience(experience: Experience[]): Company[] {
  const seen = new Map<string, Company>();
  const now = new Date().toISOString();

  for (const exp of experience) {
    const key = exp.company_urn || exp.company_name.toLowerCase();
    if (seen.has(key)) continue;

    const normResult = normalizeCompany(exp.company_name);
    seen.set(key, {
      urn: exp.company_urn || `urn:li:fsd_company:unknown_${normResult.normalized.replace(/\s+/g, "_")}`,
      name: exp.company_name,
      normalized: normResult.normalized,
      aliases: normResult.was_aliased ? [normResult.raw] : [],
      website: null,
      industry: null,
      size_band: null,
      hq: null,
      parent_urn: null,
      fetched_at: now,
      source: "profile",
      parser_version: "1.0.0",
    });
  }

  return Array.from(seen.values());
}
