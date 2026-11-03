"""S1-022: persist normalized reviews and read them back (table: reviews).

How the ingestion pipeline (S1-019 to S1-023) uses this module:

    result = normalize_pages(pages)
    saved = reviews.bulk_insert(run_id, target_id, owner_id, result.reviews)
    reviews.save_rejections(run_id, target_id, owner_id, result.rejected)
    runs.mark_succeeded(run_id, saved, len(result.rejected))

Rules this module enforces:
- OWNERSHIP: FastAPI bypasses Row Level Security, so every method checks the owner
  through the parent target first. Another user's target is NotFoundError, exactly
  like a missing one, so IDs cannot be probed.
- RUNNING RUNS ONLY: reviews and rejections are saved only while their run is
  RUNNING (InvalidRunTransitionError otherwise), so nothing can be added to a run
  that is queued or already finished. Ownership is checked first, so another
  user's run is a 404 whatever its state.
- DUPLICATES: a review whose source_review_id this target already has is skipped,
  and the first copy wins. Reviews with no source_review_id are always saved.
- COUNTS: bulk_insert returns the rows actually inserted, so a run never reports
  reviews it did not store.
- HISTORY: nothing here updates or deletes reviews, so a failed run cannot remove
  what earlier runs saved.
"""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.errors import NotFoundError
from app.repositories.ingestion_runs import InvalidRunTransitionError
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.normalize_reviews import RejectedRecord


class ReviewRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._targets = TargetRepository(conn)

    def bulk_insert(
        self,
        run_id: UUID | str,
        target_id: UUID | str,
        owner_id: UUID | str,
        reviews: list[ReviewCreate],
    ) -> int:
        """Save a run's reviews, all or nothing. Returns how many were new."""
        self._require_owned_run(run_id, target_id, owner_id)
        saved = 0
        with self._conn.transaction():
            for review in reviews:
                saved += self._conn.execute(
                    """
                    insert into public.reviews (
                        target_id, run_id, rating, review_text, mapping_version,
                        reviewed_at, source_review_id, source_url, source_metadata
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (target_id, source_review_id) do nothing
                    """,
                    (
                        target_id,
                        run_id,
                        review.rating,
                        review.review_text,
                        review.mapping_version,
                        review.reviewed_at,
                        review.source_review_id,
                        review.source_url,
                        Jsonb(review.source_metadata),
                    ),
                ).rowcount
        return saved

    def save_rejections(
        self,
        run_id: UUID | str,
        target_id: UUID | str,
        owner_id: UUID | str,
        rejected: list[RejectedRecord],
    ) -> None:
        """Record the source rows this run skipped and why, all or nothing."""
        self._require_owned_run(run_id, target_id, owner_id)
        with self._conn.transaction():
            for record in rejected:
                self._conn.execute(
                    """
                    insert into public.ingestion_rejections (run_id, reason, raw_record)
                    values (%s, %s, %s)
                    """,
                    (run_id, str(record.reason), Jsonb(record.raw_record)),
                )

    def list_for_target(
        self, target_id: UUID | str, owner_id: UUID | str
    ) -> list[dict]:
        """Reviews of a target the user owns, newest first; otherwise NotFoundError."""
        self._targets.get_owned(target_id, owner_id)
        return self._conn.execute(
            """
            select id, target_id, run_id, rating, review_text, mapping_version,
                   reviewed_at, source_review_id, source_url, source_metadata,
                   created_at
              from public.reviews
             where target_id = %s
             order by reviewed_at desc nulls last, created_at desc, id
            """,
            (target_id,),
        ).fetchall()

    def _require_owned_run(
        self, run_id: UUID | str, target_id: UUID | str, owner_id: UUID | str
    ) -> None:
        """NotFoundError unless the run belongs to this target and the user owns it;
        InvalidRunTransitionError unless the run is RUNNING. The run row stays
        locked until the transaction ends, so it cannot finish mid-save."""
        row = self._conn.execute(
            """
            select r.status::text as status
              from public.ingestion_runs r
              join public.analysis_targets t on t.id = r.target_id
             where r.id = %s and r.target_id = %s and t.owner_id = %s
               for update of r
            """,
            (run_id, target_id, owner_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Target not found.")
        if row["status"] != "RUNNING":
            raise InvalidRunTransitionError(
                "Reviews can only be saved while the ingestion run is running."
            )
