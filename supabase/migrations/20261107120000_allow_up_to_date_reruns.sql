-- A re-run that finds only reviews we already hold is a success, not a failure.
--
-- Before: an INGESTION run could end COMPLETED or PARTIAL only if it saved at least one
-- review, so a re-run of an up-to-date target ended FAILED (NO_REVIEWS_FOUND) even though
-- nothing was wrong.
-- Now: it may also end COMPLETED or PARTIAL when it saw duplicates, i.e. reviews already
-- saved. "NO FAKE SUCCESS" still holds: a run that found no reviews at all (nothing saved,
-- nothing duplicate) is still FAILED.

alter table public.ingestion_runs
    drop constraint ingestion_runs_success_has_reviews,
    add constraint ingestion_runs_success_has_reviews
        check (
            job_type <> 'INGESTION'
            or status not in ('COMPLETED', 'PARTIAL')
            or reviews_ingested > 0
            or reviews_duplicate > 0
        );
