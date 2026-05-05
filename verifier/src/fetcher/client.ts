import { request } from "undici";
import type { Company, Profile } from "../domain/index.js";

export interface FetcherClient {
  fetchProfile(urnOrUrl: string, maxAgeDays?: number): Promise<{ profile: Profile; source: string }>;
  fetchCompany(urn: string): Promise<{ company: Company; source: string }>;
  searchCompanies(query: string, limit?: number): Promise<Company[]>;
}

export class HttpFetcherClient implements FetcherClient {
  constructor(private readonly baseUrl: string) {}

  async fetchProfile(urnOrUrl: string, maxAgeDays?: number) {
    const payload: Record<string, unknown> = { urn_or_url: urnOrUrl };
    if (maxAgeDays !== undefined) payload.max_age_days = maxAgeDays;
    const body = await this.post<{ profile: Profile; source: string }>(
      "/fetch/profile",
      payload,
    );
    return body;
  }

  async fetchCompany(urn: string) {
    return this.post<{ company: Company; source: string }>("/fetch/company", { urn });
  }

  async searchCompanies(query: string, limit = 5): Promise<Company[]> {
    const body = await this.post<{ results: Company[] }>("/search/company", { query, limit });
    return body.results;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const url = this.baseUrl.replace(/\/+$/, "") + path;
    const res = await request(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await res.body.text();
    if (res.statusCode >= 400) {
      throw new FetcherError(res.statusCode, text);
    }
    return JSON.parse(text) as T;
  }
}

export class FetcherError extends Error {
  constructor(public readonly status: number, body: string) {
    super(`fetcher returned ${status}: ${body}`);
  }
}
