"""Persistence for AnalysisTarget (table: analysis_targets).

OWNERSHIP RULE: this module is the only place that queries analysis_targets.
FastAPI connects as the `postgres` role, which bypasses Row Level Security, so
every query here filters by owner_id itself. Another user's target is reported
as NotFoundError, never as "forbidden", so IDs cannot be probed.
"""

from uuid import UUID

import psycopg

from app.core.errors import NotFoundError

# The newest ingestion run's status, or NULL when the target was never ingested.
_LATEST_STATUS = """
    (select r.status::text
       from public.ingestion_runs r
      where r.target_id = t.id
      order by r.started_at desc
      limit 1) as ingestion_status
"""

_COLUMNS = f"t.id, t.name, t.platform, t.source_url, t.created_at, t.updated_at, {_LATEST_STATUS}"


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
