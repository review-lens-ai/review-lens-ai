-- S3-009: where a refresh continues collecting older reviews.
--
-- A run reads newest first and stops at MAX_REVIEWS_PER_RUN new reviews, so a
-- place with more reviews than that is collected over several runs. Each refresh
-- first reads the newest pages until it reaches reviews the target already has,
-- then jumps to older_page_token, the SerpApi token for the page after the oldest
-- one any earlier run read, and keeps going from there.
--
--   older_page_token       NULL when there is no saved place to continue from (never
--                          ingested, or SerpApi refused the token: the next refresh
--                          walks down from the top instead).
--   all_reviews_collected  true once a run reached the last page: there is nothing
--                          older, so a refresh only looks for new reviews.
--
-- Both are written in the same transaction as the run's reviews (NORMALIZATION), so a
-- failed run never moves them past reviews it did not save.

alter table public.analysis_targets
    add column older_page_token text
        check (older_page_token is null or older_page_token ~ '\S'),
    add column all_reviews_collected boolean not null default false;

-- Targets ingested before S3-009: continue from where their newest finished run stopped.
-- Every run so far read from the top, so that run's last page is the deepest point.
with latest as (
    select distinct on (r.target_id)
           r.target_id,
           r.id as run_id,
           r.source_metadata ->> 'stopped_because' as stopped_because
      from public.ingestion_runs r
     where r.job_type = 'INGESTION'
       and r.status in ('COMPLETED', 'PARTIAL')
     order by r.target_id, r.queued_at desc
),
last_page as (
    select distinct on (p.run_id) p.run_id, p.next_page_token
      from public.ingestion_raw_pages p
      join latest l on l.run_id = p.run_id
     order by p.run_id, p.page_number desc
)
update public.analysis_targets t
   set older_page_token = case
           when l.stopped_because = 'CAP_REACHED' and lp.next_page_token ~ '\S'
               then lp.next_page_token
       end,
       all_reviews_collected = coalesce(
           l.stopped_because in ('NO_MORE_PAGES', 'EMPTY_PAGE', 'NO_RESULTS'), false
       )
  from latest l
  left join last_page lp on lp.run_id = l.run_id
 where t.id = l.target_id;
