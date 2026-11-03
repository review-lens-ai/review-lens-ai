-- Ingestion pipeline schema, step 4 of 5: ingestion_stages, the stage checkpoints.
--
-- One row per stage of a run (ingestion -> normalization -> enrichment). A stage
-- is marked 'completed' ONLY after its data has been durably written. After a
-- failure the worker skips the completed stages and re-runs the first one that
-- did not complete. Ownership is resolved through the run and the target.
--
-- 'enrichment' (analysis of the reviews) is part of the planned pipeline but is
-- Sprint 2 work; the value exists so the schema does not have to change later.

create type public.ingestion_stage as enum ('ingestion', 'normalization', 'enrichment');
create type public.ingestion_stage_status as enum ('pending', 'running', 'completed', 'failed');

create table public.ingestion_stages (
    id            uuid                          primary key default gen_random_uuid(),
    run_id        uuid                          not null references public.ingestion_runs (id) on delete cascade,
    stage         public.ingestion_stage        not null,
    status        public.ingestion_stage_status not null default 'pending',
    -- Where to resume. For the ingestion stage this holds the provider's next
    -- page token, so a retry continues instead of starting over.
    cursor        text,
    -- Short machine-readable reason when failed (never raw exception text).
    error_code    text,
    created_at    timestamptz                   not null default now(),
    updated_at    timestamptz                   not null default now(),
    -- Each stage appears once per run.
    constraint ingestion_stages_run_id_stage_key unique (run_id, stage),
    -- A failure must say why; nothing else may carry an error.
    constraint ingestion_stages_error_only_when_failed
        check ((status = 'failed') = (error_code is not null))
);

create index ingestion_stages_run_id_idx on public.ingestion_stages (run_id);

create trigger ingestion_stages_set_updated_at
    before update on public.ingestion_stages
    for each row execute function public.set_updated_at();

alter table public.ingestion_stages enable row level security;
revoke all on table public.ingestion_stages from anon, authenticated;
