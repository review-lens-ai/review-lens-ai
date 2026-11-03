-- Enum values are UPPERCASE (docs/standards/coding-standards.md, Database).
--
-- Run statuses also take the pipeline diagram's names: a run ends COMPLETED,
-- not "succeeded". PARTIAL stays separate from COMPLETED (spec 4.3).
--
--   ingestion_status        queued, running, succeeded, partial, failed
--                        -> QUEUED, RUNNING, COMPLETED, PARTIAL, FAILED
--   ingestion_stage         ingestion, normalization, enrichment
--                        -> INGESTION, NORMALIZATION, ENRICHMENT
--   ingestion_stage_status  pending, running, completed, failed
--                        -> PENDING, RUNNING, COMPLETED, FAILED
--
-- RENAME VALUE keeps each value's identity, so existing rows, column defaults,
-- CHECK constraints and the one-active-run index follow the new names on their own.

alter type public.ingestion_status rename value 'queued' to 'QUEUED';
alter type public.ingestion_status rename value 'running' to 'RUNNING';
alter type public.ingestion_status rename value 'succeeded' to 'COMPLETED';
alter type public.ingestion_status rename value 'partial' to 'PARTIAL';
alter type public.ingestion_status rename value 'failed' to 'FAILED';

alter type public.ingestion_stage rename value 'ingestion' to 'INGESTION';
alter type public.ingestion_stage rename value 'normalization' to 'NORMALIZATION';
alter type public.ingestion_stage rename value 'enrichment' to 'ENRICHMENT';

alter type public.ingestion_stage_status rename value 'pending' to 'PENDING';
alter type public.ingestion_stage_status rename value 'running' to 'RUNNING';
alter type public.ingestion_stage_status rename value 'completed' to 'COMPLETED';
alter type public.ingestion_stage_status rename value 'failed' to 'FAILED';
