"""S1-020: stage checkpoints of an ingestion run (table: ingestion_stages).

A run moves through stages in order: INGESTION (fetch and store raw pages), then
NORMALIZATION (turn stored pages into reviews and save them), then ENRICHMENT
(Sprint 2). A stage is marked COMPLETED only after its data is durably written.
When a FAILED run is retried (IngestionRunRepository.requeue), its stage rows
stay, so the worker skips every COMPLETED stage and re-runs the first one that
is not, from its beginning.

Worker only (app/worker.py). Ownership was checked when the run was created;
stage rows are reachable only through the run and its target.
"""

from enum import StrEnum
from uuid import UUID

import psycopg


class Stage(StrEnum):
    INGESTION = "INGESTION"
    NORMALIZATION = "NORMALIZATION"
    ENRICHMENT = "ENRICHMENT"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IngestionStageRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def statuses(self, run_id: UUID | str) -> dict[Stage, StageStatus]:
        """Each recorded stage of the run and its status. A stage that never
        started has no row and is missing from the result."""
        rows = self._conn.execute(
            """
            select stage::text as stage, status::text as status
              from public.ingestion_stages
             where run_id = %s
            """,
            (run_id,),
        ).fetchall()
        return {Stage(row["stage"]): StageStatus(row["status"]) for row in rows}

    def start(self, run_id: UUID | str, stage: Stage) -> None:
        """Mark the stage RUNNING, creating its row on the run's first attempt
        and clearing an earlier failure on a retry."""
        self._conn.execute(
            """
            insert into public.ingestion_stages (run_id, stage, status)
            values (%s, %s, 'RUNNING')
            on conflict (run_id, stage)
            do update set status = 'RUNNING', error_code = null
            """,
            (run_id, Stage(stage).value),
        )

    def complete(self, run_id: UUID | str, stage: Stage) -> None:
        """The stage's data is written; a retry will skip it."""
        self._set(run_id, stage, StageStatus.COMPLETED, None)

    def fail(self, run_id: UUID | str, stage: Stage, error_code: str) -> None:
        """The stage stopped; error_code is a short code (ErrorCode), never raw
        exception text."""
        self._set(run_id, stage, StageStatus.FAILED, str(error_code))

    def _set(
        self,
        run_id: UUID | str,
        stage: Stage,
        status: StageStatus,
        error_code: str | None,
    ) -> None:
        row = self._conn.execute(
            """
            update public.ingestion_stages
               set status = %s, error_code = %s
             where run_id = %s and stage = %s
            returning id
            """,
            (status.value, error_code, run_id, Stage(stage).value),
        ).fetchone()
        if row is None:
            raise ValueError(f"Stage {stage} was never started for run {run_id}.")
