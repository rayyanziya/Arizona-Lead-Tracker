import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { PLATFORMS, STATUSES } from "../types";
import type { Lead, MatchStatus } from "../types";

const PAGE_SIZE = 50;

function scoreClass(score: number | null): string {
  if (score == null) return "lo";
  if (score >= 8) return "hi";
  if (score >= 5) return "mid";
  return "lo";
}

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString();
}

export default function Leads() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [platform, setPlatform] = useState("");
  const [minScore, setMinScore] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listLeads({
        status: status || undefined,
        platform: platform || undefined,
        min_score: minScore || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setLeads(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load leads");
    } finally {
      setLoading(false);
    }
  }, [status, platform, minScore, offset]);

  useEffect(() => {
    load();
  }, [load]);

  // Changing a filter resets pagination to the first page.
  function onFilter(setter: (v: string) => void, v: string) {
    setter(v);
    setOffset(0);
  }

  async function setLeadStatus(lead: Lead, next: MatchStatus) {
    try {
      const updated = await api.updateLeadStatus(lead.id, next);
      setLeads((prev) => prev.map((l) => (l.id === lead.id ? updated : l)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update lead");
    }
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      <div className="toolbar">
        <div className="field">
          <label>Status</label>
          <select value={status} onChange={(e) => onFilter(setStatus, e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Platform</label>
          <select value={platform} onChange={(e) => onFilter(setPlatform, e.target.value)}>
            <option value="">All</option>
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Min score</label>
          <select value={minScore} onChange={(e) => onFilter(setMinScore, e.target.value)}>
            <option value="">Any</option>
            {[3, 5, 7, 8, 9].map((n) => (
              <option key={n} value={n}>
                {n}+
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <button onClick={load} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        <div className="field muted" style={{ marginLeft: "auto" }}>
          {total} lead{total === 1 ? "" : "s"}
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card" style={{ padding: 0 }}>
        {leads.length === 0 && !loading ? (
          <div className="empty">No leads match these filters yet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Score</th>
                <th>Platform</th>
                <th>Lead</th>
                <th>Matched</th>
                <th>Status</th>
                <th className="right">Triage</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <tr key={lead.id}>
                  <td>
                    <span className={`score ${scoreClass(lead.ai_score)}`}>
                      {lead.ai_score ?? "—"}
                    </span>
                    {lead.ai_is_buyer ? <div className="muted">buyer</div> : null}
                  </td>
                  <td>
                    <span className={`badge ${lead.post.platform}`}>{lead.post.platform}</span>
                  </td>
                  <td className="lead-body">
                    {lead.post.title ? <div><strong>{lead.post.title}</strong></div> : null}
                    <div>{lead.post.body.slice(0, 240)}{lead.post.body.length > 240 ? "…" : ""}</div>
                    {lead.ai_reason ? <div className="lead-reason">{lead.ai_reason}</div> : null}
                    <div className="muted" style={{ marginTop: 4 }}>
                      {lead.post.author ? `${lead.post.author} · ` : ""}
                      {when(lead.post.posted_at)} ·{" "}
                      <a href={lead.post.url} target="_blank" rel="noreferrer">
                        open
                      </a>
                    </div>
                  </td>
                  <td>{lead.matched_term ?? (lead.matched_terms ?? []).join(", ")}</td>
                  <td>
                    <span className={`badge ${lead.status}`}>{lead.status}</span>
                  </td>
                  <td className="right">
                    <div className="row-actions" style={{ justifyContent: "flex-end" }}>
                      <button onClick={() => setLeadStatus(lead, "responded")}>Responded</button>
                      <button onClick={() => setLeadStatus(lead, "ignored")}>Ignore</button>
                      {lead.status !== "pending" && (
                        <button onClick={() => setLeadStatus(lead, "pending")}>Reset</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="pager">
        <button disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
          ‹ Prev
        </button>
        <span>
          Page {page} of {pages}
        </span>
        <button disabled={offset + PAGE_SIZE >= total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>
          Next ›
        </button>
      </div>
    </div>
  );
}