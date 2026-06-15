import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { ConfigStatus, TestScoreResult } from "../types";

// A canned buyer-intent post so the operator can one-click a real scoring call
// without thinking up sample text.
const SAMPLE_POST =
  "Lagi cari developer buat bikin aplikasi POS + inventory untuk toko kami di Surabaya. " +
  "Ada rekomendasi vendor yang bisa custom? Budget bisa nego.";

// Surfaces what the backend is actually wired to do, so "I added a source but
// see no leads" has a visible explanation. Two hard gates decide whether leads
// can ever appear: something must collect (Reddit creds or a Facebook session)
// AND scoring must be on (an Anthropic key). Everything else is a soft note.

function Chip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="badge" style={{ borderColor: ok ? "var(--good)" : "var(--bad)", color: ok ? "var(--good)" : "var(--bad)" }}>
      {ok ? "✓" : "✗"} {label}
    </span>
  );
}

export default function ConfigBanner() {
  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestScoreResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  useEffect(() => {
    api.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  async function runScoringTest() {
    setTesting(true);
    setResult(null);
    setTestError(null);
    try {
      setResult(await api.testScore({ body: SAMPLE_POST }));
    } catch (e) {
      setTestError(e instanceof ApiError ? e.message : "Scoring test failed.");
    } finally {
      setTesting(false);
    }
  }

  if (!status) return null;

  const canCollect = status.reddit_configured || status.facebook_session_present;
  const blocked: string[] = [];
  if (!status.scoring_configured) {
    blocked.push("No Anthropic API key — scraped posts can't be scored, so no leads will appear. Set ANTHROPIC_API_KEY in .env and restart.");
  }
  if (!canCollect) {
    blocked.push("No collector configured — add Reddit API credentials (reliable) or capture a Facebook session to start collecting posts.");
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ marginRight: 4 }}>System status</strong>
        <Chip ok={status.scoring_configured} label="Scoring (Anthropic)" />
        <Chip ok={status.reddit_configured} label="Reddit" />
        <Chip ok={status.facebook_session_present} label="Facebook session" />
        <Chip ok={status.telegram_configured} label="Telegram" />
        <Chip ok={status.email_configured} label="Email" />
      </div>
      {blocked.length > 0 && (
        <div style={{ marginTop: 10 }}>
          {blocked.map((msg) => (
            <div key={msg} className="error" style={{ margin: "4px 0" }}>
              {msg}
            </div>
          ))}
        </div>
      )}
      {blocked.length === 0 && (
        <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
          Ready to collect and score. New posts appear here within ~20 minutes of the next scrape.
        </div>
      )}

      {/* Prove the AI scoring actually works right now — no scrape, no collector
          needed. Scores one sample post and shows Claude's verdict. */}
      <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button onClick={runScoringTest} disabled={testing || !status.scoring_configured}>
            {testing ? "Scoring…" : "Test scoring"}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            {status.scoring_configured
              ? "Sends a sample buyer post to Claude and shows the verdict."
              : "Needs an Anthropic key before it can run."}
          </span>
        </div>
        {testError && (
          <div className="error" style={{ marginTop: 8 }}>
            {testError}
          </div>
        )}
        {result && (
          <div style={{ marginTop: 8, fontSize: 13 }}>
            <span
              className="badge"
              style={{
                borderColor: result.is_buyer ? "var(--good)" : "var(--bad)",
                color: result.is_buyer ? "var(--good)" : "var(--bad)",
              }}
            >
              {result.is_buyer ? "Buyer" : "Not a buyer"} · {result.confidence}/10
            </span>
            <div className="muted" style={{ marginTop: 6 }}>
              {result.reason}
            </div>
            <div className="muted" style={{ marginTop: 4, fontSize: 11 }}>
              scored by {result.model}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}