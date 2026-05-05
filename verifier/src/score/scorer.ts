/**
 * Apply rules → produce evidence list + confidence score.
 *
 * Pure. Given the same context, always returns the same evidence and score.
 */
import type { Evidence } from "../domain/index.js";
import { RULES, ScoringContext } from "./rules.js";

export interface ScoreOutput {
  confidence: number;
  evidence: Evidence[];
}

export function score(ctx: ScoringContext): ScoreOutput {
  const evidence: Evidence[] = [];
  for (const rule of RULES) {
    const ev = rule(ctx);
    if (ev) evidence.push(ev);
  }
  const sum = evidence.reduce((a, e) => a + e.weight, 0);
  const confidence = Math.max(0, Math.min(100, sum));
  return { confidence, evidence };
}
