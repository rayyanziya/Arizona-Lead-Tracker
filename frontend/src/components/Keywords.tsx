import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { LANGUAGES, MATCH_TYPES } from "../types";
import type { Keyword } from "../types";

export default function Keywords() {
  const [items, setItems] = useState<Keyword[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [term, setTerm] = useState("");
  const [language, setLanguage] = useState("any");
  const [matchType, setMatchType] = useState("phrase");
  const [adding, setAdding] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setItems(await api.listKeywords());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load keywords");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!term.trim()) return;
    setAdding(true);
    setError(null);
    try {
      const created = await api.createKeyword({
        term: term.trim(),
        language,
        match_type: matchType,
        is_active: true,
      });
      setItems((prev) => [...prev, created]);
      setTerm("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add keyword");
    } finally {
      setAdding(false);
    }
  }

  async function toggle(k: Keyword) {
    try {
      const updated = await api.updateKeyword(k.id, { is_active: !k.is_active });
      setItems((prev) => prev.map((x) => (x.id === k.id ? updated : x)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update keyword");
    }
  }

  async function remove(k: Keyword) {
    if (!confirm(`Delete keyword "${k.term}"?`)) return;
    try {
      await api.deleteKeyword(k.id);
      setItems((prev) => prev.filter((x) => x.id !== k.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete keyword");
    }
  }

  return (
    <div>
      <div className="card">
        <form className="inline-form" onSubmit={add}>
          <div className="field" style={{ flex: 1, minWidth: 220 }}>
            <label>New keyword</label>
            <input
              value={term}
              placeholder="e.g. looking for a realtor"
              onChange={(e) => setTerm(e.target.value)}
              style={{ width: "100%" }}
            />
          </div>
          <div className="field">
            <label>Language</label>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Match</label>
            <select value={matchType} onChange={(e) => setMatchType(e.target.value)}>
              {MATCH_TYPES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
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
          <div className="empty">No keywords yet. Add one above to start matching posts.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Term</th>
                <th>Language</th>
                <th>Match</th>
                <th>Active</th>
                <th className="right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((k) => (
                <tr key={k.id}>
                  <td>{k.term}</td>
                  <td className="muted">{k.language}</td>
                  <td className="muted">{k.match_type}</td>
                  <td>{k.is_active ? "Yes" : <span className="muted">No</span>}</td>
                  <td className="right">
                    <div className="row-actions" style={{ justifyContent: "flex-end" }}>
                      <button onClick={() => toggle(k)}>{k.is_active ? "Disable" : "Enable"}</button>
                      <button className="danger" onClick={() => remove(k)}>
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