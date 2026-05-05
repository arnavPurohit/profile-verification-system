import type { FastifyInstance } from "fastify";
import type { Queue } from "bullmq";
import { z } from "zod";

import type { VerifyPipeline } from "../pipeline/verify.js";
import type { VerifyJobData, VerifyJobResult } from "../queue/index.js";

const verifyBodySchema = z.object({
  url: z.string().min(1),
  company: z.string().min(1),
  max_age_days: z.number().int().positive().optional(),
});

const batchBodySchema = z.object({
  items: z.array(verifyBodySchema).min(1).max(1000),
});

export function registerVerifyRoutes(
  app: FastifyInstance,
  verify: VerifyPipeline,
  queue: Queue<VerifyJobData, VerifyJobResult> | null = null,
): void {
  app.post("/verify", async (req, reply) => {
    const parsed = verifyBodySchema.safeParse(req.body);
    if (!parsed.success) {
      reply.code(400);
      return { error: "invalid_body", details: parsed.error.issues };
    }
    try {
      const result = await verify(parsed.data);
      return result;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      req.log.error({ err: msg }, "verify.failed");
      reply.code(502);
      return { error: "verify_failed", message: msg };
    }
  });

  app.post("/verify/async", async (req, reply) => {
    if (!queue) {
      reply.code(503);
      return { error: "queue_unavailable", message: "Async verification is not configured" };
    }
    const parsed = verifyBodySchema.safeParse(req.body);
    if (!parsed.success) {
      reply.code(400);
      return { error: "invalid_body", details: parsed.error.issues };
    }
    const job = await queue.add("verify", parsed.data);
    return { job_id: job.id!, status: "queued" as const };
  });

  app.get<{ Params: { jobId: string } }>("/verify/job/:jobId", async (req, reply) => {
    if (!queue) {
      reply.code(503);
      return { error: "queue_unavailable", message: "Async verification is not configured" };
    }
    const job = await queue.getJob(req.params.jobId);
    if (!job) {
      reply.code(404);
      return { error: "not_found", message: "Job not found" };
    }
    const state = await job.getState();
    const response: Record<string, unknown> = { status: state };
    if (state === "completed") {
      response.result = job.returnvalue;
    }
    if (state === "failed") {
      response.error = job.failedReason ?? "unknown error";
    }
    return response;
  });

  app.post("/verify/batch", async (req, reply) => {
    if (!queue) {
      reply.code(503);
      return { error: "queue_unavailable", message: "Async verification is not configured" };
    }
    const parsed = batchBodySchema.safeParse(req.body);
    if (!parsed.success) {
      reply.code(400);
      return { error: "invalid_body", details: parsed.error.issues };
    }
    const jobs = await queue.addBulk(
      parsed.data.items.map((item) => ({ name: "verify", data: item })),
    );
    return { job_ids: jobs.map((j) => j.id!) };
  });
}
