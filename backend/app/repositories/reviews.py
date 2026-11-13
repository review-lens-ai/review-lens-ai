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
- ACTIVE DATASET (S2-008): a target's dataset is every review saved for it, from
  every run (`where target_id = %s`). The reviews list, the summary and the Q&A
  context all read that same set, so the numbers on the Summary tab describe exactly
  the reviews Q&A will use. Never derive them from a run's counters (one run only),
  the raw provider pages, the browser, or the model.
"""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.errors import NotFoundError
from app.repositories.ingestion_runs import InvalidRunTransitionError
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.normalize_reviews import RejectedRecord

# The order of a target's reviews: newest first. The list and page_offset_of both use
# it, so a link to a review opens on the page the list would show it on.
# id last: pages never overlap or skip a review.
NEWEST_FIRST = "reviewed_at desc nulls last, created_at desc, id"


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
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Reviews of a target the user owns, newest first; otherwise NotFoundError.
        limit=None returns them all."""
        self._targets.get_owned(target_id, owner_id)
        return self._conn.execute(
            f"""
            select id, target_id, run_id, rating, review_text, mapping_version,
                   reviewed_at, source_review_id, source_url, source_metadata,
                   created_at
              from public.reviews
             where target_id = %s
             order by {NEWEST_FIRST}
             limit %s offset %s
            """,
            (target_id, limit, offset),
        ).fetchall()

    def page_offset_of(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        review_id: UUID | str,
        *,
        limit: int,
    ) -> int:
        """Offset of the page that holds one review, in the same order as
        list_for_target. NotFoundError if the target is not the user's or the
        review is not in it (the two look the same to the caller)."""
        self._targets.get_owned(target_id, owner_id)
        row = self._conn.execute(
            f"""
            select position from (
              select id,
                     row_number() over (order by {NEWEST_FIRST}) - 1 as position
                from public.reviews
               where target_id = %s
            ) ranked
             where id = %s
            """,
            (target_id, review_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("Review not found.")
        return row["position"] // limit * limit

    def count_for_target(self, target_id: UUID | str, owner_id: UUID | str) -> int:
        """How many reviews a target the user owns has; otherwise NotFoundError."""
        self._targets.get_owned(target_id, owner_id)
        return self._conn.execute(
            "select count(*) as total from public.reviews where target_id = %s",
            (target_id,),
        ).fetchone()["total"]

    def summary_for_target(self, target_id: UUID | str, owner_id: UUID | str) -> dict:
        """S2-008: the summary of a target's active dataset, calculated in one query
        from the saved reviews; otherwise NotFoundError.

        Returns {"review_count": int, "average_rating": float | None,
        "rating_counts": [{"rating": 1, "count": n}, ... up to 5],
        "first_reviewed_at": datetime | None, "last_reviewed_at": datetime | None}.
        Every rating from 1 to 5 is listed, with 0 when no review has it.
        average_rating is None when there are no reviews, and is rounded to 2 decimal
        places. The dates are the oldest and newest review dates; reviews without a
        date are left out of them, and both are None when no review has a date.
        """
        self._targets.get_owned(target_id, owner_id)
        row = self._conn.execute(
            """
            select count(*) as review_count,
                   round(avg(rating), 2) as average_rating,
                   count(*) filter (where rating = 1) as r1,
                   count(*) filter (where rating = 2) as r2,
                   count(*) filter (where rating = 3) as r3,
                   count(*) filter (where rating = 4) as r4,
                   count(*) filter (where rating = 5) as r5,
                   min(reviewed_at) as first_reviewed_at,
                   max(reviewed_at) as last_reviewed_at
              from public.reviews
             where target_id = %s
            """,
            (target_id,),
        ).fetchone()
        average = row["average_rating"]
        return {
            "review_count": row["review_count"],
            "average_rating": None if average is None else float(average),
            "rating_counts": [
                {"rating": rating, "count": row[f"r{rating}"]} for rating in range(1, 6)
            ],
            "first_reviewed_at": row["first_reviewed_at"],
            "last_reviewed_at": row["last_reviewed_at"],
        }

    def without_embedding(
        self, *, limit: int, target_id: UUID | str | None = None
    ) -> list[dict]:
        """Up to `limit` reviews with no embedding yet ({id, review_text}), of one
        target or of every target. Only the ingestion worker and the backfill call
        this; neither acts for a user, so there is no owner check (like
        TargetRepository.get_for_run)."""
        return self._conn.execute(
            """
            select id, review_text
              from public.reviews
             where embedding is null
               and (%(target_id)s::uuid is null or target_id = %(target_id)s::uuid)
             order by id
             limit %(limit)s
            """,
            {"target_id": target_id, "limit": limit},
        ).fetchall()

    def save_embeddings(self, embeddings: list[tuple[UUID, list[float]]]) -> None:
        """Store each review's embedding. A review that already has one is left
        as it is, so saving twice changes nothing."""
        with self._conn.cursor() as cur:
            cur.executemany(
                """
                update public.reviews
                   set embedding = %s::vector
                 where id = %s and embedding is null
                """,
                [
                    (_vector_literal(vector), review_id)
                    for review_id, vector in embeddings
                ],
            )

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


def _vector_literal(vector: list[float]) -> str:
    """pgvector's text form, "[0.1,0.2,...]", so no extra driver package is needed."""
    return "[" + ",".join(str(float(value)) for value in vector) + "]"
