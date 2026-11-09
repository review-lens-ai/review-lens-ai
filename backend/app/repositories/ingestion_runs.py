"""S1-023: record the result state of every ingestion attempt (table: ingestion_runs).

Run lifecycle (matches the team's pipeline diagram):

    QUEUED ──► RUNNING ──► COMPLETED
       ▲ │          │   └──► PARTIAL   (some records skipped)
       │ │          └──────► FAILED
       │ └─────────────────► FAILED    (could not start)
       └──── requeue() ◄──── FAILED    (retry: the same run, completed stages kept)

How the pipeline (S1-019 to S1-022) uses this module:

    run = runs.create_run(target_id, owner_id)    # API request:  QUEUED
    runs.claim_next_queued()                      # worker starts: RUNNING
    try:
        ...fetch, normalize and save reviews...
        runs.mark_succeeded(run["id"], saved, skipped, duplicates)
        #                                         COMPLETED / PARTIAL / FAILED
    except ProviderQuotaError as e:
        runs.mark_failed(run["id"], ErrorCode.QUOTA_EXCEEDED, detail=str(e))

Result rule used by mark_succeeded (S1-BR-023, S1-BR-024):
- saved > 0 and skipped == 0  -> COMPLETED
- saved > 0 and skipped > 0   -> PARTIAL
- saved == 0 and duplicates > 0 -> COMPLETED ("already up to date": a re-run that
  found only reviews we already hold), or PARTIAL if some were also skipped
- saved == 0 and duplicates == 0 -> FAILED with NO_REVIEWS_FOUND
  (a run that found nothing at all is never reported as a success; the database
  enforces this too, see the ingestion_runs migrations)
Duplicates (the same review returned twice) are counted, never treated as skipped.

Other rules this module enforces:
- OWNERSHIP: a run can only be created or read for a target the user owns.
  Another user's target is NotFoundError, exactly like a missing one (S1-BR-008).
- ONE ACTIVE RUN: a target has at most one QUEUED or RUNNING run. The database
  enforces it with a unique index; this module turns a clash into ConflictError.
- NO FAKE SUCCESS: only a RUNNING run can succeed. COMPLETED and PARTIAL are
  final; the only way out of FAILED is requeue().
- STALE RUNS: a QUEUED or RUNNING run with no progress for STALE_RUN_AFTER (since
  it was queued or started) is failed as TIMEOUT before a new run is created, so
  a crash never blocks a target forever.
- NO LEAKED INTERNALS: error_code is one of a fixed list. Technical detail is
  scrubbed (API keys, query strings) and logged; it is never stored or returned.
"""

import logging
import re
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

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

    SOURCE_UNREACHABLE = "SOURCE_UNREACHABLE"
    NO_REVIEWS_FOUND = "NO_REVIEWS_FOUND"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    UNEXPECTED = "UNEXPECTED"


class InvalidRunTransitionError(ConflictError):
    """The run is not in a state that allows this change (e.g. marking an
    already running run as running, or succeeding a run that never started)."""

    message = "This ingestion run can't be changed from its current state."


_RUN_COLUMNS = """
    id, target_id, job_type::text as job_type, status::text as status,
    queued_at, started_at, finished_at,
    reviews_ingested, reviews_rejected, reviews_duplicate, error_code
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
        """Queue a new ingestion attempt (status QUEUED).

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
             where target_id = %s and status in ('QUEUED', 'RUNNING')
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
                    values (%s, 'QUEUED')
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
               set status = 'RUNNING', started_at = now()
             where id = %s and status = 'QUEUED'
            returning {_RUN_COLUMNS}
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise InvalidRunTransitionError(
                "Only a queued ingestion run can start running."
            )
        return row

    def claim_next_queued(self) -> dict | None:
        """The worker (app/worker.py) takes the oldest QUEUED ingestion run and
        marks it RUNNING, or returns None if nothing is queued.

        FOR UPDATE SKIP LOCKED: two workers asking at the same moment never get
        the same run. Ownership was checked when the run was created.
        """
        return self._conn.execute(
            f"""
            update public.ingestion_runs
               set status = 'RUNNING', started_at = now()
             where id = (
                    select id from public.ingestion_runs
                     where status = 'QUEUED' and job_type = 'INGESTION'
                     order by queued_at
                     limit 1
                       for update skip locked
                   )
            returning {_RUN_COLUMNS}
            """
        ).fetchone()

    def record_source_metadata(self, run_id: UUID | str, details: dict) -> None:
        """Store what the run asked the provider for and what happened
        (ingestion_runs.source_metadata): sort, language, pages planned vs
        fetched, and why fetching stopped."""
        self._conn.execute(
            "update public.ingestion_runs set source_metadata = %s where id = %s",
            (Jsonb(details), run_id),
        )

    def mark_succeeded(
        self,
        run_id: UUID | str,
        reviews_ingested: int,
        reviews_rejected: int = 0,
        reviews_duplicate: int = 0,
    ) -> dict:
        """Finish a RUNNING run using the result rule in the module docstring:
        COMPLETED, PARTIAL (some skipped) or FAILED (nothing saved)."""
        _check_counts(reviews_ingested, reviews_rejected, reviews_duplicate)
        if reviews_ingested == 0 and reviews_duplicate == 0:
            return self._finish(
                run_id,
                status="FAILED",
                allowed_from=("RUNNING",),
                reviews_ingested=0,
                reviews_rejected=reviews_rejected,
                reviews_duplicate=reviews_duplicate,
                error_code=ErrorCode.NO_REVIEWS_FOUND.value,
            )
        status = "PARTIAL" if reviews_rejected > 0 else "COMPLETED"
        return self._finish(
            run_id,
            status=status,
            allowed_from=("RUNNING",),
            reviews_ingested=reviews_ingested,
            reviews_rejected=reviews_rejected,
            reviews_duplicate=reviews_duplicate,
            error_code=None,
        )

    def mark_partial(
        self,
        run_id: UUID | str,
        reviews_ingested: int,
        reviews_rejected: int,
        reviews_duplicate: int = 0,
    ) -> dict:
        """Finish a RUNNING run where some reviews were saved and some skipped."""
        if reviews_ingested <= 0 or reviews_rejected <= 0:
            raise ValueError("A partial run saves some reviews and skips some.")
        return self.mark_succeeded(
            run_id, reviews_ingested, reviews_rejected, reviews_duplicate
        )

    def mark_failed(
        self, run_id: UUID | str, error_code: ErrorCode, detail: str | None = None
    ) -> dict:
        """Finish a QUEUED or RUNNING run as FAILED.

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
            status="FAILED",
            allowed_from=("QUEUED", "RUNNING"),
            reviews_ingested=0,
            reviews_rejected=0,
            reviews_duplicate=0,
            error_code=error_code.value,
        )

    def requeue(self, run_id: UUID | str) -> dict:
        """Retry a FAILED run: the SAME run goes back to QUEUED.

        Its ingestion_stages rows are kept, so the worker re-runs only the first
        stage that did not complete. Raises ConflictError if the target already
        has another QUEUED or RUNNING run.
        """
        try:
            # A savepoint, as in create_run: a unique-index clash must not abort
            # the caller's whole transaction.
            with self._conn.transaction():
                row = self._conn.execute(
                    f"""
                    update public.ingestion_runs
                       set status = 'QUEUED',
                           queued_at = now(),
                           started_at = null,
                           finished_at = null,
                           error_code = null
                     where id = %s and status = 'FAILED'
                    returning {_RUN_COLUMNS}
                    """,
                    (run_id,),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ConflictError(_ALREADY_ACTIVE) from None
        if row is None:
            raise InvalidRunTransitionError(
                "Only a failed ingestion run can be retried."
            )
        return row

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
             order by queued_at desc
             limit 1
            """,
            (target_id,),
        ).fetchone()

    def _fail_stale_runs(self, target_id: UUID | str) -> None:
        """Fail any QUEUED or RUNNING run that has made no progress for too long."""
        self._conn.execute(
            """
            update public.ingestion_runs
               set status = 'FAILED',
                   finished_at = now(),
                   error_code = %s
             where target_id = %s
               and status in ('QUEUED', 'RUNNING')
               and coalesce(started_at, queued_at) < now() - %s
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
        reviews_duplicate: int,
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
                   reviews_duplicate = %s,
                   error_code = %s
             where id = %s and status::text = any(%s)
            returning {_RUN_COLUMNS}
            """,
            (
                status,
                reviews_ingested,
                reviews_rejected,
                reviews_duplicate,
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


def _check_counts(*counts: int) -> None:
    if min(counts) < 0:
        raise ValueError("Review counts cannot be negative.")
