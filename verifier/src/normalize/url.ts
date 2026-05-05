/**
 * URL normalization. Pure function. No IO.
 *
 * Handles: lnkd.in shortlinks, country subdomains, /pub/, /sales/, mobile,
 * trailing slashes, query params, missing protocol, public-id vs vanity slug,
 * capitalization.
 */
export interface NormalizedUrl {
  canonical: string;
  slug: string;
  host: string;
  was_normalized: boolean;
}

const COUNTRY_SUBDOMAINS = new Set([
  "uk",
  "in",
  "ca",
  "au",
  "de",
  "fr",
  "es",
  "it",
  "nl",
  "br",
  "mx",
  "jp",
  "kr",
  "sg",
  "ae",
  "m",
  "www",
]);

export function normalizeUrl(input: string): NormalizedUrl {
  if (!input || typeof input !== "string") {
    throw new Error(`invalid url: ${input}`);
  }
  const original = input.trim();
  let s = original;

  // Add protocol if missing.
  if (!/^https?:\/\//i.test(s)) {
    s = "https://" + s;
  }

  let url: URL;
  try {
    url = new URL(s);
  } catch {
    throw new Error(`unparseable url: ${original}`);
  }

  // lnkd.in is a shortlink — we can't dereference it offline, so we keep it
  // as the slug and let the caller resolve it (or reject upstream).
  if (url.hostname === "lnkd.in") {
    const slug = url.pathname.replace(/^\/+|\/+$/g, "");
    return {
      canonical: `https://lnkd.in/${slug}`,
      slug,
      host: "lnkd.in",
      was_normalized: original !== `https://lnkd.in/${slug}`,
    };
  }

  // Strip country and mobile subdomains.
  const hostParts = url.hostname.toLowerCase().split(".");
  if (hostParts.length > 2 && COUNTRY_SUBDOMAINS.has(hostParts[0]!)) {
    hostParts.shift();
  }
  const host = hostParts.join(".");

  // Pull out the slug. LinkedIn profile URLs come in two main shapes:
  //   /in/{slug}   — vanity / public id
  //   /pub/{name}/{a}/{b}/{c}  — older public profile
  //   /sales/people/{slug}  — sales nav (gated, but we accept the slug)
  let slug: string | null = null;
  const path = url.pathname.replace(/\/+$/, "");
  const inMatch = path.match(/^\/in\/([^/]+)/i);
  const pubMatch = path.match(/^\/pub\/([^/]+)/i);
  const salesMatch = path.match(/^\/sales\/(?:people|lead)\/([^/]+)/i);
  if (inMatch) slug = inMatch[1]!;
  else if (pubMatch) slug = pubMatch[1]!;
  else if (salesMatch) slug = salesMatch[1]!;

  if (!slug) {
    throw new Error(`url does not contain a profile slug: ${original}`);
  }

  slug = decodeURIComponent(slug).toLowerCase();
  const canonical = `https://www.linkedin.com/in/${slug}`;
  return {
    canonical,
    slug,
    host: "www.linkedin.com",
    was_normalized: original !== canonical,
  };
}
