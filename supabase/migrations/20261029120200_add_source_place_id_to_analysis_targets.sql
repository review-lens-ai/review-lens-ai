-- Ingestion pipeline schema, step 3 of 5: remember the provider's id for the place.
--
-- SerpApi's Google Maps reviews engine is called with a place identifier
-- (data_id, e.g. 0x89c259af336b3341:0xa4969e07ce3108de, or place_id), NOT with the
-- Google Maps link the user pasted. The id is worked out from the link (or
-- looked up) the first time the target is ingested and saved here, so later runs
-- do not have to repeat the lookup.
--
-- Nullable: a brand-new target has not been ingested yet.

alter table public.analysis_targets
    add column source_place_id text
    check (source_place_id is null or length(btrim(source_place_id)) > 0);

comment on column public.analysis_targets.source_place_id is
    'Provider id for the place (SerpApi data_id or place_id); resolved from source_url at first ingestion.';
