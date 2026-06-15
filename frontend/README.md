# Arizona Lead Tracker — Dashboard (frontend)

React + TypeScript + Vite single-page app for the lead dashboard. It talks to
the FastAPI backend through a `/api` prefix (proxied to `:8000` in dev, and to
the `api` container by nginx in production).

## Run it locally (dev)

Requires Node 20+.

```bash
cd frontend
npm install
npm run dev
```

Then open http://localhost:5173. The dev server proxies `/api` to
`http://localhost:8000` (override with `VITE_API_TARGET`), so start the backend
first (`docker compose up api postgres redis` from the repo root, plus
`python -m scripts.seed` to create a demo tenant/user to log in with).

## Run it with Docker (whole stack)

From the repo root:

```bash
docker compose up --build
```

The dashboard is served at http://localhost:8080 (the `frontend` service).

## What's here

- `src/lib/api.ts` — fetch wrapper; stores the JWT in localStorage, attaches it
  as a Bearer token, and bounces to login on 401.
- `src/components/Login.tsx` — email/password sign-in.
- `src/components/Leads.tsx` — filter (status/platform/score), triage status,
  paginate.
- `src/components/Keywords.tsx` / `Sources.tsx` — admin CRUD.