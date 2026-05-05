/**
 * API rate limiting for Fastify using sliding window counters.
 * Uses Redis when available, falls back to in-memory per-process.
 */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";

interface RateLimitOpts {
  requestsPerMinute: number;
  redis?: { zadd: Function; zremrangebyscore: Function; zcard: Function; expire: Function } | null;
}

const SKIP_PATHS = new Set(["/health"]);

export function registerRateLimit(app: FastifyInstance, opts: RateLimitOpts): void {
  const { requestsPerMinute, redis } = opts;
  const localWindows = new Map<string, number[]>();

  app.addHook("onRequest", async (request: FastifyRequest, reply: FastifyReply) => {
    if (SKIP_PATHS.has(request.url)) return;

    const clientIp = request.ip;
    const key = `ratelimit:${clientIp}`;
    const now = Date.now() / 1000;
    const windowStart = now - 60;

    let allowed: boolean;

    if (redis) {
      try {
        await redis.zremrangebyscore(key, 0, windowStart);
        const count = await redis.zcard(key) as number;
        if (count >= requestsPerMinute) {
          allowed = false;
        } else {
          await redis.zadd(key, now, String(now));
          await redis.expire(key, 120);
          allowed = true;
        }
      } catch {
        allowed = checkLocal(localWindows, key, now, windowStart, requestsPerMinute);
      }
    } else {
      allowed = checkLocal(localWindows, key, now, windowStart, requestsPerMinute);
    }

    if (!allowed) {
      reply.code(429).header("Retry-After", "60").send({
        error: "rate_limit_exceeded",
        retry_after_seconds: 60,
      });
    }
  });
}

function checkLocal(
  windows: Map<string, number[]>,
  key: string,
  now: number,
  windowStart: number,
  limit: number,
): boolean {
  const existing = windows.get(key) ?? [];
  const filtered = existing.filter((t) => t > windowStart);
  if (filtered.length >= limit) {
    windows.set(key, filtered);
    return false;
  }
  filtered.push(now);
  windows.set(key, filtered);
  return true;
}
