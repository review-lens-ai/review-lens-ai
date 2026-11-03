-- reviews: no reviewer names, a mapping version, and reviews outlive their run.
--
-- NO NAMES: the SerpApi adapter's mapping drops the reviewer's name, link and photo
-- (app/services/normalize_reviews.py). The column that would hold the name goes.
--
-- MAPPING VERSION: which mapping wrote the row (e.g. serpapi@1), so rows written
-- by an older mapping can be found and fixed after a mapping change.
--
-- LIFETIME: a review belongs to its target. run_id records the run that first saw
-- it, but deleting a run no longer deletes its reviews: a run that still has
-- reviews cannot be deleted at all. Deleting the target deletes everything.

alter table public.reviews drop column author_name;

-- No default: the writer must always say which mapping produced the row.
alter table public.reviews
    add column mapping_version text not null
        check (length(btrim(mapping_version)) > 0);

-- Was ON DELETE CASCADE from the run. NO ACTION (the default) refuses to delete a run
-- that has reviews, but is checked at the end of the statement, so deleting a target
-- (which removes its runs and its reviews together) still works.
alter table public.reviews drop constraint reviews_run_target_fkey;
alter table public.reviews
    add constraint reviews_run_target_fkey
        foreign key (run_id, target_id)
        references public.ingestion_runs (id, target_id),
    add constraint reviews_target_id_fkey
        foreign key (target_id)
        references public.analysis_targets (id) on delete cascade;
