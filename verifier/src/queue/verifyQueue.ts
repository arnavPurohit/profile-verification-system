import { Queue } from "bullmq";
import IORedis from "ioredis";

import type { VerifyInput, VerifyResult } from "../domain/index.js";

export const VERIFY_QUEUE_NAME = "verify-jobs";

export type VerifyJobData = VerifyInput;
export type VerifyJobResult = VerifyResult;

export function createVerifyQueue(redisUrl: string): Queue<VerifyJobData, VerifyJobResult> {
  const connection = new IORedis(redisUrl, { maxRetriesPerRequest: null });
  return new Queue<VerifyJobData, VerifyJobResult>(VERIFY_QUEUE_NAME, { connection });
}
