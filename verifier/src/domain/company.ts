export interface Company {
  urn: string;
  name: string;
  normalized: string;
  aliases: string[];
  website: string | null;
  industry: string | null;
  size_band: string | null;
  hq: string | null;
  parent_urn: string | null;
  fetched_at: string;
  source: "voyager" | "extension" | "fixture" | "profile" | "login";
  parser_version: string;
}
