/**
 * Role-to-company matching. Pure function. No IO.
 *
 * Given a profile and a resolved company URN, classify each experience
 * entry's relationship to that company. The scorer turns these
 * classifications into evidence + weights.
 */
import type { Company, Experience, Profile } from "../domain/index.js";
import { normalizeCompany, tokenSetRatio } from "../normalize/company.js";

export type RoleMatchKind =
  | "current_exact_urn"
  | "current_fuzzy_name"
  | "current_parent_subsidiary"
  | "ended_recent_at_company"
  | "ended_old_at_company"
  | "no_match";

export interface RoleMatch {
  kind: RoleMatchKind;
  experience: Experience | null;
  similarity: number; // 0..1
  detail: string;
}

const FUZZY_NAME_THRESHOLD = 0.6;
const RECENT_END_DAYS = 90;

export function matchRoles(
  profile: Profile,
  company: Company | null,
  now: Date = new Date(),
): RoleMatch[] {
  if (!company) return [{ kind: "no_match", experience: null, similarity: 0, detail: "no company resolved" }];

  const matches: RoleMatch[] = [];
  const compNorm = company.normalized;
  const compName = company.name.toLowerCase();
  const allCompTokens = [compName, ...company.aliases.map((a) => a.toLowerCase())];

  for (const exp of profile.experience) {
    const expNameNorm = normalizeCompany(exp.company_name).normalized;
    const tokenSim = Math.max(
      tokenSetRatio(expNameNorm, compNorm),
      ...allCompTokens.map((t) => tokenSetRatio(expNameNorm, t)),
    );

    const urnHit =
      !!exp.company_urn && !!company.urn && exp.company_urn === company.urn;
    const parentHit =
      !!exp.company_urn &&
      !!company.parent_urn &&
      exp.company_urn === company.parent_urn;
    const nameHit = tokenSim >= FUZZY_NAME_THRESHOLD;

    if (!urnHit && !parentHit && !nameHit) continue;

    if (exp.is_current) {
      if (urnHit) {
        matches.push({
          kind: "current_exact_urn",
          experience: exp,
          similarity: 1,
          detail: `current ${exp.title} at ${exp.company_name} (urn match)`,
        });
        continue;
      }
      if (parentHit) {
        matches.push({
          kind: "current_parent_subsidiary",
          experience: exp,
          similarity: 0.9,
          detail: `current ${exp.title} at ${exp.company_name} (parent/subsidiary of ${company.name})`,
        });
        continue;
      }
      matches.push({
        kind: "current_fuzzy_name",
        experience: exp,
        similarity: tokenSim,
        detail: `current ${exp.title} at ${exp.company_name} (fuzzy name=${tokenSim.toFixed(2)})`,
      });
      continue;
    }

    // Ended role at the company. How long ago?
    const ended = parseYearMonth(exp.end);
    if (!ended) {
      matches.push({
        kind: "ended_old_at_company",
        experience: exp,
        similarity: tokenSim,
        detail: `ended role at ${exp.company_name}, end date unknown`,
      });
      continue;
    }
    const daysSince = Math.max(0, (now.getTime() - ended.getTime()) / 86400000);
    matches.push({
      kind: daysSince <= RECENT_END_DAYS ? "ended_recent_at_company" : "ended_old_at_company",
      experience: exp,
      similarity: tokenSim,
      detail: `ended role at ${exp.company_name} ${Math.round(daysSince)}d ago`,
    });
  }

  if (matches.length === 0) {
    return [{ kind: "no_match", experience: null, similarity: 0, detail: "no role matched" }];
  }
  return matches;
}

function parseYearMonth(s: string | null): Date | null {
  if (!s) return null;
  const m = s.match(/^(\d{4})(?:-(\d{2}))?$/);
  if (!m) return null;
  const year = Number(m[1]);
  const month = m[2] ? Number(m[2]) - 1 : 0;
  return new Date(Date.UTC(year, month, 1));
}
