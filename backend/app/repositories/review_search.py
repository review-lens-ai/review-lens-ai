"""Read-only queries behind the Q&A review tools (S2-010).

Nothing here is stored: counts, averages and terms are calculated from the saved
reviews each time, so they always match the data the summary card shows.

SCOPE: a repository is made for one question with for_owner(conn, target_id,
owner_id), which checks ownership in SQL (TargetRepository.get_owned, the rule in
012-cross-user-authorization.md) and binds every query to that one target. So the
owner check runs once per question, cannot be skipped, and the tools never see or
pass an owner or a target. Every query is built from where_clause, so target_id is
always the first condition and is never taken from the filter.
guard.require_in_scope checks the rows again.

MEANING SEARCH: search_by_meaning ranks the target's reviews by how close their
stored embedding is to the embedding of a phrase. The embeddings and the phrase's
numbers stay inside this class and the database; the model only ever receives
review text. It uses the same where_clause, so target_id is still the first
condition and the same filters apply.
"""

from uuid import UUID

import psycopg

from app.repositories.reviews import (
    NEWEST_FIRST,
    _vector_literal,
    aggregate,
    where_clause,
)
from app.repositories.targets import TargetRepository
from app.services.embed_reviews import Embedder
from app.services.qa.tools.review_filter import ReviewFilter

# ORDER BY text is chosen from this fixed list, never built from the model's input.
ORDERS = {
    "newest": NEWEST_FIRST,
    "oldest": "reviewed_at asc nulls last, created_at asc, id",
    "lowest": "rating asc, reviewed_at desc nulls last, id",
    "highest": "rating desc, reviewed_at desc nulls last, id",
    "longest": "length(review_text) desc, id",
}
RELEVANCE = "relevance"

_REVIEW_COLUMNS = "id, target_id, rating, review_text, reviewed_at"


class MeaningSearchUnavailableError(Exception):
    """Search by meaning cannot run now (no embedder, or the model failed)."""


# Only for_owner holds this, so a repository can't be made without the owner check.
_CHECKED = object()


class ReviewSearchRepository:
    """The search queries for one target the caller owns. Make it with for_owner;
    calling the class directly raises TypeError."""

    def __init__(
        self,
        conn: psycopg.Connection,
        target_id: UUID | str,
        *,
        embedder: Embedder | None = None,
        _checked=None,
    ):
        if _checked is not _CHECKED:
            raise TypeError(
                "Make a ReviewSearchRepository with "
                "ReviewSearchRepository.for_owner(conn, target_id, owner_id)."
            )
        self._conn = conn
        self.target_id = target_id
        self._embedder = embedder

    @classmethod
    def for_owner(
        cls,
        conn: psycopg.Connection,
        target_id: UUID | str,
        owner_id: UUID | str,
        embedder: Embedder | None = None,
    ) -> "ReviewSearchRepository":
        """The repository for this target if owner_id owns it; otherwise NotFoundError.
        Without an embedder, search_by_meaning is not available."""
        target = TargetRepository(conn).get_owned(target_id, owner_id)
        return cls(conn, target["id"], embedder=embedder, _checked=_CHECKED)

    def stats(self, review_filter: ReviewFilter) -> dict:
        """Count, average, per-star counts and date range of the matching reviews,
        in the shape reviews.aggregate returns (the summary card's numbers)."""
        where, params = where_clause(self.target_id, review_filter)
        return aggregate(self._conn, where, params)

    def by_month(
        self, review_filter: ReviewFilter, *, months: int = 24
    ) -> tuple[list[dict], bool]:
        """Reviews and average rating per month, oldest first, for the latest
        `months` months that have reviews. The bool is True when older months were
        left out, so the tool can say so."""
        where, params = where_clause(self.target_id, review_filter)
        rows = self._conn.execute(
            f"""
            select date_trunc('month', reviewed_at) as month,
                   count(*) as total,
                   round(avg(rating), 2)::float8 as average
              from public.reviews
             where {where} and reviewed_at is not null
             group by 1
             order by 1 desc
             limit %s
            """,
            [*params, months + 1],
        ).fetchall()
        cut = len(rows) > months
        return list(reversed(rows[:months])), cut

    def top_terms(self, review_filter: ReviewFilter, *, limit: int = 10) -> list[dict]:
        """The words used in the most reviews. Forms of one word count together
        ("service", "services") and are shown as the form used most often."""
        where, params = where_clause(self.target_id, review_filter)
        return self._conn.execute(
            f"""
            with words as (
                select id, lower(w) as word
                  from public.reviews,
                       regexp_split_to_table(review_text, '[^[:alpha:]]+') as w
                 where {where} and length(w) > 2
            ),
            stems as (
                select d.word,
                       (select s.lexeme
                          from unnest(to_tsvector('english', d.word)) as s
                         limit 1) as stem
                  from (select distinct word from words) d
            )
            select mode() within group (order by words.word) as term,
                   count(distinct words.id) as reviews
              from words
              join stems on stems.word = words.word
             where stems.stem is not null and length(stems.stem) > 2
             group by stems.stem
             order by reviews desc, term
             limit %s
            """,
            [*params, limit],
        ).fetchall()

    def search(
        self,
        review_filter: ReviewFilter,
        *,
        order: str,
        limit: int = 5,
    ) -> list[dict]:
        """Matching reviews. order is "relevance" (needs keywords) or a key of
        ORDERS; anything else is a ValueError, never a silent fallback."""
        where, params = where_clause(self.target_id, review_filter)
        if order == RELEVANCE:
            if review_filter.keywords is None:
                raise ValueError("relevance order needs keywords")
            order_by = (
                "ts_rank_cd(search_vector, websearch_to_tsquery('english', %s)) desc,"
                " reviewed_at desc nulls last, id"
            )
            params = [*params, review_filter.keywords]
        elif order in ORDERS:
            order_by = ORDERS[order]
        else:
            raise ValueError(f"unknown order {order!r}")
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

    def search_by_meaning(
        self, phrase: str, review_filter: ReviewFilter, *, limit: int = 5
    ) -> list[dict]:
        """The reviews whose meaning is closest to `phrase`, closest first. Reviews
        without an embedding yet are left out. Raises MeaningSearchUnavailableError when
        there is no embedder or it fails, so the tool can say so and fall back."""
        if self._embedder is None:
            raise MeaningSearchUnavailableError("no embedder was given")
        try:
            vector = self._embedder.embed([phrase])[0]
        except Exception as error:
            raise MeaningSearchUnavailableError(str(error)) from error
        where, params = where_clause(self.target_id, review_filter)
        return self._conn.execute(
            f"""
            select {_REVIEW_COLUMNS}
              from public.reviews
             where {where} and embedding is not null
             order by embedding <=> %s::vector, id
             limit %s
            """,
            [*params, _vector_literal(vector), limit],
        ).fetchall()

    def get_many(self, review_ids: list[UUID]) -> list[dict]:
        """These reviews of the target, if they are in it; others are left out."""
        return self._conn.execute(
            f"""
            select {_REVIEW_COLUMNS}
              from public.reviews
             where target_id = %s and id = any(%s)
            """,
            (self.target_id, review_ids),
        ).fetchall()
