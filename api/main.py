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

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

TRIGGER_TASK_URL = "https://api.trigger.dev/api/v1/tasks/{task_id}/trigger"
TRIGGER_RUN_URL = "https://api.trigger.dev/api/v3/runs/{run_id}"  # v3, not v1 - confirmed in docs

TRIGGER_SECRET_KEY = os.environ.get("TRIGGER_SECRET_KEY", "")
TRIGGER_TASK_ID = os.environ.get("TRIGGER_TASK_ID", "scrape-jobs")

TERMINAL_FAILURE_STATUSES = {"CANCELED", "FAILED", "CRASHED", "INTERRUPTED", "SYSTEM_FAILURE"}

POLL_INTERVAL_SECONDS = 1
POLL_TIMEOUT_SECONDS = 60

app = FastAPI(title="Job Search Scraper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/get-jobs")
def get_jobs(job_title: str = Query(..., min_length=1, description="Job title to search for")):
    if not TRIGGER_SECRET_KEY:
        raise HTTPException(500, "TRIGGER_SECRET_KEY is not configured")

    headers = {"Authorization": f"Bearer {TRIGGER_SECRET_KEY}"}

    trigger_resp = httpx.post(
        TRIGGER_TASK_URL.format(task_id=TRIGGER_TASK_ID),
        headers=headers,
        json={"payload": {"jobTitle": job_title}},
        timeout=30,
    )
    if trigger_resp.status_code >= 400:
        raise HTTPException(502, f"Failed to trigger scrape task: {trigger_resp.text}")

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
            output = run_data.get("output") or {}
            return {"job_title": job_title, "jobs": output.get("jobs", [])}

        if status in TERMINAL_FAILURE_STATUSES:
            raise HTTPException(502, f"Scrape task ended with status: {status}")

        time.sleep(POLL_INTERVAL_SECONDS)

    raise HTTPException(504, "Timed out waiting for scrape task to complete")
