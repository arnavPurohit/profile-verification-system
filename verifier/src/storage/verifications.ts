import type { Db } from "mongodb";
import type { VerifyInput, VerifyResult } from "../domain/index.js";

export interface VerificationsRepo {
  record(input: VerifyInput, result: VerifyResult): Promise<void>;
}

export class MongoVerificationsRepo implements VerificationsRepo {
  constructor(private readonly db: Db) {}
  async record(input: VerifyInput, result: VerifyResult): Promise<void> {
    await this.db.collection("verifications").insertOne({
      input,
      verdict: result.verdict,
      confidence: result.confidence,
      evidence: result.evidence,
      snapshot: { profile: result.person, company: result.company },
      uncertainty: result.uncertainty,
      scorer_version: result.scorer_version,
      request_id: result.request_id,
      created_at: new Date(),
    });
  }
}

/** Drop-in fake for tests. */
export class InMemoryVerificationsRepo implements VerificationsRepo {
  records: Array<{ input: VerifyInput; result: VerifyResult }> = [];
  async record(input: VerifyInput, result: VerifyResult): Promise<void> {
    this.records.push({ input, result });
  }
}
