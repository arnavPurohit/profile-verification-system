/**
 * Composition root. The only file that constructs concrete classes.
 * Everything else depends on interfaces and pure functions.
 */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const dotenv = require("dotenv");
dotenv.config({ path: resolve(fileURLToPath(import.meta.url), "../../../.env") });

import Fastify from "fastify";

import { loadConfig } from "./config.js";
import { HttpFetcherClient } from "./fetcher/client.js";
import { AnthropicVerifier, NoopVerifier } from "./llm/verifier.js";
import { buildVerifyPipeline } from "./pipeline/verify.js";
import { createVerifyQueue, createVerifyWorker } from "./queue/index.js";
import { registerHealthRoutes } from "./routes/health.js";
import { registerVerifyRoutes } from "./routes/verify.js";
import { connectMongo } from "./storage/mongo.js";
import { MongoVerificationsRepo } from "./storage/verifications.js";

async function bootstrap() {
  const config = loadConfig();
  const app = Fastify({ logger: { level: process.env.LOG_LEVEL ?? "info" } });

  const db = await connectMongo(config.mongoUrl, config.mongoDb);
  const repo = new MongoVerificationsRepo(db);
  const fetcher = new HttpFetcherClient(config.fetcherUrl);
  const llm = config.anthropicApiKey
    ? new AnthropicVerifier(config.anthropicApiKey, config.anthropicModel)
    : new NoopVerifier();

  const verify = buildVerifyPipeline({
    fetcher,
    llm,
    repo,
    now: () => new Date(),
  });

  let queue: ReturnType<typeof createVerifyQueue> | null = null;
  try {
    queue = createVerifyQueue(config.redisUrl);
    const worker = createVerifyWorker(config.redisUrl, verify);
    app.addHook("onClose", async () => {
      await worker.close();
      await queue!.close();
    });
    app.log.info("BullMQ queue and worker started");
  } catch (err) {
    app.log.warn({ err }, "Redis unavailable – async/batch endpoints disabled");
  }

  registerHealthRoutes(app, { fetcherUrl: config.fetcherUrl, hasLlm: !!config.anthropicApiKey });
  registerVerifyRoutes(app, verify, queue);

  await app.listen({ host: "0.0.0.0", port: config.port });
}

bootstrap().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("bootstrap failed", err);
  process.exit(1);
});
