/**
 * The verify pipeline. Composition of pure stages + injected IO.
 *
 * Flow:
 *   1. Normalize URL
 *   2. Fetch profile (cache-first via fetcher service)
 *   3. Resolve company from profile experience entries (no LinkedIn search)
 *   4. Match roles against resolved company
 *   5. If match is weak → call LLM for enrichment & verification
 *   6. Score (rules + optional LLM adjustment) → decide verdict
 *   7. Audit log
 */
import { randomUUID } from "node:crypto";

import type {
  Company,
  Evidence,
  Profile,
  VerifyInput,
  VerifyResult,
} from "../domain/index.js";
import type { FetcherClient } from "../fetcher/client.js";
import type { LlmVerifier, LlmVerifyOutput } from "../llm/verifier.js";
import { matchRoles, type RoleMatch } from "../match/roleMatcher.js";
import { normalizeUrl } from "../normalize/url.js";
import { resolveCompanyFromProfile, type CompanyResolution } from "../resolve/companyResolver.js";
import { SCORER_VERSION, decide, score } from "../score/index.js";
import type { VerificationsRepo } from "../storage/verifications.js";

const STRONG_MATCH_THRESHOLD = 0.7;

export interface VerifyDeps {
  fetcher: FetcherClient;
  llm: LlmVerifier;
  repo: VerificationsRepo;
  now: () => Date;
  newId?: () => string;
}

export type VerifyPipeline = (input: VerifyInput) => Promise<VerifyResult>;

export function buildVerifyPipeline(deps: VerifyDeps): VerifyPipeline {
  const newId = deps.newId ?? randomUUID;

  return async function verify(input: VerifyInput): Promise<VerifyResult> {
    // 1. Normalize URL.
    const norm = normalizeUrl(input.url);

    // 2. Fetch profile (cache-first inside the fetcher service).
    const { profile } = await deps.fetcher.fetchProfile(norm.canonical, input.max_age_days);

    // 3. Resolve company from profile experience (no LinkedIn search).
    const resolution = resolveCompanyFromProfile(input.company, profile);

    // 4. Match roles against the resolved company.
    const matches = matchRoles(profile, resolution.company, deps.now());

    // 5. Determine if match is strong enough or if we need LLM help.
    const bestMatch = matches.find((m) =>
      ["current_exact_urn", "current_fuzzy_name", "current_parent_subsidiary"].includes(m.kind),
    );
    const isStrongMatch = bestMatch && bestMatch.similarity >= STRONG_MATCH_THRESHOLD;

    let llmResult: LlmVerifyOutput | null = null;
    if (!isStrongMatch) {
      llmResult = await deps.llm.verify({
        profile,
        companyQuery: input.company,
        fuzzyBestScore: matches[0]?.similarity ?? 0,
        fuzzyBestCompany: matches[0]?.experience?.company_name ?? null,
      });

      if (llmResult.normalizedCompany && resolution.company) {
        resolution.company = {
          ...resolution.company,
          industry: llmResult.normalizedCompany.industry ?? resolution.company.industry,
          aliases: [
            ...new Set([
              ...resolution.company.aliases,
              ...llmResult.normalizedCompany.aliases,
            ]),
          ],
        };
      }
    }

    // 6. Score and decide.
    const { confidence: baseConfidence, evidence } = score({
      profile,
      resolution,
      matches,
      now: deps.now(),
    });

    if (llmResult) {
      evidence.push({
        signal: "llm_verification",
        weight: llmResult.confidenceAdjustment,
        value: llmResult.verified,
        note: llmResult.reasoning,
      });
      for (const issue of llmResult.issues) {
        evidence.push({
          signal: "llm_issue",
          weight: 0,
          value: issue,
          note: "flagged by LLM analysis",
        });
      }
    }

    const totalWeight = evidence.reduce((a, e) => a + e.weight, 0);
    const confidence = Math.max(0, Math.min(100, totalWeight));

    const { verdict, uncertainty } = decide(confidence, resolution, matches, profile, evidence);

    if (llmResult) {
      for (const issue of llmResult.issues) {
        if (!uncertainty.reasons.includes(issue)) {
          uncertainty.reasons.push(issue);
        }
      }
    }

    // 7. Assemble result.
    const result: VerifyResult = {
      verdict,
      confidence,
      evidence,
      person: enrichProfile(profile),
      company: resolution.company,
      uncertainty,
      scorer_version: SCORER_VERSION,
      request_id: newId(),
    };

    // 8. Audit log.
    await deps.repo.record(input, result);

    return result;
  };
}

/**
 * Add derived fields the API consumer cares about. Pure.
 */
function enrichProfile(p: Profile): Profile & {
  current_roles: Profile["experience"];
  past_roles: Profile["experience"];
  inferred_seniority: "junior" | "mid" | "senior" | "exec" | "unknown";
  data_freshness_days: number | null;
} {
  const current = p.experience.filter((e) => e.is_current);
  const past = p.experience.filter((e) => !e.is_current);
  return {
    ...p,
    current_roles: current,
    past_roles: past,
    inferred_seniority: inferSeniority(current.map((e) => e.title)),
    data_freshness_days: ageDays(p.profile_last_updated ?? p.fetched_at),
  };
}

function inferSeniority(titles: string[]): "junior" | "mid" | "senior" | "exec" | "unknown" {
  const t = titles.join(" ").toLowerCase();
  if (/(chief|cto|cfo|ceo|coo|founder|president|vp\b|vice president)/.test(t)) return "exec";
  if (/(head of|director|principal|staff|sr\.?|senior|lead)/.test(t)) return "senior";
  if (/(intern|graduate|trainee|junior|entry)/.test(t)) return "junior";
  if (titles.length === 0) return "unknown";
  return "mid";
}

function ageDays(ts: string | null): number | null {
  if (!ts) return null;
  const ms = Date.now() - new Date(ts).getTime();
  return Math.max(0, Math.floor(ms / 86400000));
}
