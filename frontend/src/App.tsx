import { useEffect, useState } from "react";
import { api, clearToken, getToken } from "./lib/api";
import type { User } from "./types";
import Login from "./components/Login";
import ConfigBanner from "./components/ConfigBanner";
import Leads from "./components/Leads";
import Keywords from "./components/Keywords";
import Sources from "./components/Sources";

type Tab = "leads" | "keywords" | "sources";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("leads");

  // On load (and after a fresh login sets the token) resolve the current user.
  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  function onLoggedIn(u: User) {
    setUser(u);
  }
  function logout() {
    clearToken();
    setUser(null);
  }

  if (loading) return <div className="empty">Loading…</div>;
  if (!user) return <Login onLoggedIn={onLoggedIn} />;

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">Arizona Lead Tracker</div>
        <div className="tabs">
          <button className={tab === "leads" ? "active" : ""} onClick={() => setTab("leads")}>
            Leads
          </button>
          <button className={tab === "keywords" ? "active" : ""} onClick={() => setTab("keywords")}>
            Keywords
          </button>
          <button className={tab === "sources" ? "active" : ""} onClick={() => setTab("sources")}>
            Sources
          </button>
        </div>
        <div className="who">
          <span>
            {user.email} · {user.role}
          </span>
          <button onClick={logout}>Log out</button>
        </div>
      </div>
      <div className="content">
        <ConfigBanner />
        {tab === "leads" && <Leads />}
        {tab === "keywords" && <Keywords />}
        {tab === "sources" && <Sources />}
      </div>
    </div>
  );
}