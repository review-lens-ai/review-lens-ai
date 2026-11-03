-- Ingestion pipeline schema, step 1 of 5: a run can wait in a queue before a worker starts it.
--
-- Pipeline: API request -> job manager creates the run as 'queued' -> async worker
-- picks it up and sets 'running' -> succeeded / partial / failed.
--
-- This file only adds the enum value. Postgres does not allow a brand-new enum
-- value to be USED in the same transaction that adds it, and the migration
-- script runs each file in its own transaction. The next migration is the first
-- one allowed to mention 'queued'.
-- (Row Level Security rationale for every table: see 20261025120000_create_users.sql.)

alter type public.ingestion_status add value if not exists 'queued' before 'running';
