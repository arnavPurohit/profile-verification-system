/**
 * LLM-powered employment verification.
 *
 * Called only when fuzzy matching is inconclusive. Sends scraped profile data
 * + user-provided company name to Claude and gets back structured verification
 * output: verdict, confidence adjustment, normalized company info, issues.
 */
import Anthropic from "@anthropic-ai/sdk";
import type { Experience, Profile } from "../domain/index.js";

export interface LlmVerifyInput {
  profile: Profile;
  companyQuery: string;
  fuzzyBestScore: number;
  fuzzyBestCompany: string | null;
}

export interface LlmVerifyOutput {
  verified: boolean;
  confidenceAdjustment: number;
  normalizedCompany: {
    name: string;
    aliases: string[];
    industry: string | null;
  } | null;
  issues: string[];
  reasoning: string;
}

export interface LlmVerifier {
  verify(input: LlmVerifyInput): Promise<LlmVerifyOutput>;
}

export class NoopVerifier implements LlmVerifier {
  async verify(_input: LlmVerifyInput): Promise<LlmVerifyOutput> {
    return {
      verified: false,
      confidenceAdjustment: 0,
      normalizedCompany: null,
      issues: ["LLM verification unavailable (no API key configured)"],
      reasoning: "LLM disabled — relying on rules-based scoring only",
    };
  }
}

export class AnthropicVerifier implements LlmVerifier {
  private readonly client: Anthropic;
  constructor(
    apiKey: string,
    private readonly model: string,
  ) {
    this.client = new Anthropic({ apiKey });
  }

  async verify(input: LlmVerifyInput): Promise<LlmVerifyOutput> {
    const experienceSummary = input.profile.experience
      .map((e) => formatExperience(e))
      .join("\n");

    const prompt = `You are an employment verification analyst. Given a LinkedIn profile and a company name, determine whether this person currently works at that company.

PROFILE:
- Name: ${input.profile.name}
- Headline: ${input.profile.headline ?? "N/A"}
- Location: ${input.profile.location ?? "N/A"}
- Profile last updated: ${input.profile.profile_last_updated ?? "unknown"}
- Fetched: ${input.profile.fetched_at}

EXPERIENCE (most recent first):
${experienceSummary || "(no experience listed)"}

COMPANY TO VERIFY: "${input.companyQuery}"

FUZZY MATCH CONTEXT: Best match score was ${input.fuzzyBestScore.toFixed(2)} against "${input.fuzzyBestCompany ?? "none"}".

INSTRUCTIONS:
1. Determine if this person currently works at "${input.companyQuery}" (consider parent companies, subsidiaries, brand names — e.g. Google = Alphabet, Instagram = Meta).
2. Normalize the company name and list known aliases.
3. Identify any issues: outdated profile, multiple concurrent roles, contractor/advisor ambiguity, conflicting headline vs experience, stale data.
4. Provide a confidence adjustment between -30 and +30 (positive = more confident they work there, negative = less confident).

Respond with ONLY valid JSON (no markdown, no explanation outside JSON):
{
  "verified": true/false,
  "confidence_adjustment": <number between -30 and 30>,
  "normalized_company": { "name": "...", "aliases": ["..."], "industry": "..." } or null,
  "issues": ["issue1", "issue2"],
  "reasoning": "one paragraph explaining your analysis"
}`;

    try {
      const resp = await this.client.messages.create({
        model: this.model,
        max_tokens: 800,
        messages: [{ role: "user", content: prompt }],
      });

      const text = resp.content
        .filter((c): c is Anthropic.TextBlock => c.type === "text")
        .map((c) => c.text)
        .join("\n")
        .trim();

      return parseLlmResponse(text);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        verified: false,
        confidenceAdjustment: 0,
        normalizedCompany: null,
        issues: [`LLM call failed: ${msg}`],
        reasoning: "LLM verification failed — falling back to rules-based scoring",
      };
    }
  }
}

function formatExperience(e: Experience): string {
  const status = e.is_current ? "[CURRENT]" : "[ENDED]";
  const period = [e.start, e.end ?? (e.is_current ? "present" : "unknown")].filter(Boolean).join(" – ");
  const type = e.employment_type !== "unknown" ? ` (${e.employment_type})` : "";
  return `  ${status} ${e.title} at ${e.company_name}${type}, ${period}`;
}

function parseLlmResponse(text: string): LlmVerifyOutput {
  const jsonMatch = text.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    return {
      verified: false,
      confidenceAdjustment: 0,
      normalizedCompany: null,
      issues: ["LLM returned non-JSON response"],
      reasoning: text.slice(0, 500),
    };
  }

  try {
    const parsed = JSON.parse(jsonMatch[0]) as Record<string, unknown>;

    const normalizedCompany = parsed.normalized_company as {
      name?: string;
      aliases?: string[];
      industry?: string | null;
    } | null;

    return {
      verified: Boolean(parsed.verified),
      confidenceAdjustment: clamp(Number(parsed.confidence_adjustment) || 0, -30, 30),
      normalizedCompany: normalizedCompany
        ? {
            name: normalizedCompany.name ?? "",
            aliases: Array.isArray(normalizedCompany.aliases) ? normalizedCompany.aliases : [],
            industry: normalizedCompany.industry ?? null,
          }
        : null,
      issues: Array.isArray(parsed.issues) ? (parsed.issues as string[]) : [],
      reasoning: String(parsed.reasoning ?? ""),
    };
  } catch {
    return {
      verified: false,
      confidenceAdjustment: 0,
      normalizedCompany: null,
      issues: ["LLM returned invalid JSON"],
      reasoning: text.slice(0, 500),
    };
  }
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}
