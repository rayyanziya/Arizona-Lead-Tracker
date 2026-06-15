// Mirrors the FastAPI response schemas (app/schemas/*.py). Kept in one place so
// the table columns and form options stay in sync with the backend enums.

export type Platform = "facebook" | "reddit" | "x";
export type MatchStatus = "pending" | "notified" | "responded" | "ignored";
export type Language = "id" | "en" | "any";
export type MatchType = "exact" | "phrase" | "regex";

export const PLATFORMS: Platform[] = ["facebook", "reddit", "x"];
export const STATUSES: MatchStatus[] = ["pending", "notified", "responded", "ignored"];
export const LANGUAGES: Language[] = ["any", "en", "id"];
export const MATCH_TYPES: MatchType[] = ["phrase", "exact", "regex"];

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  role: string;
  tenant_id: number;
}

export interface LeadPost {
  platform: string;
  external_id: string;
  url: string;
  author: string | null;
  title: string | null;
  body: string;
  posted_at: string | null;
}

export interface Lead {
  id: number;
  status: MatchStatus;
  ai_score: number | null;
  ai_is_buyer: boolean | null;
  ai_reason: string | null;
  matched_term: string | null;
  matched_terms: string[] | null;
  created_at: string;
  post: LeadPost;
}

export interface LeadList {
  items: Lead[];
  total: number;
  limit: number;
  offset: number;
}

export interface Keyword {
  id: number;
  term: string;
  language: string;
  match_type: string;
  is_active: boolean;
  created_at: string;
}

export interface Source {
  id: number;
  platform: string;
  identifier: string;
  label: string | null;
  is_active: boolean;
  last_scraped_at: string | null;
  created_at: string;
}