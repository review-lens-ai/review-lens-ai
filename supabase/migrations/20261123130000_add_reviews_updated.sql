-- S3-010: a refresh updates reviews that were edited on the source.
--
-- A review the target already has (same source_review_id) that comes back with new
-- text, a new rating or a new edit date is updated in place, not skipped
-- (ReviewRepository.save_reviews). reviews_updated counts those, so a run's numbers
-- still add up: fetched = ingested + updated + rejected + duplicate.

alter table public.ingestion_runs
    add column reviews_updated integer not null default 0 check (reviews_updated >= 0);

-- A run that only updated edited reviews found real data: it may succeed too.
-- "NO FAKE SUCCESS" still holds: a run that found nothing at all is FAILED.
alter table public.ingestion_runs
    drop constraint ingestion_runs_success_has_reviews,
    add constraint ingestion_runs_success_has_reviews
        check (
            job_type <> 'INGESTION'
            or status not in ('COMPLETED', 'PARTIAL')
            or reviews_ingested > 0
            or reviews_duplicate > 0
            or reviews_updated > 0
        );
