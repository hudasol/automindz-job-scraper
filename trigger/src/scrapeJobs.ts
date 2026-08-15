/**
 * Trigger.dev task: scrape-jobs
 *
 * Payload: { jobTitle: string }
 * Output:  { jobs: Array<{ job_url, job_description, company_name, job_title, date_posted,
 *            job_type }> }
 *
 * job_type is dropped before the Supabase upsert (no column for it - it's a best-effort,
 * display-only guess, see scraper/weworkremotely.py) but stays in the returned `jobs` array.
 *
 * Trigger.dev tasks run in Node/TypeScript. The actual scraping logic lives in Python
 * (scraper/weworkremotely.py at the repo root) - this task shells out to it, then
 * upserts whatever it returns into Supabase, deduping on job_url.
 *
 * Verified against Trigger.dev's live docs on 2026-08-10: current task definition syntax
 * imports from "@trigger.dev/sdk" (not the older "@trigger.dev/sdk/v3" subpath), and
 * `task({...})` is exported directly without awaiting the definition itself - only
 * `.trigger()` calls (or REST API triggers, as used from api/main.py here) are async.
 * Source: https://trigger.dev/docs/tasks/overview
 *
 * Also note the working directory assumption: this expects to be run from the repo
 * root (where scraper/ lives), so the Python module can be found via `-m`. If your
 * Trigger.dev deploy config runs from a different cwd, adjust the `cwd` below or the
 * module path.
 */

import { task, logger } from "@trigger.dev/sdk";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createClient } from "@supabase/supabase-js";
import path from "node:path";

const execFileAsync = promisify(execFile);
const PYTHON_BIN = process.platform === "win32" ? "python" : "python3";
const REPO_ROOT = process.cwd();

interface ScrapedJob {
  job_url: string;
  job_description: string;
  company_name: string;
  job_title: string;
  date_posted: string | null;
  job_type: string;
}

const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!
);

async function notifyN8n(webhookUrl: string, job: ScrapedJob) {
  try {
    const res = await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(job),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      logger.warn("n8n webhook returned a non-OK status", { status: res.status, job_url: job.job_url });
    }
  } catch (error) {
    logger.warn("n8n webhook call failed", { error, job_url: job.job_url });
  }
}

export const scrapeJobs = task({
  id: "scrape-jobs",
  run: async (payload: { jobTitle: string }) => {
    logger.log("Scraping WeWorkRemotely", { jobTitle: payload.jobTitle });

    const { stdout, stderr } = await execFileAsync(
      PYTHON_BIN,
      ["-m", "scraper.weworkremotely", payload.jobTitle],
      { cwd: REPO_ROOT, maxBuffer: 10 * 1024 * 1024 }
    );

    if (stderr) {
      logger.warn("scraper stderr", { stderr });
    }

    const jobs: ScrapedJob[] = JSON.parse(stdout);
    logger.log(`Scraped ${jobs.length} job(s)`);

    if (jobs.length > 0) {
      // job_type has no column in Supabase (best-effort, display-only field - see
      // scraper/weworkremotely.py's _infer_job_type) so it's dropped before the upsert to
      // avoid an unknown-column error; it stays on the `jobs` array returned below.
      const rows = jobs.map(({ job_type, ...rest }) => ({
        ...rest,
        search_query: payload.jobTitle,
      }));
      // ON CONFLICT DO NOTHING (ignoreDuplicates) means Postgres's RETURNING - and so
      // this .select() - only reflects rows actually inserted just now, i.e. job_urls
      // genuinely never seen before this run, not merely new to this search.
      const { data: insertedRows, error } = await supabase
        .from("jobs")
        .upsert(rows, { onConflict: "job_url", ignoreDuplicates: true })
        .select("job_url");

      if (error) {
        logger.error("Supabase upsert failed", { error });
        throw new Error(`Supabase upsert failed: ${error.message}`);
      }

      const webhookUrl = process.env.N8N_WEBHOOK_URL;
      if (webhookUrl && insertedRows && insertedRows.length > 0) {
        const newJobUrls = new Set(insertedRows.map((row) => row.job_url));
        const newJobs = jobs.filter((job) => newJobUrls.has(job.job_url));
        logger.log(`Notifying n8n of ${newJobs.length} first-time-seen job(s)`);
        await Promise.allSettled(newJobs.map((job) => notifyN8n(webhookUrl, job)));
      }
    }

    return { jobs };
  },
});
