# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A job-search app: scrapes WeWorkRemotely, stores results in Supabase, orchestrates the scrape
through Trigger.dev, serves results via FastAPI, and displays them on a static page.

Live app: https://automindz-job-scraper.vercel.app

## Architecture

```
Website (web/index.html, served by FastAPI at GET /)
   -> FastAPI (api/main.py)          GET /v1/get-jobs?job_title=...
   -> Trigger.dev task (trigger/src/scrapeJobs.ts)
   -> Python scraper (scraper/weworkremotely.py)
   -> Supabase (jobs table)
   -> results flow back up through Trigger.dev -> FastAPI -> Website
```

FastAPI triggers the Trigger.dev task over its REST API and polls until the run completes, then
returns whatever the task stored. The Trigger.dev task (Node/TS) shells out to the Python scraper
as a subprocess, parses its JSON output, and upserts into Supabase, deduping on `job_url`. The
website is a single static HTML file served directly by FastAPI (not a separate deployment).

The Python scraper fetches WeWorkRemotely's Programming category RSS feed
(`categories/remote-programming-jobs.rss`) and filters items by whether the search term appears
in the title or description. It does **not** scrape the HTML search page — see "Gotchas" below
for why.

**`scraper/` exists in two places**: the canonical copy at the repo root, and a duplicate at
`trigger/scraper/`. The Trigger.dev build only bundles files inside `trigger/`, so the root
`scraper/` has to be manually copied into `trigger/scraper/` before every Trigger.dev deploy. If
you edit `scraper/weworkremotely.py`, the change does not take effect in production until this
copy step is re-run and the task is redeployed.

## Commands

**Run the scraper standalone:**
```bash
pip install -r requirements.txt
python -m scraper.weworkremotely "Python Developer"
# prints a JSON array of jobs to stdout
```

**Run the full app locally** (three terminals):
```bash
# 1. Trigger.dev task (keep running)
cd trigger && npm install && npx trigger.dev@latest dev

# 2. FastAPI (root .env populated)
uvicorn api.main:app --reload
# test: curl "http://localhost:8000/v1/get-jobs?job_title=Python%20Developer"

# 3. Website: http://localhost:8000/ (FastAPI serves web/index.html at GET /)
```

**Deploy FastAPI + website** (Vercel): push to the connected GitHub branch, or `vercel --prod`
from the repo root. Vercel auto-detects `api/main.py` as a Python serverless function and routes
all paths to it.

**Deploy the Trigger.dev task:**
```bash
# from repo root — sync the scraper copy first, every time
Copy-Item -Path .\scraper -Destination .\trigger\scraper -Recurse -Force   # PowerShell
# or: cp -r scraper trigger/scraper                                        # bash

cd trigger
npx trigger.dev@latest deploy
```

**Database**: no deploy step. Schema changes are run manually in the Supabase SQL editor against
`supabase/schema.sql`. (The Supabase CLI — `supabase db push`, `supabase migration new` — isn't
currently used in this project, but is the standard way to script schema changes if that changes.)

## Environment variables

Two separate `.env` files, because Trigger.dev's CLI auto-loads `.env` from the directory
containing `trigger.config.ts` (`trigger/`), not the repo root.

**Repo root `.env`** (read by `api/main.py`):
- `TRIGGER_SECRET_KEY` — from Trigger.dev dashboard → API Keys. `tr_dev_...` for local dev.
  **The deployed Vercel app must use the `tr_prod_...` Production key** — the dev key only works
  while a local `trigger dev` CLI session is running to pick up runs.
- `TRIGGER_TASK_ID` — defaults to `scrape-jobs`

**`trigger/.env`** (read by `scrapeJobs.ts`, create manually — not committed):
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — from Supabase dashboard → Project Settings → API

For the deployed app, `TRIGGER_SECRET_KEY`/`TRIGGER_TASK_ID` are Vercel environment variables
(scoped to Production), and `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are Trigger.dev dashboard
environment variables (Production environment) — neither `.env` file is deployed.

## Gotchas and engineering decisions

- **WeWorkRemotely blocks scraping from cloud/datacenter IPs.** Direct requests to
  `/remote-jobs/search` return `403 Forbidden` from Trigger.dev's AWS us-east-1 workers (confirmed
  via run logs), even though the same request works from a home IP. Its RSS feeds are not
  blocked — this is why the scraper reads RSS instead of the HTML search page. If a future feature
  needs data not in the RSS feed, expect this same blocking behavior from any direct HTML fetch.
- **Trigger.dev's runtime must be `node-22`.** Set in `trigger.config.ts`. `@supabase/supabase-js`'s
  realtime client requires native `WebSocket`, only available from Node 22+; Trigger.dev's default
  deployed runtime (21.7.3) fails at the task-indexing step without this.
- **Trigger.dev's build only bundles files inside `trigger/`.** The `additionalFiles` build
  extension (`./scraper/**` in `trigger.config.ts`) copies from inside `trigger/`, not from the
  repo root — hence the manual `scraper/` → `trigger/scraper/` copy before every deploy (see
  Commands above). Skipping this step reproduces `ModuleNotFoundError: No module named 'scraper'`.
- **The website is served by FastAPI, not a separate static Vercel deployment.** Vercel's Python
  zero-config setup routes every path to the detected function by default, so a standalone static
  deploy of `web/index.html` isn't reachable at the site root without a dedicated `GET /` route.
- **Job descriptions are truncated client-side only** (`summarize()` in `web/index.html`, ~250
  chars). The full description is always scraped, stored, and returned by the API unmodified.
- **RLS is intentionally disabled on `jobs`.** Only the server-side Trigger.dev task (using the
  `service_role` key) ever touches the table; nothing in the website or FastAPI exposes Supabase
  to the browser. `schema.sql` explicitly disables RLS and grants table-level access instead.
- **FastAPI triggers-and-polls Trigger.dev rather than using a webhook callback**, since a webhook
  would need a publicly reachable endpoint during local dev.
- **Some WWR titles carry an invisible leading emoji/variation-selector character.** `str.strip()`
  only removes whitespace, not symbol/mark characters, so a decorative glyph WWR sometimes
  prepends to a title can survive scraping and silently break alphabetical sorting (it sorts
  before any real letter, even though it renders as a near-invisible gap). Defended in two
  places: `scraper/weworkremotely.py`'s `_clean_title()` strips leading Unicode
  symbol/mark/format characters before the title/company ever leave the scraper, and
  `web/index.html`'s `sortKey()` strips any leading non-letter/non-digit character again before
  comparing, in case dirty data reaches the frontend some other way (e.g. old rows already in
  Supabase).

## Working across Vercel / Trigger.dev / Supabase from here

All three have CLIs that can be run directly instead of using their dashboards:
- **Vercel**: `vercel` (link/deploy), `vercel env` (manage env vars), `vercel --prod` (production deploy)
- **Trigger.dev**: `npx trigger.dev@latest dev` (local), `npx trigger.dev@latest deploy` (production)
- **Supabase**: `supabase` CLI (not yet used in this project — would need `supabase link` first)

GitHub is the trigger for auto-deploys on both Vercel and Trigger.dev's connected integrations —
a push to the connected branch is enough to redeploy Vercel; Trigger.dev still requires an
explicit `npx trigger.dev@latest deploy` (it does not auto-deploy on push in this project's setup).
