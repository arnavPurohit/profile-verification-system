/** Map confidence + resolution + matches → final verdict and uncertainty level. Pure. */
import type { Evidence, Profile, Verdict } from "../domain/index.js";
import type { CompanyResolution } from "../resolve/companyResolver.js";
import type { RoleMatch } from "../match/roleMatcher.js";

export interface VerdictOutput {
  verdict: Verdict;
  uncertainty: { level: "low" | "medium" | "high"; reasons: string[] };
}

export function decide(
  confidence: number,
  resolution: CompanyResolution,
  matches: RoleMatch[],
  profile?: Profile,
  evidence?: Evidence[],
): VerdictOutput {
  const reasons: string[] = [];

  if (resolution.method === "ambiguous") reasons.push("company resolution ambiguous");
  if (resolution.method === "not_found") reasons.push("company not found in profile experience");
  if (matches[0]?.kind === "no_match") reasons.push("no role at this company in profile");
  const currents = matches.filter((m) =>
    ["current_exact_urn", "current_fuzzy_name", "current_parent_subsidiary"].includes(m.kind),
  );
  if (currents.length > 1) reasons.push(`multiple current roles match (${currents.length})`);

  if (profile) {
    if (profile.experience.length === 0) {
      reasons.push("profile has no experience entries — unverifiable");
    }
    const staleDays = staleness(profile);
    if (staleDays !== null && staleDays > 365) {
      reasons.push(`profile data is ${staleDays} days old — may be stale`);
    }
  }

  if (evidence) {
    const hasConflict = evidence.some((e) => e.signal === "headline_experience_conflict");
    if (hasConflict) reasons.push("headline contradicts experience data");

    const hasImpossible = evidence.some((e) => e.signal === "impossible_dates");
    if (hasImpossible) reasons.push("timeline contains impossible dates");

    const hasOverlap = evidence.some((e) => e.signal === "timeline_overlap");
    if (hasOverlap) reasons.push("timeline has significant overlapping roles at different companies");
  }

  let verdict: Verdict;
  if (confidence >= 70) verdict = "yes";
  else if (confidence <= 30) verdict = "no";
  else verdict = "uncertain";

  let level: "low" | "medium" | "high" = "low";
  if (reasons.length >= 2 || verdict === "uncertain") level = "high";
  else if (reasons.length === 1) level = "medium";

  return { verdict, uncertainty: { level, reasons } };
}

function staleness(profile: Profile): number | null {
  const ts = profile.profile_last_updated ?? profile.fetched_at;
  if (!ts) return null;
  return Math.floor((Date.now() - new Date(ts).getTime()) / 86400000);
}
