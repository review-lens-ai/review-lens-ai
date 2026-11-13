"""Read-only queries behind the Q&A review tools (S2-010).

Nothing here is stored: counts, averages and terms are calculated from the saved
reviews each time, so they always match the data the summary card shows.

SCOPE: every query is built from where_clause, so target_id is always the first
condition and is never taken from the filter. Each method also checks that the user
owns the target (TargetRepository.get_owned), like ReviewRepository: another user's
target is NotFoundError.
"""

from uuid import UUID

import psycopg

from app.repositories.targets import TargetRepository
from app.services.qa.tools.review_filter import ReviewFilter

# ORDER BY text is chosen from this fixed list, never built from the model's input.
ORDERS = {
    "newest": "reviewed_at desc nulls last, created_at desc, id",
    "oldest": "reviewed_at asc nulls last, created_at asc, id",
    "lowest": "rating asc, reviewed_at desc nulls last, id",
    "highest": "rating desc, reviewed_at desc nulls last, id",
    "longest": "length(review_text) desc, id",
}
RELEVANCE = "relevance"

_REVIEW_COLUMNS = "id, target_id, rating, review_text, reviewed_at"


def where_clause(target_id: UUID | str, flt: ReviewFilter) -> tuple[str, list]:
    """SQL after "where", and its parameters, for one target and one filter."""
    conditions = ["target_id = %s"]
    params: list = [target_id]
    if flt.words is not None:
        # websearch_to_tsquery accepts any text: it never raises on odd input.
        conditions.append("search_vector @@ websearch_to_tsquery('english', %s)")
        params.append(flt.words)
    if flt.min_rating is not None:
        conditions.append("rating >= %s")
        params.append(flt.min_rating)
    if flt.max_rating is not None:
        conditions.append("rating <= %s")
        params.append(flt.max_rating)
    if flt.since is not None:
        conditions.append("reviewed_at >= %s")
        params.append(flt.since)
    if flt.until is not None:
        conditions.append("reviewed_at < %s")
        params.append(flt.until)
    return " and ".join(conditions), params


class ReviewSearchRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._targets = TargetRepository(conn)

    def stats(
        self, target_id: UUID | str, owner_id: UUID | str, flt: ReviewFilter
    ) -> dict:
        """Count, average, how many reviews per star and the review dates."""
        self._targets.get_owned(target_id, owner_id)
        where, params = where_clause(target_id, flt)
        row = self._conn.execute(
            f"""
            select count(*) as total,
                   avg(rating)::float8 as average,
                   count(*) filter (where rating = 1) as star_1,
                   count(*) filter (where rating = 2) as star_2,
                   count(*) filter (where rating = 3) as star_3,
                   count(*) filter (where rating = 4) as star_4,
                   count(*) filter (where rating = 5) as star_5,
                   min(reviewed_at) as first_reviewed_at,
                   max(reviewed_at) as last_reviewed_at
              from public.reviews
             where {where}
            """,
            params,
        ).fetchone()
        return {
            "total": row["total"],
            "average": row["average"],
            "per_star": {n: row[f"star_{n}"] for n in range(1, 6)},
            "first_reviewed_at": row["first_reviewed_at"],
            "last_reviewed_at": row["last_reviewed_at"],
        }

    def by_month(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        flt: ReviewFilter,
        *,
        months: int = 24,
    ) -> list[dict]:
        """Reviews and average rating per month, oldest first (the latest `months`)."""
        self._targets.get_owned(target_id, owner_id)
        where, params = where_clause(target_id, flt)
        rows = self._conn.execute(
            f"""
            select date_trunc('month', reviewed_at) as month,
                   count(*) as total,
                   avg(rating)::float8 as average
              from public.reviews
             where {where} and reviewed_at is not null
             group by 1
             order by 1 desc
             limit %s
            """,
            [*params, months],
        ).fetchall()
        return list(reversed(rows))

    def top_terms(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        flt: ReviewFilter,
        *,
        limit: int = 10,
    ) -> list[dict]:
        """The words (reduced to their stem) used in the most reviews."""
        self._targets.get_owned(target_id, owner_id)
        where, params = where_clause(target_id, flt)
        return self._conn.execute(
            f"""
            select u.lexeme as term, count(*) as reviews
              from public.reviews,
                   unnest(search_vector) as u(lexeme, positions, weights)
             where {where} and length(u.lexeme) > 2
             group by u.lexeme
             order by reviews desc, term
             limit %s
            """,
            [*params, limit],
        ).fetchall()

    def search(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        flt: ReviewFilter,
        *,
        order: str = RELEVANCE,
        limit: int = 5,
    ) -> list[dict]:
        """Matching reviews. order is "relevance" (needs flt.words) or a key of ORDERS."""
        self._targets.get_owned(target_id, owner_id)
        where, params = where_clause(target_id, flt)
        if order == RELEVANCE and flt.words is not None:
            order_by = (
                "ts_rank_cd(search_vector, websearch_to_tsquery('english', %s)) desc,"
                " reviewed_at desc nulls last, id"
            )
            params = [*params, flt.words]
        else:
            order_by = ORDERS.get(order, ORDERS["newest"])
        return self._conn.execute(
            f"""
            select {_REVIEW_COLUMNS}
              from public.reviews
             where {where}
             order by {order_by}
             limit %s
            """,
            [*params, limit],
        ).fetchall()

    def get_many(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        review_ids: list[UUID],
    ) -> list[dict]:
        """These reviews of the target, if they are in it; others are left out."""
        self._targets.get_owned(target_id, owner_id)
        return self._conn.execute(
            f"""
            select {_REVIEW_COLUMNS}
              from public.reviews
             where target_id = %s and id = any(%s)
            """,
            (target_id, review_ids),
        ).fetchall()
