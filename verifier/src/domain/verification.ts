import type { Company } from "./company.js";
import type { Evidence } from "./evidence.js";
import type { Profile } from "./profile.js";

export type Verdict = "yes" | "no" | "uncertain";

export interface VerifyInput {
  url: string;
  company: string;
  max_age_days?: number;
}

export interface VerifyResult {
  verdict: Verdict;
  confidence: number;
  evidence: Evidence[];
  person: Profile;
  company: Company | null;
  uncertainty: { level: "low" | "medium" | "high"; reasons: string[] };
  scorer_version: string;
  request_id: string;
}
