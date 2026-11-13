"""S2-010: the review search column and the shared filter, against real Postgres.

Skipped without TEST_DATABASE_URL.
"""

from app.repositories.review_search import where_clause
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.qa.tools.review_filter import ReviewFilter

URL = "https://www.google.com/maps/place/Joes+Pizza"


def _target_with_reviews(conn, owner_id, texts_and_ratings):
    target = TargetRepository(conn).create(owner_id, "Joe's", "google_maps", URL)
    run_id = conn.execute(
        """
        insert into public.ingestion_runs (target_id, status, started_at)
        values (%s, 'RUNNING', now())
        returning id
        """,
        (target["id"],),
    ).fetchone()["id"]
    ReviewRepository(conn).bulk_insert(
        run_id,
        target["id"],
        owner_id,
        [
            ReviewCreate(rating=rating, review_text=text, mapping_version="serpapi@1")
            for text, rating in texts_and_ratings
        ],
    )
    return target["id"]


def _texts(conn, target_id, flt):
    sql, params = where_clause(target_id, flt)
    rows = conn.execute(
        f"select review_text from public.reviews where {sql} order by review_text",
        params,
    ).fetchall()
    return [row["review_text"] for row in rows]


def test_a_word_search_matches_other_forms_of_the_word(conn, make_user):
    target_id = _target_with_reviews(
        conn,
        make_user(),
        [
            ("The service was slow", 2),
            ("Slowly cooked and great", 5),
            ("Fast and friendly", 5),
        ],
    )

    found = _texts(conn, target_id, ReviewFilter(words="slow"))

    assert found == ["Slowly cooked and great", "The service was slow"]


def test_rating_and_word_conditions_combine(conn, make_user):
    target_id = _target_with_reviews(
        conn, make_user(), [("The service was slow", 2), ("Slow but worth it", 5)]
    )

    found = _texts(conn, target_id, ReviewFilter(words="slow", max_rating=3))

    assert found == ["The service was slow"]


def test_the_filter_never_reaches_another_targets_reviews(conn, make_user):
    mine = _target_with_reviews(conn, make_user(), [("slow service", 2)])
    other = _target_with_reviews(conn, make_user(), [("slow pizza", 2)])

    assert _texts(conn, mine, ReviewFilter(words="slow")) == ["slow service"]
    assert _texts(conn, other, ReviewFilter(words="slow")) == ["slow pizza"]


def test_odd_search_text_does_not_raise(conn, make_user):
    target_id = _target_with_reviews(conn, make_user(), [("great", 5)])

    assert _texts(conn, target_id, ReviewFilter(words='"unclosed & | ! ( ')) == []
