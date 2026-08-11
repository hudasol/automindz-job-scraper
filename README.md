# Job Search Scraper

A small job-search app built for the Automindz Solutions take-home. Scrapes
[WeWorkRemotely](https://weworkremotely.com), stores results in Supabase, orchestrates the
scrape through Trigger.dev, serves results via FastAPI, and displays them on a static page.

**Live app:** https://automindz-job-scraper.vercel.app

## How it works

```
Website (web/index.html, served by FastAPI at GET /)
   -> FastAPI (api/main.py)          GET /v1/get-jobs?job_title=...
   -> Trigger.dev task (trigger/src/scrapeJobs.ts)
   -> Python scraper (scraper/weworkremotely.py)
   -> Supabase (jobs table)
   -> results flow back up through Trigger.dev -> FastAPI -> Website
```

FastAPI triggers the Trigger.dev task over its REST API and polls until the run completes,
then returns whatever jobs the task stored. The Trigger.dev task (Node/TS) shells out to the
Python scraper as a subprocess, parses its JSON output, and upserts it into Supabase, deduping
on `job_url` so re-searching a title doesn't create duplicates. The website is a single static
HTML file served directly by the FastAPI app (see "Notes on engineering decisions" for why).

The Python scraper fetches WeWorkRemotely's Programming category RSS feed and filters items by
whether the search term appears in the job title or description - it does not scrape the HTML
search page. See "Notes on engineering decisions" for why.

## Project structure

```
scraper/weworkremotely.py   Python scraper - fetches WWR's RSS feed, filters by query, returns JSON
trigger/scraper/            Copy of scraper/, needed so Trigger.dev's deploy can bundle it (see below)
api/main.py                 FastAPI app - GET /v1/get-jobs, GET / (serves the website), triggers + polls Trigger.dev
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

**Repo root `.env`** - read by `api/main.py`:

| Variable | Where to get it |
|---|---|
| `TRIGGER_SECRET_KEY` | Trigger.dev dashboard -> API Keys. Use the `tr_dev_...` key for local dev. **For the deployed Vercel app, this must be the Production key (`tr_prod_...`) instead** - the dev key only works while your local `trigger dev` CLI is running to pick up runs. |
| `TRIGGER_TASK_ID` | Defaults to `scrape-jobs` |

**`trigger/.env`** (create manually) - read by `scrapeJobs.ts`:

| Variable | Where to get it |
|---|---|
| `SUPABASE_URL` | Supabase dashboard -> Project Settings -> API |
| `SUPABASE_SERVICE_ROLE_KEY` | Same page - the legacy `service_role` JWT key, under "Legacy anon, service_role API keys" |

Never commit either file. For the deployed app, `TRIGGER_SECRET_KEY`/`TRIGGER_TASK_ID` are set
as Vercel environment variables, and `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are set as
Trigger.dev environment variables (Production environment) in their dashboard - not in either
`.env` file, since those aren't deployed.

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

**4. Website** - open `web/index.html` directly in a browser, or visit
`http://localhost:8000/` once FastAPI is running (it serves the same file). Calls
`https://automindz-job-scraper.vercel.app` by default per the `API_BASE` constant in
`web/index.html` - change that to `http://localhost:8000` for local-only testing.

## Deployment

- **FastAPI + Website -> Vercel**: one deployment. Vercel auto-detects `api/main.py` as a
  Python serverless function and routes all paths to it (including `/`, which now returns
  `web/index.html` via a dedicated route). Set `TRIGGER_SECRET_KEY` (the **production** key)
  and `TRIGGER_TASK_ID` as Vercel environment variables, scoped to Production.
- **Trigger.dev task -> Trigger.dev cloud**:
  1. Copy the scraper into the Trigger.dev project so its build can find it (Trigger.dev only
     bundles files inside `trigger/`, not sibling folders):
     ```bash
     # from repo root
     Copy-Item -Path .\scraper -Destination .\trigger\scraper -Recurse -Force   # PowerShell
     # or: cp -r scraper trigger/scraper                                        # bash
     ```
     Re-run this any time `scraper/weworkremotely.py` changes, before redeploying.
  2. Set `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` in the Trigger.dev dashboard's
     Production environment variables.
  3. From `trigger/`: `npx trigger.dev@latest deploy`.
  Trigger.dev's runners are Node-only by default, so `trigger.config.ts` uses the `aptGet`
  build extension to install `python3`, `python3-requests`, and `python3-bs4` into the
  deployed image, and sets `runtime: "node-22"` (see engineering notes below for why).
- **Supabase** needs no separate deployment - just make sure `schema.sql` has been run.

## Notes on engineering decisions

- **WeWorkRemotely blocks scraping from cloud IPs.** The original scraper hit WWR's HTML
  `/remote-jobs/search` page directly. That works fine from a home IP but WWR's firewall
  returns `403 Forbidden` to the same request from Trigger.dev's deployed workers (AWS
  us-east-1 datacenter range) - confirmed via Trigger.dev's run logs. WWR's RSS feeds are not
  blocked, so the scraper now fetches the Programming category RSS feed
  (`categories/remote-programming-jobs.rss`) and filters entries by whether the search term
  appears in the title or description, instead of scraping the search page and 25 individual
  job detail pages. This is also simpler and faster - one request instead of up to 26.
- **Trigger.dev's Node runtime had to be bumped to `node-22`.** `@supabase/supabase-js`'s
  realtime client requires native `WebSocket` support, which Node only has from v22 onward.
  Trigger.dev's default deployed runtime is Node 21.7.3, which caused every deploy to fail at
  the task-indexing step with `Node.js detected but native WebSocket not found`. Fixed by
  setting `runtime: "node-22"` in `trigger.config.ts`.
- **`scraper/` is duplicated into `trigger/scraper/` before each deploy.** Trigger.dev's build
  only bundles files that live inside the Trigger project directory (`trigger/`); the Python
  scraper lives one level up so the rest of the app (FastAPI, local CLI usage) can use it
  directly. The `additionalFiles` build extension is configured to copy `./scraper/**`, but
  that glob only sees files already inside `trigger/`, so the copy has to happen first. Without
  it, the deployed task fails with `ModuleNotFoundError: No module named 'scraper'`.
- **The website is served by FastAPI, not a separate static Vercel deployment.** Vercel's
  Python zero-config setup routes every path in the project to the detected function by
  default, so a plain static `web/index.html` deploy wasn't actually reachable at the site
  root. Simplest fix was a `GET /` route in `api/main.py` that returns the file directly,
  rather than fighting Vercel's routing config for a second static deployment.
- Job descriptions are truncated to ~250 characters in the website's JS (`summarize()` in
  `web/index.html`) purely for display. The full description is still scraped, stored in
  Supabase, and returned by the API unmodified.
- Deduping uses a `UNIQUE` constraint on `job_url` plus an `upsert` with
  `ignoreDuplicates: true`, rather than check-then-insert - simpler, and avoids a race
  condition if the same search runs twice concurrently.
- FastAPI triggers-and-polls rather than using a webhook callback, since a webhook would
  need a publicly reachable endpoint during local dev.
- `service_role` bypasses Supabase's row-level security policies but not table-level
  grants, so `schema.sql` explicitly disables RLS and grants access on the `jobs` table.
  Only the Trigger.dev task (server-side, using `service_role`) ever touches this table
  directly - nothing in the website or FastAPI exposes Supabase to the browser.
