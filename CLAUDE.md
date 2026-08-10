# Project guide for Claude Code

## What this is
A small job-search app: scrape WeWorkRemotely, store results in Supabase, orchestrate the
scrape through Trigger.dev, serve via FastAPI, display on a static page.

## Structure
- `scraper/weworkremotely.py` - all scraping logic. Pure Python, callable as a library
  (`scrape_jobs(query)`) or CLI (`python3 -m scraper.weworkremotely "<query>"`, prints JSON
  to stdout). If selectors break, this is the only file that needs touching.
- `api/main.py` - FastAPI app. Single real endpoint: `GET /v1/get-jobs?job_title=`.
  Triggers the Trigger.dev task via REST and polls for the result.
- `trigger/src/scrapeJobs.ts` - Trigger.dev task (`scrape-jobs`). Shells out to the Python
  scraper, upserts results into Supabase (dedup on `job_url`).
- `supabase/schema.sql` - the one table (`jobs`).
- `web/index.html` - static single-page UI, no build step, calls the FastAPI endpoint
  directly via `fetch`.
- `scripts/test_connection.py` - standalone script to sanity-check that
  weworkremotely.com is reachable and inspect raw HTML before touching the scraper.

## Conventions
- Keep it simple - this is a take-home, not a production system. Don't add frameworks,
  ORMs, or extra abstraction layers beyond what's here unless asked.
- The scraper file is the single source of truth for WWR's HTML structure. If selectors
  need to change, that's the only file that should change.
- Env vars live in `.env` (see `.env.example`); never hardcode secrets or commit `.env`.
- The Trigger.dev task assumes it runs from the repo root so `python3 -m scraper.weworkremotely`
  resolves - keep that working directory assumption in mind if the deploy config changes.

## Verified vs. still worth checking
- WWR's scraping approach and Trigger.dev's REST API shape were both confirmed against live
  sources on 2026-08-10 (see README's "What's verified" section for specifics). Two bugs that
  fix caught: the run-retrieve endpoint is `/api/v3/runs/{runId}` not `/api/v1/`, and the SDK
  import is `"@trigger.dev/sdk"` not the older `/v3` subpath.
- Still run `scripts/test_connection.py` once locally as a final check with real `requests`
  output, and do one full local end-to-end run before deploying or the live round-2 session.
- `trigger.config.ts` installs `python3`/`python3-requests`/`python3-bs4` via the `aptGet`
  build extension so the subprocess call to the Python scraper works in Trigger.dev's
  deployed (Node-only by default) environment, not just locally.
