-- analysis_targets: place details, one target per place, and no invisible names.

-- What the provider says about the place (page 1 of the first ingestion): title,
-- address, category, rating, review count. NULL until the target is ingested.
-- Shown in the workspace; the user's own `name` is never overwritten by it.
alter table public.analysis_targets
    add column source_metadata jsonb
        check (source_metadata is null or jsonb_typeof(source_metadata) = 'object');

-- A user cannot add the same place twice. source_place_id is optional (resolved
-- from the link at first ingestion), so the rule applies once it is known; the
-- ingestion that resolves it gets a unique-violation for a duplicate place.
create unique index analysis_targets_owner_place_key
    on public.analysis_targets (owner_id, platform, source_place_id)
    where source_place_id is not null;

-- btrim() only trims spaces, so a name made of tabs or newlines passed the old check.
-- The name must now contain at least one non-whitespace character.
alter table public.analysis_targets drop constraint analysis_targets_name_check;
alter table public.analysis_targets
    add constraint analysis_targets_name_check check (name ~ '\S');
