-- S1-014: ingestion_rejections, records the source rows a run skipped.
-- A rejected record is NOT counted as an ingested review (spec 4.3). Ownership
-- is resolved through the run and the target.

create table public.ingestion_rejections (
    id          uuid        primary key default gen_random_uuid(),
    run_id      uuid        not null references public.ingestion_runs (id) on delete cascade,
    -- Why the record was skipped, e.g. "missing review text" or "invalid rating".
    reason      text        not null check (length(btrim(reason)) > 0),
    -- The original source record, kept so a rejection can be diagnosed.
    raw_record  jsonb,
    created_at  timestamptz not null default now()
);

create index ingestion_rejections_run_id_idx on public.ingestion_rejections (run_id);

alter table public.ingestion_rejections enable row level security;
revoke all on table public.ingestion_rejections from anon, authenticated;
