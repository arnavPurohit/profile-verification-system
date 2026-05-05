export type EmploymentType =
  | "full_time"
  | "part_time"
  | "contractor"
  | "advisor"
  | "intern"
  | "freelance"
  | "self_employed"
  | "unknown";

export interface Experience {
  company_urn: string | null;
  company_name: string;
  title: string;
  start: string | null;
  end: string | null;
  is_current: boolean;
  employment_type: EmploymentType;
  location?: string | null;
  description?: string | null;
}

export interface Profile {
  urn: string;
  public_id: string | null;
  url: string | null;
  name: string;
  headline: string | null;
  location: string | null;
  experience: Experience[];
  education: unknown[];
  profile_last_updated: string | null;
  fetched_at: string;
  source: "voyager" | "extension" | "fixture" | "login";
  parser_version: string;
}
