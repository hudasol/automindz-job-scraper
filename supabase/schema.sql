-- Run this in the Supabase SQL editor (Project -> SQL Editor -> New query).

create extension if not exists pgcrypto;

create table if not exists jobs (
    id uuid primary key default gen_random_uuid(),
    job_url text not null unique,
    job_description text,
    company_name text,
    job_title text,
    search_query text,
    created_at timestamptz not null default now(),
    date_posted timestamptz
);

-- Run this against an existing table (created before date_posted existed):
-- alter table jobs add column if not exists date_posted timestamptz;

-- Speeds up "have we already stored jobs for this search" type lookups.
create index if not exists idx_jobs_search_query on jobs (search_query);

-- job_url is UNIQUE above, so an upsert with onConflict: "job_url" (used in the
-- Trigger.dev task) naturally avoids storing the same job twice.

-- Only the server-side Trigger.dev task (via service_role) ever writes to this table, and
-- nothing in the frontend/API exposes Supabase to the browser - so RLS is intentionally
-- disabled rather than written as per-row policies.
alter table public.jobs disable row level security;

-- service_role bypasses RLS but NOT table-level grants - without this, the task's Supabase
-- calls fail even with the right key.
grant select, insert, update, delete on public.jobs to service_role, anon, authenticated;

-- Caches Context.dev company enrichment (employee range + funding stage) so a company is
-- only ever billed once, regardless of how many searches/users surface its jobs. Keyed on
-- a normalized (trimmed, lowercased) company_name since that's all WeWorkRemotely's RSS
-- feed gives us - no company domain is available to key on instead.
create table if not exists company_enrichment (
    id uuid primary key default gen_random_uuid(),
    company_name text not null unique,
    domain text,
    employee_range text,
    funding_stage text,
    not_found boolean not null default false,
    -- Credits actually spent resolving this row (0/10/20) - summed to enforce the
    -- Context.dev spend budget in trigger/src/enrichCompany.ts before each new lookup.
    credits_spent integer not null default 0,
    enriched_at timestamptz not null default now()
);

-- Same reasoning as jobs above: only the Trigger.dev task touches this table.
grant select, insert, update, delete on public.company_enrichment to service_role;
