# Arizona Lead Tracker

This app watches social media for you and finds people who want to buy custom
software (things like ERP, HRIS, CRM, POS, and business apps). It checks Facebook
Groups, Reddit, and X (Twitter), reads each post, decides how likely the person is
a real buyer, and shows you the best leads in a simple dashboard. It can also ping
you on Telegram and email the moment a hot lead shows up, so you can be the first
to reply.

> Note on the name: the project is called "Arizona" but it is built for the
> Indonesian market (Indonesian Facebook groups, Jakarta timezone). The name is
> just a label.

> Please read before using: Facebook and X do not allow automated scraping in
> their rules, so accounts or IP addresses can get banned. The posts you collect
> contain people's personal data, which is protected by law (Indonesia's PDP Law
> and GDPR). Use a separate account, go slow, and get legal advice before using
> this commercially. Reddit is read through its official, allowed API.

## What you get

- A web dashboard to browse leads, filter them by score, and mark them as handled.
- A "Scrape Facebook" button so you can pull fresh posts on demand instead of
  waiting for the automatic schedule.
- Automatic scoring that runs for free (no paid AI account needed).
- Optional Telegram and email alerts for high-scoring leads.

## What you need before you start

- A computer with **Docker Desktop** installed. That is the only thing you have to
  install yourself. Docker handles everything else (database, background workers,
  the website) inside its own containers.
- A Facebook account you are willing to use for collecting posts (a spare account
  is safest).

That is it. You do not need to install Python, Postgres, or Node yourself.

## How to set it up (first time)

Open a terminal in the project folder and run these one at a time:

```bash
cp .env.example .env     # makes your private settings file
make up                  # builds and starts everything
make migrate             # sets up the database
make seed                # creates your login and a sample group to watch
make capture-fb          # opens a browser so you can log into Facebook once
```

A few notes in plain terms:

- `.env` is your private settings file. It holds passwords and keys and is never
  shared or uploaded. Open it and fill in the few blanks (the file explains each
  one). For free scoring you can leave the AI key empty.
- `make capture-fb` opens a real browser window. Log into Facebook by hand (do any
  2-step verification as normal). The app saves your login in a safe, encrypted
  form so the background worker can browse as you. You only do this once per
  computer.

When it finishes, open the dashboard at **http://localhost:8080**.

If you also want to see test emails, the built-in email viewer is at
**http://localhost:8025**.

## How to use it day to day

1. Open **http://localhost:8080** and log in.
2. Go to **Sources** and add the Facebook groups you want to watch.
3. Go to **Leads** and click **Scrape Facebook** to pull in posts right away, or
   just wait. The app checks your groups automatically every 20 minutes.
4. New leads appear in the table with a score. Click **Refresh** after a minute or
   two to see them. Use the filters to focus on the best ones, and mark each lead
   as Responded or Ignore as you work through them.

## Moving it to another computer

A copy of the code by itself is not enough to run the app, on purpose, because the
sensitive parts are kept out of the shared code for safety. On a new computer you
need to:

1. Install Docker Desktop.
2. Get the code (`git clone` or `git pull`).
3. Run the same five setup steps above (`cp .env.example .env`, `make up`,
   `make migrate`, `make seed`, `make capture-fb`).

Three things never travel with the code and must be redone on each computer: your
private `.env` settings, your Facebook login (you log in again with
`make capture-fb`), and the database (rebuilt by `make migrate` and `make seed`).
This is by design. Those parts contain secrets and personal data and should never
be shared.

## Handy commands

```bash
make up        # start everything (also rebuilds after code changes)
make down      # stop everything
make logs      # watch what the app is doing
make seed      # recreate the sample login and group
make test      # run the automated checks
```

## How it works (for the curious)

A scheduler tells background workers to visit your sources. The workers collect
posts, remove duplicates, check them against your keywords, score how likely each
poster is a buyer, save the good ones, and send alerts. A web service (FastAPI)
serves the data to the React dashboard.

```
        Scheduler ──▶ Redis (queue + memory of seen posts)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
  Reddit worker     Facebook/X worker   Alert worker
        │                 │                 ▲
        └──── new posts ───┴──▶ shared pipeline ──┘
                       remove duplicates →
                       match keywords →
                       score the buyer intent →
                       save → alert
                          │
                    PostgreSQL database
                          │
                    FastAPI  ◀──▶  React dashboard
```

The collectors are kept simple. All the important and tricky logic lives in one
well-tested pipeline.

## Built with

Python, FastAPI, Celery with Redis, PostgreSQL, Playwright, React, and Docker
Compose.

## Project status

| Part | What it does | Status |
|------|--------------|--------|
| Foundation | Project structure and Docker setup | Done |
| Database | Multi-tenant database, migrations, seed data | Done |
| Core pipeline | Duplicate removal, keyword matching, scoring, alerts | Done |
| Facebook | Collects posts from Facebook groups | Done |
| Reddit | Collects posts from Reddit | Done |
| X (Twitter) | Collects posts from X | Done |
| Dashboard | Web dashboard and admin (login, leads, sources, keywords) | Done |
| Production hardening | Extra polish for a public, paid launch | In progress |

Scoring works for free out of the box using a built-in offline scorer, so you do
not need a paid AI account to try the app.
