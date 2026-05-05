export interface Config {
  port: number;
  mongoUrl: string;
  mongoDb: string;
  redisUrl: string;
  fetcherUrl: string;
  anthropicApiKey: string;
  anthropicModel: string;
  profileFreshDays: number;
  companyFreshDays: number;
  rateLimitRpm: number;
  workerConcurrency: number;
}

export function loadConfig(): Config {
  return {
    port: Number(process.env.VERIFIER_PORT ?? 8000),
    mongoUrl: process.env.MONGO_URL ?? "mongodb://localhost:27017",
    mongoDb: process.env.MONGO_DB ?? "verification",
    redisUrl: process.env.REDIS_URL ?? "redis://localhost:6379/0",
    fetcherUrl: process.env.FETCHER_URL ?? "http://localhost:8001",
    anthropicApiKey: process.env.ANTHROPIC_API_KEY ?? "",
    anthropicModel: process.env.ANTHROPIC_MODEL ?? "claude-haiku-4-5-20251001",
    profileFreshDays: Number(process.env.PROFILE_FRESH_DAYS ?? 14),
    companyFreshDays: Number(process.env.COMPANY_FRESH_DAYS ?? 60),
    rateLimitRpm: Number(process.env.RATE_LIMIT_RPM ?? 120),
    workerConcurrency: Number(process.env.WORKER_CONCURRENCY ?? 5),
  };
}
