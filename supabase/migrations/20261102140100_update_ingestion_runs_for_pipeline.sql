-- ingestion_runs becomes the job table of the pipeline diagram.
--
-- JOB TYPE
--   INGESTION   fetch -> normalize -> (enrich). Writes reviews.
--   ENRICHMENT  re-runs only the analysis on reviews already stored, e.g. when the
--               analysis changes. Writes no reviews.
--   A target still has at most one QUEUED or RUNNING job of any type
--   (ingestion_runs_one_active_per_target_idx).
--
-- TIMESTAMPS
--   queued_at    when the run (last) entered the queue: created, or retried
--   started_at   when a worker began it (NULL while it waits in the queue, and for
--                a run that failed before it could start)
--   finished_at  when it reached COMPLETED, PARTIAL or FAILED
--   A run is stale when it is still QUEUED or RUNNING 10 minutes after it was
--   queued or started (app/repositories/ingestion_runs.py, STALE_RUN_AFTER).
--
-- RETRY (the one exception to "a finished run never changes")
--   A FAILED run is retried by moving the SAME row back to QUEUED: queued_at is
--   set to now, started_at, finished_at and error_code are cleared. Its
--   ingestion_stages rows stay, so the worker skips every stage already COMPLETED
--   and re-runs the first one that is not. COMPLETED and PARTIAL runs are final.
--
-- NO FAKE SUCCESS (spec 4.3)
--   An INGESTION run may only end COMPLETED or PARTIAL if it saved at least one
--   review. Trade-off: zero reviews is usually a mistake (wrong link, provider
--   outage, a mapping bug), but a place can also genuinely have no reviews yet.
--   We choose to never report an empty result as a success: the worker ends such
--   a run FAILED with error_code NO_REVIEWS_FOUND, and the user sees "this page
--   didn't return any reviews" instead of a green "Ingested, 0 reviews".
--   ENRICHMENT runs save no reviews, so the rule does not apply to them.

create type public.ingestion_job_type as enum ('INGESTION', 'ENRICHMENT');

alter table public.ingestion_runs
    add column job_type public.ingestion_job_type not null default 'INGESTION',
    add column queued_at timestamptz not null default now(),
    add column updated_at timestamptz not null default now(),
    -- Same review returned twice (e.g. by two sort orders). Not bad data, so it
    -- is counted apart from reviews_rejected: fetched = ingested + rejected + duplicate.
    add column reviews_duplicate integer not null default 0 check (reviews_duplicate >= 0),
    -- Run options and what actually happened: sort plan, language, pages planned
    -- vs fetched. This is how a PARTIAL run shows what is missing.
    add column source_metadata jsonb
        check (source_metadata is null or jsonb_typeof(source_metadata) = 'object');

alter table public.ingestion_runs
    alter column started_at drop default,
    alter column started_at drop not null;

-- Existing rows: their started_at was really the time they were queued.
update public.ingestion_runs set queued_at = started_at;
update public.ingestion_runs set started_at = null where status = 'QUEUED';

alter table public.ingestion_runs
    -- A run that is running or produced a result has a start time. QUEUED runs and
    -- runs that FAILED before a worker started them (e.g. stale) may not.
    add constraint ingestion_runs_started_when_running_or_done
        check (status not in ('RUNNING', 'COMPLETED', 'PARTIAL') or started_at is not null),
    -- Same rule as ingestion_stages: a failure says why, nothing else carries a code.
    add constraint ingestion_runs_error_only_when_failed
        check ((status = 'FAILED') = (error_code is not null)),
    add constraint ingestion_runs_success_has_reviews
        check (
            job_type <> 'INGESTION'
            or status not in ('COMPLETED', 'PARTIAL')
            or reviews_ingested > 0
        );

-- "Latest run" means the most recently queued one.
drop index public.ingestion_runs_target_id_started_at_idx;
create index ingestion_runs_target_id_queued_at_idx
    on public.ingestion_runs (target_id, queued_at desc);

create trigger ingestion_runs_set_updated_at
    before update on public.ingestion_runs
    for each row execute function public.set_updated_at();
