"""S1-023: record the result state of every ingestion attempt (table: ingestion_runs).

Run lifecycle (matches the team's pipeline diagram):

    queued ──► running ──► succeeded
       │          │   └──► partial   (some records skipped)
       │          └──────► failed
       └─────────────────► failed    (could not start)

How the pipeline (S1-019 to S1-022) uses this module:

    run = runs.create_run(target_id, owner_id)    # API request:  queued
    runs.mark_running(run["id"])                  # worker starts: running
    try:
        ...fetch, normalize and save reviews...
        runs.mark_succeeded(run["id"], saved, skipped)  # succeeded / partial / failed
    except ProviderQuotaError as e:
        runs.mark_failed(run["id"], ErrorCode.QUOTA_EXCEEDED, detail=str(e))

Result rule used by mark_succeeded (S1-BR-023, S1-BR-024):
- saved > 0 and skipped == 0  -> succeeded
- saved > 0 and skipped > 0   -> partial
- saved == 0                  -> failed with NO_REVIEWS_FOUND
  (a run that saved nothing is never reported as a success)

Other rules this module enforces:
- OWNERSHIP: a run can only be created or read for a target the user owns.
  Another user's target is NotFoundError, exactly like a missing one (S1-BR-008).
- ONE ACTIVE RUN: a target has at most one queued or running run. The database
  enforces it with a unique index; this module turns a clash into ConflictError.
- NO FAKE SUCCESS: only a running run can succeed; a finished run is locked.
- STALE RUNS: a queued or running run older than STALE_RUN_AFTER is failed as
  TIMEOUT before a new run is created, so a crash never blocks a target forever.
- NO LEAKED INTERNALS: error_code is one of a fixed list. Technical detail is
  scrubbed (API keys, query strings) and logged; it is never stored or returned.
"""

import logging
import re
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

import psycopg

from app.core.errors import ConflictError
from app.repositories.targets import TargetRepository

logger = logging.getLogger(__name__)

# A run still queued or running after this long is treated as crashed.
STALE_RUN_AFTER = timedelta(minutes=10)

# Longest technical detail written to the log, after scrubbing.
MAX_LOGGED_DETAIL = 500


class ErrorCode(StrEnum):
    """Why a run failed. Each code maps to a user-facing message in the
    UI/UX standards (docs/standards/ui-ux-standards.md, section 6.3)."""

    SOURCE_UNREACHABLE = "source_unreachable"
    NO_REVIEWS_FOUND = "no_reviews_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    TIMEOUT = "timeout"
    UNEXPECTED = "unexpected"


class InvalidRunTransitionError(ConflictError):
    """The run is not in a state that allows this change (e.g. marking an
    already running run as running, or succeeding a run that never started)."""

    message = "This ingestion run can't be changed from its current state."


_RUN_COLUMNS = """
    id, target_id, status::text as status, started_at, finished_at,
    reviews_ingested, reviews_rejected, error_code
"""

_ALREADY_ACTIVE = (
    "Reviews are already being collected for this target. "
    "Wait for that to finish, then try again."
)

# Matches the query string of a URL, e.g. "?engine=google_maps&api_key=abc".
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s#]*", re.IGNORECASE)
# Matches key=value secrets that appear outside a URL.
_SECRET_PARAM = re.compile(r"\b(api_key|apikey|key|token)=[^&\s]+", re.IGNORECASE)


def scrub_detail(detail: str) -> str:
    """Make technical failure text safe to log.

    SerpApi puts the API key in the request URL, and HTTP library errors often
    include that URL. Drop every query string, redact key/token values, and cap
    the length."""
    text = _URL_QUERY.sub(r"\1?[redacted]", detail)
    text = _SECRET_PARAM.sub(r"\1=[redacted]", text)
    if len(text) > MAX_LOGGED_DETAIL:
        text = text[:MAX_LOGGED_DETAIL] + "…"
    return text


class IngestionRunRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._targets = TargetRepository(conn)

    def create_run(self, target_id: UUID | str, owner_id: UUID | str) -> dict:
        """Queue a new ingestion attempt (status 'queued').

        Raises NotFoundError if the user does not own the target, and
        ConflictError if a run for this target is already queued or running.
        """
        self._targets.get_owned(target_id, owner_id)  # ownership check first
        self._fail_stale_runs(target_id)

        # Friendly early answer. Not airtight on its own: two requests can both
        # pass it, which is why the unique index below has the final say.
        active = self._conn.execute(
            """
            select 1 from public.ingestion_runs
             where target_id = %s and status in ('queued', 'running')
             limit 1
            """,
            (target_id,),
        ).fetchone()
        if active is not None:
            raise ConflictError(_ALREADY_ACTIVE)

        try:
            # A savepoint, so a unique-index clash does not abort the caller's
            # whole transaction.
            with self._conn.transaction():
                return self._conn.execute(
                    f"""
                    insert into public.ingestion_runs (target_id, status)
                    values (%s, 'queued')
                    returning {_RUN_COLUMNS}
                    """,
                    (target_id,),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ConflictError(_ALREADY_ACTIVE) from None

    def mark_running(self, run_id: UUID | str) -> dict:
        """The worker has picked the run up and is about to fetch reviews."""
        row = self._conn.execute(
            f"""
            update public.ingestion_runs
               set status = 'running'
             where id = %s and status = 'queued'
            returning {_RUN_COLUMNS}
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise InvalidRunTransitionError(
                "Only a queued ingestion run can start running."
            )
        return row

    def mark_succeeded(
        self, run_id: UUID | str, reviews_ingested: int, reviews_rejected: int = 0
    ) -> dict:
        """Finish a running run using the result rule in the module docstring:
        succeeded, partial (some skipped) or failed (nothing saved)."""
        _check_counts(reviews_ingested, reviews_rejected)
        if reviews_ingested == 0:
            return self._finish(
                run_id,
                status="failed",
                allowed_from=("running",),
                reviews_ingested=0,
                reviews_rejected=reviews_rejected,
                error_code=ErrorCode.NO_REVIEWS_FOUND.value,
            )
        status = "partial" if reviews_rejected > 0 else "succeeded"
        return self._finish(
            run_id,
            status=status,
            allowed_from=("running",),
            reviews_ingested=reviews_ingested,
            reviews_rejected=reviews_rejected,
            error_code=None,
        )

    def mark_partial(
        self, run_id: UUID | str, reviews_ingested: int, reviews_rejected: int
    ) -> dict:
        """Finish a running run where some reviews were saved and some skipped."""
        if reviews_ingested <= 0 or reviews_rejected <= 0:
            raise ValueError("A partial run saves some reviews and skips some.")
        return self.mark_succeeded(run_id, reviews_ingested, reviews_rejected)

    def mark_failed(
        self, run_id: UUID | str, error_code: ErrorCode, detail: str | None = None
    ) -> dict:
        """Finish a queued or running run as failed.

        `detail` is the technical reason (exception text, provider message). It
        is scrubbed and written to the server log only, never stored or returned.
        """
        error_code = ErrorCode(error_code)  # rejects anything not in the list
        if detail:
            logger.warning(
                "Ingestion run %s failed (%s): %s",
                run_id,
                error_code,
                scrub_detail(detail),
            )
        return self._finish(
            run_id,
            status="failed",
            allowed_from=("queued", "running"),
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

    def _fail_stale_runs(self, target_id: UUID | str) -> None:
        """Fail any queued or running run that has been stuck too long."""
        self._conn.execute(
            """
            update public.ingestion_runs
               set status = 'failed',
                   finished_at = now(),
                   error_code = %s
             where target_id = %s
               and status in ('queued', 'running')
               and started_at < now() - %s
            """,
            (ErrorCode.TIMEOUT.value, target_id, STALE_RUN_AFTER),
        )

    def _finish(
        self,
        run_id: UUID | str,
        *,
        status: str,
        allowed_from: tuple[str, ...],
        reviews_ingested: int,
        reviews_rejected: int,
        error_code: str | None,
    ) -> dict:
        # Only a run in an allowed state can be finished, so a finished run's
        # result can never be overwritten later.
        row = self._conn.execute(
            f"""
            update public.ingestion_runs
               set status = %s::public.ingestion_status,
                   finished_at = now(),
                   reviews_ingested = %s,
                   reviews_rejected = %s,
                   error_code = %s
             where id = %s and status::text = any(%s)
            returning {_RUN_COLUMNS}
            """,
            (
                status,
                reviews_ingested,
                reviews_rejected,
                error_code,
                run_id,
                list(allowed_from),
            ),
        ).fetchone()
        if row is None:
            raise InvalidRunTransitionError(
                f"Only a {' or '.join(allowed_from)} ingestion run can become {status}."
            )
        return row


def _check_counts(reviews_ingested: int, reviews_rejected: int) -> None:
    if reviews_ingested < 0 or reviews_rejected < 0:
        raise ValueError("Review counts cannot be negative.")
