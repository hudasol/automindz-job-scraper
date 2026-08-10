/**
 * Trigger.dev task: scrape-jobs
 *
 * Payload: { jobTitle: string }
 * Output:  { jobs: Array<{ job_url, job_description, company_name, job_title }> }
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

const execFileAsync = promisify(execFile);
const PYTHON_BIN = process.platform === "win32" ? "python" : "python3";

interface ScrapedJob {
  job_url: string;
  job_description: string;
  company_name: string;
  job_title: string;
}

const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!
);

export const scrapeJobs = task({
  id: "scrape-jobs",
  run: async (payload: { jobTitle: string }) => {
    logger.log("Scraping WeWorkRemotely", { jobTitle: payload.jobTitle });

    const { stdout } = await execFileAsync(
      PYTHON_BIN,
      ["-m", "scraper.weworkremotely", payload.jobTitle],
      { cwd: process.cwd(), maxBuffer: 10 * 1024 * 1024 }
    );

    const jobs: ScrapedJob[] = JSON.parse(stdout);
    logger.log(`Scraped ${jobs.length} job(s)`);

    if (jobs.length > 0) {
      const { error } = await supabase
        .from("jobs")
        .upsert(
          jobs.map((j) => ({ ...j, search_query: payload.jobTitle })),
          { onConflict: "job_url", ignoreDuplicates: true }
        );

      if (error) {
        logger.error("Supabase upsert failed", { error });
        throw new Error(`Supabase upsert failed: ${error.message}`);
      }
    }

    return { jobs };
  },
});
