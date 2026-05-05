/**
 * Company-name normalization. Pure function. No IO.
 *
 * Strips legal suffixes and punctuation, lowercases, applies the alias map.
 * The alias map is intentionally a small seed set, not a global DB — anything
 * beyond ~50 entries belongs in a real entity-resolution system.
 */
export interface NormalizedCompany {
  raw: string;
  normalized: string;
  alias_target: string | null;
  was_aliased: boolean;
}

const SUFFIXES = [
  ", inc.",
  " inc.",
  ", inc",
  " inc",
  ", llc",
  " llc",
  ", ltd",
  " ltd",
  " gmbh",
  " s.a.",
  " sa",
  " plc",
  " pvt.",
  " pvt",
  " private limited",
  " corp.",
  " corp",
  " co.",
  " corporation",
  " company",
  " technologies",
];

// Seed alias map. (Lowercased keys.)
const ALIASES: Record<string, string> = {
  facebook: "meta",
  "fb inc": "meta",
  "meta platforms": "meta",
  alphabet: "google",
  "google llc": "google",
  twitter: "x",
  "x corp": "x",
  jpmc: "jpmorgan chase",
  "jp morgan": "jpmorgan chase",
  "jp morgan chase": "jpmorgan chase",
  "deepmind": "google",
  "youtube": "google",
};

export function normalizeCompany(input: string): NormalizedCompany {
  if (!input || typeof input !== "string") {
    throw new Error(`invalid company: ${input}`);
  }
  const raw = input.trim();
  let s = raw.toLowerCase();

  for (const suffix of SUFFIXES) {
    if (s.endsWith(suffix)) {
      s = s.slice(0, -suffix.length);
    }
  }

  // Strip punctuation, collapse whitespace.
  s = s.replace(/[^a-z0-9 &]+/g, " ").replace(/\s+/g, " ").trim();

  const aliasTarget = ALIASES[s] ?? null;
  return {
    raw,
    normalized: aliasTarget ?? s,
    alias_target: aliasTarget,
    was_aliased: aliasTarget !== null,
  };
}

/** Token-set similarity: order-insensitive Jaccard on word tokens. Pure. */
export function tokenSetRatio(a: string, b: string): number {
  const ta = new Set(a.toLowerCase().split(/\s+/).filter(Boolean));
  const tb = new Set(b.toLowerCase().split(/\s+/).filter(Boolean));
  if (ta.size === 0 || tb.size === 0) return 0;
  let intersect = 0;
  for (const t of ta) if (tb.has(t)) intersect++;
  const union = ta.size + tb.size - intersect;
  return intersect / union;
}
