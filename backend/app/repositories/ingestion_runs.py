"""S1-023: record the result state of every ingestion attempt (table: ingestion_runs).

How the ingestion pipeline (S1-019 to S1-022) uses this module:

    run = runs.start_run(target_id, owner_id)          # status: running
    try:
        ...fetch, normalize and save reviews...
        runs.mark_succeeded(run["id"], saved, rejected)  # status: succeeded
    except SomeProviderError:
        runs.mark_failed(run["id"], ErrorCode.QUOTA_EXCEEDED)  # status: failed

Rules this module enforces:
- OWNERSHIP: a run can only be started or read for a target the user owns.
  Another user's target is NotFoundError, exactly like a missing one (S1-BR-008).
- NO FAKE SUCCESS: a failed run records zero new reviews and an error code; it
  never touches existing reviews from earlier runs (S1-BR-023, S1-BR-024).
- NO LEAKED INTERNALS: error_code is one of a fixed list of short codes. Raw
  provider or exception text is logged on the server, never stored here.
"""

import logging
from enum import StrEnum
from uuid import UUID

import psycopg

from app.core.errors import ConflictError, NotFoundError
from app.repositories.targets import TargetRepository

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Why a run failed. Each code maps to a user-facing message in the
    UI/UX standards (docs/standards/ui-ux-standards.md, section 6.3)."""

    SOURCE_UNREACHABLE = "source_unreachable"
    NO_REVIEWS_FOUND = "no_reviews_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    TIMEOUT = "timeout"
    UNEXPECTED = "unexpected"


_RUN_COLUMNS = """
    id, target_id, status::text as status, started_at, finished_at,
    reviews_ingested, reviews_rejected, error_code
"""


class IngestionRunRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._targets = TargetRepository(conn)

    def start_run(self, target_id: UUID | str, owner_id: UUID | str) -> dict:
        """Record that an ingestion attempt has begun (status 'running').

        Raises NotFoundError if the user does not own the target, and
        ConflictError if a run for this target is already in progress.
        """
        self._targets.get_owned(target_id, owner_id)  # ownership check first

        running = self._conn.execute(
            """
            select 1 from public.ingestion_runs
             where target_id = %s and status = 'running'
             limit 1
            """,
            (target_id,),
        ).fetchone()
        if running is not None:
            raise ConflictError(
                "Reviews are already being collected for this target. "
                "Wait for that to finish, then try again."
            )

        return self._conn.execute(
            f"""
            insert into public.ingestion_runs (target_id)
            values (%s)
            returning {_RUN_COLUMNS}
            """,
            (target_id,),
        ).fetchone()

    def mark_succeeded(
        self, run_id: UUID | str, reviews_ingested: int, reviews_rejected: int = 0
    ) -> dict:
        """Finish a running run as succeeded, with how many reviews were saved
        and how many were skipped as unusable."""
        if reviews_ingested < 0 or reviews_rejected < 0:
            raise ValueError("Review counts cannot be negative.")
        return self._finish(
            run_id,
            status="succeeded",
            reviews_ingested=reviews_ingested,
            reviews_rejected=reviews_rejected,
            error_code=None,
        )

    def mark_failed(
        self, run_id: UUID | str, error_code: ErrorCode, detail: str | None = None
    ) -> dict:
        """Finish a running run as failed.

        `detail` is the technical reason (exception text, provider message).
        It is written to the server log only, never to the database or the user.
        """
        error_code = ErrorCode(error_code)  # rejects anything not in the list
        if detail:
            logger.warning(
                "Ingestion run %s failed (%s): %s", run_id, error_code, detail
            )
        return self._finish(
            run_id,
            status="failed",
            reviews_ingested=0,
            reviews_rejected=0,
            error_code=error_code.value,
        )

    def latest_for_target(
        self, target_id: UUID | str, owner_id: UUID | str
    ) -> dict | None:
        """The newest run for a target the user owns, or None if it was never
        ingested. Another user's target raises NotFoundError."""
        self._targets.get_owned(target_id, owner_id)
        return self._conn.execute(
            f"""
            select {_RUN_COLUMNS}
              from public.ingestion_runs
             where target_id = %s
             order by started_at desc
             limit 1
            """,
            (target_id,),
        ).fetchone()

    def _finish(
        self,
        run_id: UUID | str,
        *,
        status: str,
        reviews_ingested: int,
        reviews_rejected: int,
        error_code: str | None,
    ) -> dict:
        # Only a run that is still 'running' can be finished, so a finished
        # run's result can never be overwritten later.
        row = self._conn.execute(
            f"""
            update public.ingestion_runs
               set status = %s::public.ingestion_status,
                   finished_at = now(),
                   reviews_ingested = %s,
                   reviews_rejected = %s,
                   error_code = %s
             where id = %s and status = 'running'
            returning {_RUN_COLUMNS}
            """,
            (status, reviews_ingested, reviews_rejected, error_code, run_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Ingestion run not found, or it already finished.")
        return row
