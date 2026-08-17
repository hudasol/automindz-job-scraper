"""
FastAPI entrypoint.

GET /v1/get-jobs?job_title=Python%20Developer

Flow (per the take-home's architecture diagram):
  Website -> FastAPI -> Trigger.dev -> Python scraper -> Supabase -> back to FastAPI -> Website

This endpoint triggers the `scrape-jobs` Trigger.dev task via Trigger.dev's REST API,
polls until the run completes, and returns the jobs the task reports it stored.

Env vars required (see .env.example):
  TRIGGER_SECRET_KEY  - Trigger.dev secret API key (starts with tr_dev_ / tr_prod_...)
  TRIGGER_TASK_ID      - defaults to "scrape-jobs"

Verified against Trigger.dev's live docs on 2026-08-10 (docs.trigger.dev's OpenAPI spec):
  - Trigger:  POST https://api.trigger.dev/api/v1/tasks/{taskIdentifier}/trigger
              -> { "id": "run_xxx" }
  - Retrieve: GET  https://api.trigger.dev/api/v3/runs/{runId}   <- note: v3, not v1
              -> { "id", "status", "output", ... }
  - status enum: PENDING_VERSION, DELAYED, QUEUED, EXECUTING, REATTEMPTING, FROZEN,
                 COMPLETED, CANCELED, FAILED, CRASHED, INTERRUPTED, SYSTEM_FAILURE
Source: https://trigger.dev/docs/management/tasks/trigger and
https://trigger.dev/docs/management/runs/retrieve
"""

import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool

from dotenv import load_dotenv
load_dotenv()

TRIGGER_TASK_URL = "https://api.trigger.dev/api/v1/tasks/{task_id}/trigger"
TRIGGER_RUN_URL = "https://api.trigger.dev/api/v3/runs/{run_id}"  # v3, not v1 - confirmed in docs

TRIGGER_SECRET_KEY = os.environ.get("TRIGGER_SECRET_KEY", "")
TRIGGER_TASK_ID = os.environ.get("TRIGGER_TASK_ID", "scrape-jobs")

# Gates the /mcp endpoint only (see require_mcp_auth below) - the REST endpoints below stay
# open, matching their existing behavior. An MCP tool surface is easier for an unrelated
# Claude Code session to stumble into and trigger real spend (Trigger.dev runs, Context.dev
# credits) than a REST URL nobody has a reason to guess, so this closes that gap.
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")

TERMINAL_FAILURE_STATUSES = {"CANCELED", "FAILED", "CRASHED", "INTERRUPTED", "SYSTEM_FAILURE"}

POLL_INTERVAL_SECONDS = 1
POLL_TIMEOUT_SECONDS = 60

# MCP server, mounted onto this same FastAPI app at /mcp (see the bottom of this file).
# Tools are defined further down, right after the REST endpoints they wrap - see
# search_jobs/get_company_info.
mcp = MCPServer("automindz-job-scraper")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mounting the MCP server disables FastAPI's own default lifespan handling, so this
    # app-level lifespan has to be the one that enters mcp.session_manager.run() - without
    # it, the first request to /mcp fails with "Task group is not initialized".
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Job Search Scraper API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_mcp_auth(request: Request, call_next):
    if request.url.path.startswith("/mcp"):
        if not MCP_API_KEY:
            return JSONResponse({"error": "MCP_API_KEY is not configured"}, status_code=500)
        if request.headers.get("authorization") != f"Bearer {MCP_API_KEY}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok"}


def trigger_and_poll(task_id: str, payload: dict) -> dict:
    """Triggers a Trigger.dev task via its REST API and polls until it completes.

    Shared by /v1/get-jobs (scrape-jobs task) and /v1/enrich-company (enrich-company task) -
    both need the same trigger-then-poll dance, just against different task ids/payloads.
    """
    if not TRIGGER_SECRET_KEY:
        raise HTTPException(500, "TRIGGER_SECRET_KEY is not configured")

    headers = {"Authorization": f"Bearer {TRIGGER_SECRET_KEY}"}

    trigger_resp = httpx.post(
        TRIGGER_TASK_URL.format(task_id=task_id),
        headers=headers,
        json={"payload": payload},
        timeout=30,
    )
    if trigger_resp.status_code >= 400:
        raise HTTPException(502, f"Failed to trigger {task_id} task: {trigger_resp.text}")

    run_id = trigger_resp.json().get("id")
    if not run_id:
        raise HTTPException(502, "Trigger.dev did not return a run id")

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        run_resp = httpx.get(TRIGGER_RUN_URL.format(run_id=run_id), headers=headers, timeout=30)
        run_resp.raise_for_status()
        run_data = run_resp.json()
        status = run_data.get("status")

        if status == "COMPLETED":
            return run_data.get("output") or {}

        if status in TERMINAL_FAILURE_STATUSES:
            raise HTTPException(502, f"{task_id} task ended with status: {status}")

        time.sleep(POLL_INTERVAL_SECONDS)

    raise HTTPException(504, f"Timed out waiting for {task_id} task to complete")


@app.get("/v1/get-jobs")
def get_jobs(job_title: str = Query(..., min_length=1, description="Job title to search for")):
    output = trigger_and_poll(TRIGGER_TASK_ID, {"jobTitle": job_title})
    return {"job_title": job_title, "jobs": output.get("jobs", [])}


@app.get("/v1/enrich-company")
def enrich_company(company_name: str = Query(..., min_length=1, description="Company name to enrich")):
    output = trigger_and_poll("enrich-company", {"companyName": company_name})
    return {
        "company_name": company_name,
        "company_size": output.get("company_size"),
        "funding_stage": output.get("funding_stage"),
    }


# MCP tools - thin wrappers around the REST endpoints above, run off the FastAPI thread
# pool (run_in_threadpool) since get_jobs/enrich_company make blocking httpx calls and this
# is an async context. No separate backend logic: these return exactly what the REST
# endpoints return, just callable directly by an MCP client instead of over HTTP.

@mcp.tool()
async def search_jobs(job_title: str) -> dict:
    """Search WeWorkRemotely's Programming job feed for a title or keyword.

    Scrapes live and can take up to a minute. Results are also stored in this app's
    Supabase `jobs` table as a side effect.

    Args:
        job_title: Job title or keyword to search for, e.g. "Python Developer".
    """
    return await run_in_threadpool(get_jobs, job_title)


@mcp.tool()
async def get_company_info(company_name: str) -> dict:
    """Look up a company's employee-count range and latest funding stage via Context.dev.

    COSTS MONEY: up to 20 Context.dev credits for a company that has never been looked up
    before (0 credits if it's already cached). Confirm with the user before calling this for
    a company you haven't already resolved in this conversation. Repeat lookups for the same
    company are free.

    Args:
        company_name: The company's name, e.g. "Acme Inc".
    """
    return await run_in_threadpool(enrich_company, company_name)


from fastapi.responses import FileResponse

FRONTEND_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "index.html")

@app.get("/")
def serve_frontend():
    return FileResponse(FRONTEND_PATH)


# Mounted last and deliberately: Starlette matches routes in registration order, and a
# root Mount("/") matches any path, including "/" itself. Mounting it before the frontend
# route above would swallow "/" before that route is ever reached (confirmed - that's
# exactly what broke the live site: GET / returned the MCP sub-app's own 404 instead of
# index.html). Registering all other routes first and this mount last means those exact
# routes always match before Starlette falls through to the mount.
#
# transport_security: the SDK auto-enables DNS-rebinding host-header protection allowing
# only 127.0.0.1/localhost/::1 whenever `host` isn't overridden (see
# mcp.server.lowlevel.server.Server.streamable_http_app) - meant for unauthenticated local
# dev servers. That rejected every real request in production with "Invalid Host header"
# (confirmed - Vercel's Host header is the deployment's actual domain, never localhost).
# require_mcp_auth above is the real security boundary here (a bearer token a DNS-rebinding
# attack can't forge), and Vercel's preview URLs are per-deployment random hashes that
# can't be enumerated into a static allowed_hosts list anyway, so this protection is both
# redundant and unworkable for this deployment - disabled explicitly rather than guessed at.
app.mount(
    "/",
    mcp.streamable_http_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)),
)
