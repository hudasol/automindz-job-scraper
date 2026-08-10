# Job Search Scraper

A small job-search app built for the Automindz Solutions take-home. Scrapes
[WeWorkRemotely](https://weworkremotely.com), stores results in Supabase, orchestrates the
scrape through Trigger.dev, serves results via FastAPI, and displays them on a static page.

## How it works

```
Website (web/index.html)
   -> FastAPI (api/main.py)          GET /v1/get-jobs?job_title=...
   -> Trigger.dev task (trigger/src/scrapeJobs.ts)
   -> Python scraper (scraper/weworkremotely.py)
   -> Supabase (jobs table)
   -> results flow back up through Trigger.dev -> FastAPI -> Website
```

FastAPI triggers the Trigger.dev task over its REST API and polls until the run completes,
then returns whatever jobs the task stored. The Trigger.dev task (Node/TS) shells out to the
Python scraper as a subprocess, parses its JSON output, and upserts it into Supabase, deduping
on `job_url` so re-searching a title doesn't create duplicates.

## Project structure

```
scraper/weworkremotely.py   Python scraper - accepts a search query, returns jobs as JSON
api/main.py                 FastAPI app - GET /v1/get-jobs, triggers + polls Trigger.dev
trigger/src/scrapeJobs.ts   Trigger.dev task - runs the scraper, writes to Supabase
supabase/schema.sql         The one table (`jobs`)
web/index.html               Static single-page UI, no build step
scripts/test_connection.py  Standalone check that WWR is reachable
CLAUDE.md                    Orientation notes for Claude Code
```

## Environment variables

Two separate `.env` files - Trigger.dev's CLI auto-loads `.env` from the directory containing
`trigger.config.ts` (`trigger/`), not the repo root, so the variables have to live in the
matching location.

**Repo root `.env`** (copy from `.env.example`) - read by `api/main.py`:

| Variable | Where to get it |
|---|---|
| `TRIGGER_SECRET_KEY` | Trigger.dev dashboard -> API Keys (`tr_dev_...` for local dev) |
| `TRIGGER_TASK_ID` | Defaults to `scrape-jobs` |

**`trigger/.env`** (create manually) - read by `scrapeJobs.ts`:

| Variable | Where to get it |
|---|---|
| `SUPABASE_URL` | Supabase dashboard -> Project Settings -> API |
| `SUPABASE_SERVICE_ROLE_KEY` | Same page - the legacy `service_role` JWT key, under "Legacy anon, service_role API keys" |

Never commit either file.

## Running the scraper standalone

```bash
pip install -r requirements.txt
python -m scraper.weworkremotely "Python Developer"
# prints a JSON array of jobs to stdout
```

## Running the full app locally

**1. Database** - in the Supabase SQL editor, run `supabase/schema.sql`.

**2. Trigger.dev task** (keep this terminal running):
```bash
cd trigger
npm install
# fill in trigger.config.ts's `project` field from the Trigger.dev dashboard
# create trigger/.env with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
npx trigger.dev@latest dev
```

**3. FastAPI** (new terminal, root `.env` populated):
```bash
uvicorn api.main:app --reload
```
Test: `curl "http://localhost:8000/v1/get-jobs?job_title=Python%20Developer"`

**4. Website** - open `web/index.html` in a browser. Calls `http://localhost:8000` by
default; update the `API_BASE` constant once the API is deployed.

## Deployment

- **FastAPI -> Vercel**: deploy as a Python serverless function pointing at `api/main.py`;
  set `TRIGGER_SECRET_KEY` / `TRIGGER_TASK_ID` as Vercel environment variables.
- **Trigger.dev task -> Trigger.dev cloud**: `npx trigger deploy` from `trigger/`; set
  `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` in the Trigger.dev dashboard. Trigger.dev's
  runners are Node-only by default, so `trigger.config.ts` uses the `aptGet` build extension
  to install `python3`, `python3-requests`, and `python3-bs4` into the deployed image.
- **Website -> Vercel** (static), with `API_BASE` pointed at the deployed FastAPI URL.
- **Supabase** needs no separate deployment - just make sure `schema.sql` has been run.

## Notes on engineering decisions

- Deduping uses a `UNIQUE` constraint on `job_url` plus an `upsert` with
  `ignoreDuplicates: true`, rather than check-then-insert - simpler, and avoids a race
  condition if the same search runs twice concurrently.
- The scraper caps detail-page fetches at 25 per search and adds a short delay between
  requests - reasonably polite to WWR's servers without adding real complexity.
- FastAPI triggers-and-polls rather than using a webhook callback, since a webhook would
  need a publicly reachable endpoint during local dev.
- `service_role` bypasses Supabase's row-level security policies but not table-level
  grants, so `schema.sql` explicitly disables RLS and grants access on the `jobs` table.
- The Trigger.dev task resolves its working directory to the repo root explicitly, since
  the task itself runs from inside `trigger/` but the Python scraper package lives one
  level up.
