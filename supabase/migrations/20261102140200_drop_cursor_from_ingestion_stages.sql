-- A stage that fails restarts from its beginning (pipeline diagram). Resuming in the
-- middle of a stage is not supported, so the resume cursor is not needed.
--
-- Retry reuses these rows: see 20261102140100_update_ingestion_runs_for_pipeline.sql.

alter table public.ingestion_stages drop column cursor;
