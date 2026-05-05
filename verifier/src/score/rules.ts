/**
 * Scoring rule registry. Open/Closed in action.
 *
 * Each rule is a pure function `(ctx) => Evidence | null`. The scorer
 * iterates the registry, sums weights, collects evidence. Adding a new
 * signal is appending to this list — no changes to the scorer.
 */
import type { Evidence, Profile } from "../domain/index.js";
import type { CompanyResolution } from "../resolve/companyResolver.js";
import type { RoleMatch } from "../match/roleMatcher.js";

export interface ScoringContext {
  profile: Profile;
  resolution: CompanyResolution;
  matches: RoleMatch[];
  now: Date;
}

export type ScoringRule = (ctx: ScoringContext) => Evidence | null;

export const SCORER_VERSION = "1.0.0";

// ---------- individual rules ----------

const currentRoleExactUrn: ScoringRule = (ctx) => {
  const m = ctx.matches.find((m) => m.kind === "current_exact_urn");
  if (!m || !m.experience) return null;
  return {
    signal: "current_role_exact_urn",
    weight: 65,
    value: m.experience.company_urn ?? "",
    note: m.detail,
  };
};

const currentRoleFuzzyName: ScoringRule = (ctx) => {
  if (ctx.matches.some((m) => m.kind === "current_exact_urn")) return null;
  const m = ctx.matches.find((m) => m.kind === "current_fuzzy_name");
  if (!m || !m.experience) return null;
  return {
    signal: "current_role_fuzzy_name",
    weight: 25,
    value: m.experience.company_name,
    note: m.detail,
  };
};

const parentSubsidiary: ScoringRule = (ctx) => {
  const m = ctx.matches.find((m) => m.kind === "current_parent_subsidiary");
  if (!m || !m.experience) return null;
  return {
    signal: "parent_subsidiary_match",
    weight: 15,
    value: m.experience.company_name,
    note: m.detail,
  };
};

const endedRecent: ScoringRule = (ctx) => {
  const hasCurrent = ctx.matches.some((m) =>
    ["current_exact_urn", "current_fuzzy_name", "current_parent_subsidiary"].includes(m.kind),
  );
  if (hasCurrent) return null;
  const m = ctx.matches.find((m) => m.kind === "ended_recent_at_company");
  if (!m || !m.experience) return null;
  return {
    signal: "ended_recent_at_company",
    weight: 5,
    value: m.experience.end ?? "",
    note: m.detail,
  };
};

const endedOld: ScoringRule = (ctx) => {
  const hasCurrent = ctx.matches.some((m) =>
    ["current_exact_urn", "current_fuzzy_name", "current_parent_subsidiary"].includes(m.kind),
  );
  if (hasCurrent) return null;
  const m = ctx.matches.find((m) => m.kind === "ended_old_at_company");
  if (!m || !m.experience) return null;
  return {
    signal: "ended_old_at_company",
    weight: -10,
    value: m.experience.end ?? "unknown",
    note: m.detail,
  };
};

const noMatch: ScoringRule = (ctx) => {
  if (ctx.matches[0]?.kind !== "no_match") return null;
  return { signal: "no_matching_role", weight: -50, value: true, note: ctx.matches[0].detail };
};

const profileFreshness: ScoringRule = (ctx) => {
  const ts = ctx.profile.profile_last_updated ?? ctx.profile.fetched_at;
  if (!ts) return { signal: "profile_freshness", weight: -10, value: "unknown", note: "no last-updated timestamp" };
  const ageMs = ctx.now.getTime() - new Date(ts).getTime();
  const days = Math.floor(ageMs / 86400000);
  let weight = 0;
  if (days <= 90) weight = 10;
  else if (days <= 365) weight = 0;
  else weight = -10;
  return {
    signal: "profile_freshness",
    weight,
    value: `${days}d`,
    note: `last update ${days}d ago`,
  };
};

const multipleCurrentRoles: ScoringRule = (ctx) => {
  const currents = ctx.profile.experience.filter((e) => e.is_current);
  if (currents.length <= 1) return null;
  return {
    signal: "multiple_current_roles",
    weight: -10,
    value: currents.length,
    note: `${currents.length} current roles in profile`,
  };
};

const contractorOrAdvisor: ScoringRule = (ctx) => {
  const matched = ctx.matches.find(
    (m) => m.experience && ["contractor", "advisor", "intern", "freelance"].includes(m.experience.employment_type),
  );
  if (!matched || !matched.experience) return null;
  return {
    signal: "non_full_time_role",
    weight: -5,
    value: matched.experience.employment_type,
    note: `role type=${matched.experience.employment_type}; surfaced as flag`,
  };
};

const headlineMentionsCompany: ScoringRule = (ctx) => {
  const company = ctx.resolution.company;
  const headline = ctx.profile.headline;
  if (!company || !headline) return null;
  const lower = headline.toLowerCase();
  if (lower.includes(company.name.toLowerCase()) || company.aliases.some((a) => lower.includes(a.toLowerCase()))) {
    return { signal: "headline_mentions_company", weight: 5, value: headline, note: "company name appears in headline" };
  }
  return null;
};

const ambiguousResolution: ScoringRule = (ctx) => {
  if (ctx.resolution.method !== "ambiguous") return null;
  return {
    signal: "company_resolution_ambiguous",
    weight: -10,
    value: ctx.resolution.method,
    note: ctx.resolution.reason,
  };
};

const aliasNote: ScoringRule = (ctx) => {
  if (!ctx.resolution.was_aliased) return null;
  return {
    signal: "company_alias_hit",
    weight: 0,
    value: `${ctx.resolution.query} → ${ctx.resolution.normalized}`,
    note: "resolved via alias map",
  };
};

// ---------- timeline consistency ----------

function parseToDate(s: string | null): Date | null {
  if (!s) return null;
  const m = s.match(/^(\d{4})(?:-(\d{2}))?$/);
  if (!m) return null;
  return new Date(Date.UTC(Number(m[1]), m[2] ? Number(m[2]) - 1 : 0, 1));
}

const timelineOverlap: ScoringRule = (ctx) => {
  const roles = ctx.profile.experience;
  if (roles.length < 2) return null;

  let overlaps = 0;
  const details: string[] = [];

  for (let i = 0; i < roles.length; i++) {
    const a = roles[i]!;
    for (let j = i + 1; j < roles.length; j++) {
      const b = roles[j]!;
      if (a.company_urn && a.company_urn === b.company_urn) continue;
      if (a.company_name === b.company_name) continue;

      const aStart = parseToDate(a.start);
      const aEnd = a.is_current ? ctx.now : parseToDate(a.end);
      const bStart = parseToDate(b.start);
      const bEnd = b.is_current ? ctx.now : parseToDate(b.end);

      if (!aStart || !aEnd || !bStart || !bEnd) continue;
      if (aStart < bEnd && bStart < aEnd) {
        const overlapMs = Math.min(aEnd.getTime(), bEnd.getTime()) - Math.max(aStart.getTime(), bStart.getTime());
        const overlapDays = Math.round(overlapMs / 86400000);
        if (overlapDays > 365) {
          overlaps++;
          if (details.length < 3) {
            details.push(`${a.title}@${a.company_name} overlaps ${b.title}@${b.company_name} by ~${overlapDays}d`);
          }
        }
      }
    }
  }

  if (overlaps === 0) return null;
  return {
    signal: "timeline_overlap",
    weight: -5,
    value: overlaps,
    note: details.join("; "),
  };
};

const impossibleDates: ScoringRule = (ctx) => {
  const issues: string[] = [];
  for (const exp of ctx.profile.experience) {
    const start = parseToDate(exp.start);
    const end = exp.is_current ? null : parseToDate(exp.end);
    if (start && end && start > end) {
      issues.push(`${exp.title}@${exp.company_name}: start ${exp.start} > end ${exp.end}`);
    }
    if (start && start.getFullYear() < 1950) {
      issues.push(`${exp.title}@${exp.company_name}: implausible start year ${exp.start}`);
    }
  }
  if (issues.length === 0) return null;
  return {
    signal: "impossible_dates",
    weight: -10,
    value: issues.length,
    note: issues.slice(0, 3).join("; "),
  };
};

// ---------- conflict detection ----------

const headlineExperienceConflict: ScoringRule = (ctx) => {
  const company = ctx.resolution.company;
  const headline = ctx.profile.headline;
  if (!company || !headline) return null;

  const lower = headline.toLowerCase();
  const companyInHeadline = lower.includes(company.name.toLowerCase()) ||
    company.aliases.some((a) => lower.includes(a.toLowerCase()));

  const hasCurrent = ctx.matches.some((m) =>
    ["current_exact_urn", "current_fuzzy_name", "current_parent_subsidiary"].includes(m.kind),
  );

  if (companyInHeadline && !hasCurrent) {
    return {
      signal: "headline_experience_conflict",
      weight: -5,
      value: headline,
      note: `headline mentions ${company.name} but no current role in experience matches`,
    };
  }

  if (!companyInHeadline && hasCurrent) {
    const currentMatch = ctx.matches.find((m) =>
      ["current_exact_urn", "current_fuzzy_name", "current_parent_subsidiary"].includes(m.kind),
    );
    const otherCo = currentMatch?.experience?.company_name;
    if (otherCo && !lower.includes(otherCo.toLowerCase())) {
      return {
        signal: "headline_experience_conflict",
        weight: -5,
        value: headline,
        note: `experience shows current role at ${otherCo} but headline does not mention it`,
      };
    }
  }

  return null;
};

// ---------- insufficient data ----------

const insufficientData: ScoringRule = (ctx) => {
  const exp = ctx.profile.experience;
  if (exp.length === 0) {
    return {
      signal: "no_experience_data",
      weight: -20,
      value: true,
      note: "profile has no experience entries — cannot verify employment",
    };
  }
  const allMissingCompany = exp.every((e) => !e.company_name || e.company_name.trim() === "");
  if (allMissingCompany) {
    return {
      signal: "no_company_names",
      weight: -15,
      value: true,
      note: "all experience entries lack company names — verification unreliable",
    };
  }
  return null;
};

// ---------- registry ----------

export const RULES: ScoringRule[] = [
  currentRoleExactUrn,
  currentRoleFuzzyName,
  parentSubsidiary,
  endedRecent,
  endedOld,
  noMatch,
  profileFreshness,
  multipleCurrentRoles,
  contractorOrAdvisor,
  headlineMentionsCompany,
  ambiguousResolution,
  aliasNote,
  timelineOverlap,
  impossibleDates,
  headlineExperienceConflict,
  insufficientData,
];
