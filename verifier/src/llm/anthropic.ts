import Anthropic from "@anthropic-ai/sdk";

/**
 * LLM is used for ONE thing only: company disambiguation when rules tie.
 * Narrow interface so the resolver doesn't depend on the SDK directly.
 */
export interface LlmDisambiguator {
  disambiguate(input: {
    query: string;
    candidates: Array<{ urn: string; name: string; website?: string | null; industry?: string | null }>;
  }): Promise<{ chosen_urn: string | null; reason: string }>;
}

/** Null implementation: returns ambiguous. Used when no API key configured. */
export class NoopDisambiguator implements LlmDisambiguator {
  async disambiguate(input: {
    query: string;
    candidates: Array<{ urn: string; name: string; website?: string | null; industry?: string | null }>;
  }) {
    return {
      chosen_urn: input.candidates[0]?.urn ?? null,
      reason: "LLM disabled; selected first candidate",
    };
  }
}

export class AnthropicDisambiguator implements LlmDisambiguator {
  private readonly client: Anthropic;
  constructor(apiKey: string, private readonly model: string) {
    this.client = new Anthropic({ apiKey });
  }

  async disambiguate(input: {
    query: string;
    candidates: Array<{ urn: string; name: string; website?: string | null; industry?: string | null }>;
  }): Promise<{ chosen_urn: string | null; reason: string }> {
    if (input.candidates.length === 0) {
      return { chosen_urn: null, reason: "no candidates" };
    }
    if (input.candidates.length === 1) {
      return { chosen_urn: input.candidates[0]!.urn, reason: "only one candidate" };
    }
    const list = input.candidates
      .map(
        (c, i) =>
          `${i + 1}. ${c.name} (urn=${c.urn}; website=${c.website ?? "?"}; industry=${c.industry ?? "?"})`,
      )
      .join("\n");
    const prompt = `A user typed the company name "${input.query}". Pick the candidate they most likely meant from the list below. If you cannot pick with reasonable confidence, respond with "NONE".

Candidates:
${list}

Respond with one line in the format:
URN: <urn or NONE>
REASON: <one short sentence>`;
    const resp = await this.client.messages.create({
      model: this.model,
      max_tokens: 200,
      messages: [{ role: "user", content: prompt }],
    });
    const text = resp.content
      .filter((c): c is Anthropic.TextBlock => c.type === "text")
      .map((c) => c.text)
      .join("\n");
    const urnMatch = text.match(/URN:\s*(\S+)/i);
    const reasonMatch = text.match(/REASON:\s*(.+)/i);
    const urn = urnMatch?.[1] ?? "NONE";
    const reason = reasonMatch?.[1]?.trim() ?? "no reason given";
    if (urn === "NONE" || !input.candidates.some((c) => c.urn === urn)) {
      return { chosen_urn: null, reason };
    }
    return { chosen_urn: urn, reason };
  }
}
