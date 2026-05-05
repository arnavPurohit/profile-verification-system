import { Worker } from "bullmq";
import IORedis from "ioredis";

import type { VerifyPipeline } from "../pipeline/verify.js";
import { VERIFY_QUEUE_NAME, type VerifyJobData, type VerifyJobResult } from "./verifyQueue.js";

const DEFAULT_CONCURRENCY = 5;

export function createVerifyWorker(
  redisUrl: string,
  pipeline: VerifyPipeline,
  concurrency: number = DEFAULT_CONCURRENCY,
): Worker<VerifyJobData, VerifyJobResult> {
  const connection = new IORedis(redisUrl, { maxRetriesPerRequest: null });
  return new Worker<VerifyJobData, VerifyJobResult>(
    VERIFY_QUEUE_NAME,
    async (job) => pipeline(job.data),
    { connection, concurrency },
  );
}
