// Tiny fetch wrapper. The JWT lives in localStorage so a refresh keeps you
// logged in; every request attaches it as a Bearer token. A 401 clears the
// token and reloads, dropping the user back to the login screen.

import type { Keyword, Lead, LeadList, Source, User } from "../types";

const TOKEN_KEY = "alt_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api${path}`, { ...options, headers });

  if (res.status === 401 && token) {
    // Stale/expired token: drop it and bounce to login.
    clearToken();
    window.location.reload();
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data?.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<User>("/auth/me"),

  listLeads: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") q.set(k, String(v));
    }
    const qs = q.toString();
    return request<LeadList>(`/leads${qs ? `?${qs}` : ""}`);
  },
  updateLeadStatus: (id: number, status: string) =>
    request<Lead>(`/leads/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),

  listKeywords: () => request<Keyword[]>("/keywords"),
  createKeyword: (body: Record<string, unknown>) =>
    request<Keyword>("/keywords", { method: "POST", body: JSON.stringify(body) }),
  updateKeyword: (id: number, body: Record<string, unknown>) =>
    request<Keyword>(`/keywords/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteKeyword: (id: number) => request<void>(`/keywords/${id}`, { method: "DELETE" }),

  listSources: () => request<Source[]>("/sources"),
  createSource: (body: Record<string, unknown>) =>
    request<Source>("/sources", { method: "POST", body: JSON.stringify(body) }),
  updateSource: (id: number, body: Record<string, unknown>) =>
    request<Source>(`/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSource: (id: number) => request<void>(`/sources/${id}`, { method: "DELETE" }),
};