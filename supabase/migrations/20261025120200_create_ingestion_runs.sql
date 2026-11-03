-- S1-014: ingestion_runs, one row per attempt to collect reviews for a target.
-- Ownership is resolved through the parent target (target_id -> owner_id).
-- The ingestion logic that writes these rows belongs to S1-019/S1-023.

create type public.ingestion_status as enum ('running', 'succeeded', 'partial', 'failed');

create table public.ingestion_runs (
    id                uuid                    primary key default gen_random_uuid(),
    target_id         uuid                    not null references public.analysis_targets (id) on delete cascade,
    status            public.ingestion_status not null default 'running',
    started_at        timestamptz             not null default now(),
    finished_at       timestamptz,
    reviews_ingested  integer                 not null default 0 check (reviews_ingested >= 0),
    reviews_rejected  integer                 not null default 0 check (reviews_rejected >= 0),
    -- Short machine-readable reason (e.g. quota_exceeded). Never raw provider
    -- or exception text: user-visible errors must not leak internals.
    error_code        text,
    -- A run is finished exactly when it is no longer 'running'.
    constraint ingestion_runs_finished_matches_status
        check ((status = 'running') = (finished_at is null)),
    -- Lets reviews prove that a run and a review belong to the same target.
    constraint ingestion_runs_id_target_id_key unique (id, target_id)
);

create index ingestion_runs_target_id_started_at_idx
    on public.ingestion_runs (target_id, started_at desc);

alter table public.ingestion_runs enable row level security;
revoke all on table public.ingestion_runs from anon, authenticated;
