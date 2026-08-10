# Job Search Scraper

A small job-search app built for the Automindz Solutions take-home. Scrapes
[WeWorkRemotely](https://weworkremotely.com), stores results in Supabase, orchestrates the
scrape through Trigger.dev, serves results via FastAPI, and displays them on a static page.

## Architecture

```
Website (web/index.html)
   |
   v
FastAPI (api/main.py)  ->  GET /v1/get-jobs?job_title=...
   |
   v
Trigger.dev task (trigger/src/scrapeJobs.ts)
   |
   v
Python scraper (scraper/weworkremotely.py)
   |
   v
Supabase (jobs table)  ->  results flow back up through Trigger.dev -> FastAPI -> Website
```

FastAPI triggers the Trigger.dev task over its REST API and polls until the run completes,
then returns whatever jobs the task reports it stored. The Trigger.dev task (Node/TS, since
Trigger.dev is TypeScript-first) shells out to the Python scraper as a subprocess, parses its
JSON output, and upserts it into Supabase - deduping on `job_url` so re-searching a title
doesn't create duplicate rows.

## Before you do anything else

Run the connectivity check locally as a final confirmation:

```bash
pip install -r requirements.txt
python3 scripts/test_connection.py
```

I verified the scraper's approach against the live site directly (fetched a real search page
and a real job detail page on 2026-08-10) - the `?term=` search filters server-side, sponsored
listings route through `/listing_ads/...` so they're naturally excluded, and the title/company
extraction matched real markup. `scripts/test_connection.py` re-confirms this with plain
`requests` from your machine, which is what actually matters since that's the library the
scraper itself uses.

## Project structure

```
scraper/weworkremotely.py   Python scraper - the only file that should need touching if
                             WWR's markup changes. Usable as a library or CLI.
api/main.py                 FastAPI app - GET /v1/get-jobs, triggers + polls Trigger.dev
trigger/src/scrapeJobs.ts   Trigger.dev task - runs the Python scraper, writes to Supabase
supabase/schema.sql         The one table (`jobs`)
web/index.html               Static single-page UI, no build step
scripts/test_connection.py  Run first - sanity-checks WWR is reachable
CLAUDE.md                    Orientation notes for Claude Code
```

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Used by | Where to get it |
|---|---|---|
| `TRIGGER_SECRET_KEY` | `api/main.py` | Trigger.dev dashboard -> API keys |
| `TRIGGER_TASK_ID` | `api/main.py` | Defaults to `scrape-jobs`, matches the task `id` in `scrapeJobs.ts` |
| `SUPABASE_URL` | `trigger/src/scrapeJobs.ts` | Supabase dashboard -> Project Settings -> API |
| `SUPABASE_SERVICE_ROLE_KEY` | `trigger/src/scrapeJobs.ts` | Same page - use the service role key (not the anon key), since the Trigger.dev task writes to the DB server-side |

## Running it locally

**1. Database.** In the Supabase dashboard, open the SQL editor and run `supabase/schema.sql`.

**2. Python scraper (standalone sanity check):**
```bash
pip install -r requirements.txt
python3 -m scraper.weworkremotely "Python Developer"
# should print a JSON array of jobs to stdout
```

**3. Trigger.dev task:**
```bash
cd trigger
npm install
npx trigger login
# fill in trigger.config.ts's `project` field from the Trigger.dev dashboard first
npm run dev
```
This starts the Trigger.dev dev server, which will pick up `scrapeJobs.ts` and let it run
locally when triggered. It shells out to `python3 -m scraper.weworkremotely`, so it needs to
run from a working directory where that resolves (repo root) and needs Python + the scraper's
dependencies available on PATH.

**4. FastAPI:**
```bash
# from the repo root, with .env populated
uvicorn api.main:app --reload
```
Test it directly: `curl "http://localhost:8000/v1/get-jobs?job_title=Python%20Developer"`

**5. Website:**
Just open `web/index.html` in a browser (or serve it with `python3 -m http.server` from the
`web/` folder). It calls `http://localhost:8000` by default - update the `API_BASE` constant
in `index.html` once the API is deployed.

## Deployment

- **FastAPI -> Vercel.** Vercel can run a FastAPI app as a Python serverless function; add a
  `vercel.json` pointing at `api/main.py` (or use Vercel's Python runtime detection), and set
  `TRIGGER_SECRET_KEY` / `TRIGGER_TASK_ID` as environment variables in the Vercel project
  settings - never commit them.
- **Trigger.dev task -> Trigger.dev cloud.** From `trigger/`, run `npx trigger deploy`. Set
  `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` as environment variables in the Trigger.dev
  dashboard for the deployed environment. Trigger.dev's managed runners only officially
  support Node, so `trigger.config.ts` uses the `aptGet` build extension to install
  `python3`, `python3-requests`, and `python3-bs4` into the deployed image - without that,
  the subprocess call to the Python scraper would fail in production even though it works
  locally where Python's already installed.
- **Website -> Vercel** (static) or served directly from the FastAPI app as a static file -
  either works for something this small; just make sure `API_BASE` in `index.html` points at
  the deployed FastAPI URL.
- **Supabase** needs no separate deployment - it's already hosted; just make sure
  `supabase/schema.sql` has been run against the production project.

## What's verified vs. what still needs a local check

Everything below was confirmed against live sources on 2026-08-10, not just written from
memory:

- WeWorkRemotely's search endpoint, link structure, and job detail page markup - fetched
  both directly (see "Before you do anything else" above).
- Trigger.dev's REST API shape - fetched the actual OpenAPI spec. Two things I'd originally
  guessed wrong and fixed: the run-retrieve endpoint is `/api/v3/runs/{runId}` (not
  `/api/v1/`), and the status enum has more values than "COMPLETED/FAILED" (see
  `TERMINAL_FAILURE_STATUSES` in `api/main.py`).
- Trigger.dev's current SDK import path - `"@trigger.dev/sdk"`, not the older `/v3` subpath
  I initially used in `scrapeJobs.ts` and `trigger.config.ts`.

Still worth a final sanity pass once you're running locally with a real Supabase/Trigger.dev
project: run `scripts/test_connection.py`, and do one end-to-end local run before deploying
(the "Running it locally" steps above) so you catch anything environment-specific before the
live round-2 session.

## Notes on engineering decisions

- Deduping is handled with a `UNIQUE` constraint on `job_url` in Supabase plus an `upsert`
  with `ignoreDuplicates: true` in the Trigger.dev task, rather than checking-then-inserting -
  simpler and avoids a race condition if the same search runs twice concurrently.
- The scraper caps detail-page fetches at 25 per search (`MAX_JOBS`) and adds a short delay
  between requests - keeps it simple and reasonably polite to WWR's servers rather than
  fetching everything at once.
- FastAPI triggers-and-polls rather than using a webhook callback, since a webhook would need
  a publicly reachable endpoint during local dev - polling is simpler for something this size
  and the task should complete well within the request timeout.
