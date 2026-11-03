-- Ingestion pipeline schema, step 2 of 5: teach ingestion_runs about 'queued'.
--
-- The original rule was "finished_at is set exactly when status is not running".
-- A queued run has not finished either, so the rule becomes: finished_at is set
-- exactly when the run reached a final status (succeeded, partial or failed).

alter table public.ingestion_runs
    drop constraint ingestion_runs_finished_matches_status;

alter table public.ingestion_runs
    add constraint ingestion_runs_finished_matches_status
    check ((status in ('succeeded', 'partial', 'failed')) = (finished_at is not null));

-- New runs start waiting in the queue; the worker flips them to 'running'.
alter table public.ingestion_runs
    alter column status set default 'queued';
