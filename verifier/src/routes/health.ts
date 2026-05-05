import type { FastifyInstance } from "fastify";

export function registerHealthRoutes(app: FastifyInstance, info: { fetcherUrl: string; hasLlm: boolean }): void {
  app.get("/health", async () => ({
    ok: true,
    fetcher: info.fetcherUrl,
    llm_enabled: info.hasLlm,
  }));
}
