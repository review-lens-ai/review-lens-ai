"""S1-020: the provider's pages for an ingestion run (table: ingestion_raw_pages).

The INGESTION stage stores every page here before anything is normalized, so a
retry can re-run NORMALIZATION without paying the provider again. Pages arrive
already scrubbed of reviewer identity by the SerpApi adapter (see the
document_raw_page_scrubbing migration). Never store request URLs or headers:
they hold the API key.

Worker only (app/worker.py). Ownership was checked when the run was created;
raw pages are reachable only through the run and its target.
"""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.adapters.serpapi import RawPage


class RawPageRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def replace_for_run(self, run_id: UUID | str, pages: list[RawPage]) -> None:
        """Store the run's pages, replacing any from an earlier attempt.

        A retried INGESTION stage fetches from page 1 again, and page numbers
        are unique per run, so the old pages go first.
        """
        self._conn.execute(
            "delete from public.ingestion_raw_pages where run_id = %s", (run_id,)
        )
        for page in pages:
            self._conn.execute(
                """
                insert into public.ingestion_raw_pages
                    (run_id, page_number, payload, next_page_token)
                values (%s, %s, %s, %s)
                """,
                (run_id, page.page_number, Jsonb(page.payload), page.next_page_token),
            )

    def payloads_for_run(self, run_id: UUID | str) -> list[dict]:
        """The stored page payloads in page order: what normalize_pages takes."""
        rows = self._conn.execute(
            """
            select payload
              from public.ingestion_raw_pages
             where run_id = %s
             order by page_number
            """,
            (run_id,),
        ).fetchall()
        return [row["payload"] for row in rows]
