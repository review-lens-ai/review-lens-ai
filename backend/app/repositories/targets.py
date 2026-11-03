"""Persistence for AnalysisTarget (table: analysis_targets).

OWNERSHIP RULE: this module is the only place that queries analysis_targets.
FastAPI connects as the `postgres` role, which bypasses Row Level Security, so
every query here filters by owner_id itself. Another user's target is reported
as NotFoundError, never as "forbidden", so IDs cannot be probed.

One exception: get_for_run is for the ingestion worker (app/worker.py), which
works from a run id, not from a request. The run was created by
IngestionRunRepository.create_run, which checked ownership first. Never call
get_for_run with an id that came from a request.
"""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.errors import ConflictError, NotFoundError

# The newest ingestion run's status, or NULL when the target was never ingested.
# "Newest" is the most recently queued run (queued_at; a queued run has no started_at).
_LATEST_STATUS = """
    (select r.status::text
       from public.ingestion_runs r
      where r.target_id = t.id
      order by r.queued_at desc
      limit 1) as ingestion_status
"""

_COLUMNS = f"t.id, t.name, t.platform, t.source_url, t.created_at, t.updated_at, {_LATEST_STATUS}"


class DuplicatePlaceError(ConflictError):
    """The owner already has another target for this place (one target per
    place per owner, analysis_targets_owner_place_key)."""

    message = "You already have a target for this place."


class TargetRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def create(
        self, owner_id: UUID | str, name: str, platform: str, source_url: str
    ) -> dict:
        """Insert a target for `owner_id`, which must come from the verified token."""
        row = self._conn.execute(
            """
            insert into public.analysis_targets (owner_id, name, platform, source_url)
            values (%s, %s, %s, %s)
            returning id
            """,
            (owner_id, name, platform, source_url),
        ).fetchone()
        return self.get_owned(row["id"], owner_id)

    def list_for_owner(self, owner_id: UUID | str) -> list[dict]:
        """Only this owner's targets, newest first."""
        return self._conn.execute(
            f"""
            select {_COLUMNS}
              from public.analysis_targets t
             where t.owner_id = %s
             order by t.created_at desc
            """,
            (owner_id,),
        ).fetchall()

    def get_owned(self, target_id: UUID | str, owner_id: UUID | str) -> dict:
        """The target if `owner_id` owns it; otherwise NotFoundError."""
        row = self._conn.execute(
            f"""
            select {_COLUMNS}
              from public.analysis_targets t
             where t.id = %s and t.owner_id = %s
            """,
            (target_id, owner_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Target not found.")
        return row

    def delete_owned(self, target_id: UUID | str, owner_id: UUID | str) -> None:
        """Delete the target if `owner_id` owns it; otherwise NotFoundError.
        Runs and reviews are removed by their foreign-key cascade."""
        row = self._conn.execute(
            """
            delete from public.analysis_targets
             where id = %s and owner_id = %s
            returning id
            """,
            (target_id, owner_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Target not found.")

    def get_for_run(self, run_id: UUID | str) -> dict:
        """The target an ingestion run belongs to, with its owner. Worker only:
        see the module docstring. NotFoundError if the run does not exist."""
        row = self._conn.execute(
            """
            select t.id, t.owner_id, t.platform, t.source_url, t.source_place_id
              from public.ingestion_runs r
              join public.analysis_targets t on t.id = r.target_id
             where r.id = %s
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("Target not found.")
        return row

    def save_place_id(
        self, target_id: UUID | str, owner_id: UUID | str, place_id: str
    ) -> None:
        """Remember the provider's id for the place (S1-020 resolves it from the
        link), so later runs skip the lookup. DuplicatePlaceError if the owner
        already has another target for the same place."""
        try:
            # A savepoint, so a clash does not abort the caller's transaction.
            with self._conn.transaction():
                row = self._conn.execute(
                    """
                    update public.analysis_targets set source_place_id = %s
                     where id = %s and owner_id = %s
                    returning id
                    """,
                    (place_id, target_id, owner_id),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise DuplicatePlaceError() from None
        if row is None:
            raise NotFoundError("Target not found.")

    def save_place_details(
        self, target_id: UUID | str, owner_id: UUID | str, details: dict
    ) -> None:
        """What the provider says about the place (analysis_targets.source_metadata:
        title, address, category, rating, review count). The user's own `name`
        is never changed."""
        row = self._conn.execute(
            """
            update public.analysis_targets set source_metadata = %s
             where id = %s and owner_id = %s
            returning id
            """,
            (Jsonb(details), target_id, owner_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Target not found.")
