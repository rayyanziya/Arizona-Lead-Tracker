import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { PLATFORMS } from "../types";
import type { Source } from "../types";

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "never";
}

export default function Sources() {
  const [items, setItems] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [platform, setPlatform] = useState("reddit");
  const [identifier, setIdentifier] = useState("");
  const [label, setLabel] = useState("");
  const [adding, setAdding] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setItems(await api.listSources());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load sources");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!identifier.trim()) return;
    setAdding(true);
    setError(null);
    try {
      const created = await api.createSource({
        platform,
        identifier: identifier.trim(),
        label: label.trim() || undefined,
        is_active: true,
      });
      setItems((prev) => [...prev, created]);
      setIdentifier("");
      setLabel("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add source");
    } finally {
      setAdding(false);
    }
  }

  async function toggle(s: Source) {
    try {
      const updated = await api.updateSource(s.id, { is_active: !s.is_active });
      setItems((prev) => prev.map((x) => (x.id === s.id ? updated : x)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update source");
    }
  }

  async function remove(s: Source) {
    if (!confirm(`Delete source "${s.label || s.identifier}"?`)) return;
    try {
      await api.deleteSource(s.id);
      setItems((prev) => prev.filter((x) => x.id !== s.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete source");
    }
  }

  const placeholder =
    platform === "reddit"
      ? "subreddit, e.g. r/Phoenix"
      : platform === "facebook"
        ? "group URL or id"
        : "search query or handle";

  return (
    <div>
      <div className="card">
        <form className="inline-form" onSubmit={add}>
          <div className="field">
            <label>Platform</label>
            <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ flex: 1, minWidth: 220 }}>
            <label>Identifier</label>
            <input
              value={identifier}
              placeholder={placeholder}
              onChange={(e) => setIdentifier(e.target.value)}
              style={{ width: "100%" }}
            />
          </div>
          <div className="field" style={{ minWidth: 160 }}>
            <label>Label (optional)</label>
            <input value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="field">
            <button className="primary" type="submit" disabled={adding}>
              {adding ? "Adding…" : "Add"}
            </button>
          </div>
        </form>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card" style={{ padding: 0 }}>
        {items.length === 0 && !loading ? (
          <div className="empty">No monitored sources yet. Add a subreddit or group above.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Platform</th>
                <th>Identifier</th>
                <th>Label</th>
                <th>Last scraped</th>
                <th>Active</th>
                <th className="right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <span className={`badge ${s.platform}`}>{s.platform}</span>
                  </td>
                  <td>{s.identifier}</td>
                  <td className="muted">{s.label || "—"}</td>
                  <td className="muted">{when(s.last_scraped_at)}</td>
                  <td>{s.is_active ? "Yes" : <span className="muted">No</span>}</td>
                  <td className="right">
                    <div className="row-actions" style={{ justifyContent: "flex-end" }}>
                      <button onClick={() => toggle(s)}>{s.is_active ? "Disable" : "Enable"}</button>
                      <button className="danger" onClick={() => remove(s)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}