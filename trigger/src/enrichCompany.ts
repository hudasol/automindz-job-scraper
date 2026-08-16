/**
 * Trigger.dev task: enrich-company
 *
 * Payload: { companyName: string }
 * Output:  { company_size: string | null, funding_stage: string | null }
 *
 * On-demand (triggered per job card from the frontend, never automatically for a whole
 * search) lookup of a company's employee-count range and latest funding stage via
 * Context.dev. Results are cached in Supabase's `company_enrichment` table, keyed on a
 * normalized company_name, so a given company is only ever billed once.
 *
 * Two Context.dev calls, both real money (10 credits each, confirmed via
 * docs.context.dev - there is no free tier for either):
 *   1. POST /v1/brand/retrieve {type: "by_name", name} -> resolves company_name to a
 *      domain and returns `employees.range` directly.
 *   2. POST /v1/web/extract on that domain, with a JSON Schema asking for funding_stage.
 *      Context.dev crawls the company's own site for this - if the company doesn't
 *      publish it (no press/about page, private company, etc.), the field is simply
 *      absent from the response. That is treated as "unknown", not retried, and never
 *      guessed at.
 *
 * A running total of credits actually spent (summed from `company_enrichment.credits_spent`)
 * is checked against CREDIT_BUDGET before every new lookup - the user approved spending up
 * to 250 credits and asked to be consulted before going past that, so once the budget would
 * be exceeded this task stops calling Context.dev entirely rather than spending more without
 * asking first.
 */

import { task, logger } from "@trigger.dev/sdk";
import { createClient } from "@supabase/supabase-js";

const CONTEXT_DEV_API_BASE = "https://api.context.dev/v1";
const CREDIT_BUDGET = 250;
const MAX_CALL_COST = 20; // worst case: brand/retrieve (10) + web/extract (10)

const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!
);

interface EnrichmentResult {
  company_size: string | null;
  funding_stage: string | null;
}

interface CompanyEnrichmentRow {
  company_name: string;
  domain: string | null;
  employee_range: string | null;
  funding_stage: string | null;
  not_found: boolean;
}

function normalizeCompanyName(companyName: string): string {
  return companyName.trim().toLowerCase();
}

async function contextDevRequest(
  path: string,
  body: Record<string, unknown>
): Promise<{ data: any; creditsConsumed: number }> {
  const res = await fetch(`${CONTEXT_DEV_API_BASE}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.CONTEXT_DEV_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60_000),
  });
  const json = await res.json();
  const creditsConsumed: number = json?.key_metadata?.credits_consumed ?? 0;
  if (!res.ok) {
    throw Object.assign(new Error(`Context.dev ${path} returned ${res.status}: ${json?.message ?? "unknown error"}`), {
      creditsConsumed,
    });
  }
  return { data: json, creditsConsumed };
}

export const enrichCompany = task({
  id: "enrich-company",
  run: async (payload: { companyName: string }): Promise<EnrichmentResult> => {
    const normalizedName = normalizeCompanyName(payload.companyName);

    const { data: cached, error: cacheReadError } = await supabase
      .from("company_enrichment")
      .select("domain, employee_range, funding_stage, not_found")
      .eq("company_name", normalizedName)
      .maybeSingle<CompanyEnrichmentRow>();

    if (cacheReadError) {
      logger.warn("company_enrichment cache read failed", { error: cacheReadError });
    }
    if (cached) {
      logger.log("Serving company enrichment from cache", { companyName: payload.companyName });
      return { company_size: cached.employee_range, funding_stage: cached.funding_stage };
    }

    // Context.dev's /brand/retrieve requires a 3-30 char name; too short/long to be a
    // meaningful lookup anyway, so skip the call entirely rather than spend credits on a
    // request we already know will fail.
    if (normalizedName.length < 3 || payload.companyName.length > 30) {
      logger.warn("Company name out of Context.dev's supported length, skipping enrichment", {
        companyName: payload.companyName,
      });
      return { company_size: null, funding_stage: null };
    }

    const { data: spendRows, error: spendError } = await supabase
      .from("company_enrichment")
      .select("credits_spent");
    if (spendError) {
      logger.warn("Failed to read Context.dev credit spend, skipping enrichment to be safe", { error: spendError });
      return { company_size: null, funding_stage: null };
    }
    const creditsSpentSoFar = (spendRows ?? []).reduce((sum, row) => sum + (row.credits_spent ?? 0), 0);
    if (creditsSpentSoFar + MAX_CALL_COST > CREDIT_BUDGET) {
      logger.warn("Context.dev credit budget would be exceeded - skipping enrichment, needs approval to raise the budget", {
        creditsSpentSoFar,
        CREDIT_BUDGET,
      });
      return { company_size: null, funding_stage: null };
    }

    let creditsThisRun = 0;
    let domain: string | null = null;
    let employeeRange: string | null = null;
    let fundingStage: string | null = null;

    try {
      const { data, creditsConsumed } = await contextDevRequest("/brand/retrieve", {
        type: "by_name",
        name: payload.companyName,
      });
      creditsThisRun += creditsConsumed;
      domain = data?.domain ?? null;
      employeeRange = data?.employees?.range ?? null;
    } catch (error) {
      const creditsConsumed = (error as { creditsConsumed?: number })?.creditsConsumed ?? 0;
      creditsThisRun += creditsConsumed;
      logger.warn("Context.dev /brand/retrieve failed", { companyName: payload.companyName, error: String(error) });
    }

    if (domain) {
      try {
        const { data, creditsConsumed } = await contextDevRequest("/web/extract", {
          url: domain.startsWith("http") ? domain : `https://${domain}`,
          schema: {
            type: "object",
            properties: {
              funding_stage: {
                type: "string",
                description:
                  "The company's most recent funding round or stage (e.g. Seed, Series A, Series B, IPO, Acquired). Leave blank if not stated on the site.",
              },
            },
          },
          instructions:
            "Look only on an About, Press, News, or Investors page for a publicly stated funding round or stage. Do not infer or guess - leave the field blank if it isn't explicitly stated.",
          maxPages: 5,
        });
        creditsThisRun += creditsConsumed;
        const found = typeof data?.data?.funding_stage === "string" ? data.data.funding_stage.trim() : "";
        fundingStage = found.length > 0 ? found : null;
      } catch (error) {
        const creditsConsumed = (error as { creditsConsumed?: number })?.creditsConsumed ?? 0;
        creditsThisRun += creditsConsumed;
        logger.warn("Context.dev /web/extract failed", { companyName: payload.companyName, domain, error: String(error) });
      }
    }

    const { error: upsertError } = await supabase.from("company_enrichment").upsert(
      {
        company_name: normalizedName,
        domain,
        employee_range: employeeRange,
        funding_stage: fundingStage,
        not_found: !domain,
        credits_spent: creditsThisRun,
        enriched_at: new Date().toISOString(),
      },
      { onConflict: "company_name" }
    );
    if (upsertError) {
      logger.warn("Failed to cache company_enrichment result", { error: upsertError });
    }

    return { company_size: employeeRange, funding_stage: fundingStage };
  },
});
