-- A target may have at most ONE run that is still waiting or working.
--
-- "Active" means status 'queued' or 'running'. Finished runs (succeeded, partial,
-- failed) are history and a target can have any number of them.
--
-- Why the database enforces this instead of the application: two requests can
-- arrive at the same moment (a double-clicked Start button, a retry). The app
-- checks "is a run already active?" and then inserts, and two requests can both
-- pass that check before either inserts. A unique index closes the gap: the second
-- insert fails with a unique-violation error, which the application turns into a
-- friendly 409 ("already being collected").
--
-- This is a separate migration from the one that added 'queued' because Postgres
-- does not allow a new enum value to be used in the same transaction that creates it.

create unique index ingestion_runs_one_active_per_target_idx
    on public.ingestion_runs (target_id)
    where status in ('queued', 'running');
