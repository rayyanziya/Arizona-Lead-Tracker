# Arizona Lead Tracker

Multi-tenant social-media **keyword monitoring for lead generation**. It watches
Facebook Groups, Reddit, and X (Twitter) for posts where someone is *looking to
buy* custom software services (ERP, HRIS, CRM, POS, business apps), scores intent
with Claude, and notifies you instantly via Telegram + email so you can be first
to respond.

> ⚠️ **Compliance note.** Facebook and X prohibit automated scraping in their ToS;
> accounts/IPs can be banned. Stored posts contain personal data subject to
> Indonesia's PDP Law / GDPR. Use dedicated accounts, conservative pacing, and
> obtain legal review before commercial launch. Reddit uses the official API.

## Architecture

```
        Celery Beat ──schedules──▶ Redis (broker + dedup cache)
                                        │
        ┌───────────────┬───────────────┼───────────────┐
        ▼               ▼               ▼               ▼
  Reddit Worker   Browser Worker   (browser q)    Notify Worker
   (PRAW API)     (Playwright FB/X)               (Telegram+SMTP)
        │               │                               ▲
        └──── raw posts ─┴───────▶ process_post ────────┘
                          dedup → keyword match →
                          Claude score → persist match → notify
                                        │
                            PostgreSQL (multi-tenant)
                                        │
                         FastAPI (REST+JWT) ◀──▶ React/Tailwind
```

The collectors are thin; all expensive/fragile logic (dedup, matching, scoring,
notifying) lives in one tested pipeline. See `docs/` and the plan for detail.

## Stack

Python 3.12 · FastAPI · Celery + Redis · PostgreSQL · SQLAlchemy 2 + Alembic ·
Playwright · Anthropic (Claude) · React + Tailwind (Phase 6) · Docker Compose.

## Repo layout

```
backend/
  app/
    core/        config, db engines, logging, security/crypto
    models/      SQLAlchemy ORM (multi-tenant schema)
    schemas/     Pydantic DTOs incl. RawPost normalization
    services/    dedup, keyword_matcher, scoring (Claude)
    notifiers/   telegram, email
    monitors/    facebook, reddit, x collectors
    tasks/       celery app, dispatch, process_post pipeline
    api/         routers + deps
  scripts/       seed.py, capture_fb_session.py
  tests/         unit + integration (pytest)
  alembic/       migrations
frontend/        React dashboard (Phase 6)
```

## Quickstart

```bash
cp .env.example .env            # fill in secrets (Anthropic key, Telegram token…)
make up                         # build + start postgres, redis, api, workers, beat
make migrate                    # apply DB schema
make seed                       # create the MVP tenant + sample keywords/source
make test                       # run the test suite
```

API: http://localhost:8000/docs · Mailpit UI: http://localhost:8025

### Capture a Facebook session (one-time, assisted)

```bash
make capture-fb                 # opens a browser; log in manually (handles 2FA),
                                # saves an ENCRYPTED storage_state for the worker
```

## Delivery status

| Phase | Scope | Status |
|------|-------|--------|
| 0 | Project structure + Docker Compose | ✅ |
| 1 | PostgreSQL multi-tenant schema + migrations + seed | ✅ |
| 2 | Shared pipeline primitives (dedup, matcher, scoring, notifiers) — **TDD** | ✅ |
| 3 | Facebook monitor MVP → Telegram (end-to-end) | ⏳ |
| 4 | Reddit monitor (PRAW) | ⏳ |
| 5 | X (Twitter) monitor | ⏳ |
| 6 | Dashboard + admin (FastAPI API + React) | ⏳ |
| 7 | Productionization / SaaS-readiness | ⏳ |

## Testing

TDD throughout. Pure pipeline logic (matcher, dedup, scoring, notify formatting)
is unit-tested without external services; DB/Redis/Playwright paths are covered by
integration tests behind markers. Target ≥ 80% coverage.

```bash
make cov
```
