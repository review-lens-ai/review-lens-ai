"""S2-010: the SQL behind the Q&A tools, against real Postgres.

Skipped without TEST_DATABASE_URL. The tools' wording is tested without a database
in tests/unit/qa_tools; this file checks that the queries return the right rows.
"""

from datetime import UTC, datetime

import pytest

from app.core.errors import NotFoundError
from app.repositories.review_search import ReviewSearchRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.qa.tools.review_filter import ReviewFilter

URL = "https://www.google.com/maps/place/Joes+Pizza"


def _target(conn, owner_id, reviews):
    """reviews: (text, rating, reviewed_at or None)."""
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
            ReviewCreate(
                rating=rating,
                review_text=text,
                mapping_version="serpapi@1",
                reviewed_at=reviewed_at,
            )
            for text, rating, reviewed_at in reviews
        ],
    )
    return target["id"]


def _day(month, day=10):
    return datetime(2026, month, day, tzinfo=UTC)


@pytest.fixture
def seeded(conn, make_user):
    owner = make_user()
    target_id = _target(
        conn,
        owner,
        [
            ("Slow service and cold pizza", 1, _day(1)),
            ("Slow but the pizza was great", 4, _day(2)),
            ("Friendly staff, great pizza", 5, _day(3)),
            ("Great pizza, great price", 5, None),
        ],
    )
    return ReviewSearchRepository.for_owner(conn, target_id, owner), owner, target_id


def test_stats_count_average_and_stars(seeded):
    repo, _, target_id = seeded

    stats = repo.stats(ReviewFilter())

    assert stats["review_count"] == 4
    assert stats["average_rating"] == pytest.approx(3.75)
    counts = {item["rating"]: item["count"] for item in stats["rating_counts"]}
    assert counts == {1: 1, 2: 0, 3: 0, 4: 1, 5: 2}
    assert stats["first_reviewed_at"] == _day(1)
    assert stats["last_reviewed_at"] == _day(3)


def test_stats_with_no_filter_is_exactly_the_summary_card(conn, seeded):
    repo, owner, target_id = seeded

    tool_numbers = repo.stats(ReviewFilter())
    card_numbers = ReviewRepository(conn).summary_for_target(target_id, owner)

    assert tool_numbers == card_numbers


def test_stats_of_nothing_is_zero_with_no_average(seeded):
    repo, _, target_id = seeded

    stats = repo.stats(ReviewFilter(keywords="parking"))

    assert stats["review_count"] == 0
    assert stats["average_rating"] is None


def test_the_date_range_splits_reviews_into_before_and_after(seeded):
    repo, _, target_id = seeded
    split = _day(2, 1)

    before = repo.stats(ReviewFilter(until=split))
    after = repo.stats(ReviewFilter(since=split))

    # The undated review is in neither.
    assert (before["review_count"], after["review_count"]) == (1, 2)


def test_by_month_is_oldest_first(seeded):
    repo, _, target_id = seeded

    months, cut = repo.by_month(ReviewFilter())

    assert [m["month"].month for m in months] == [1, 2, 3]
    assert [m["total"] for m in months] == [1, 1, 1]
    assert cut is False


def test_by_month_keeps_the_latest_months_and_says_it_cut(seeded):
    repo, _, target_id = seeded

    months, cut = repo.by_month(ReviewFilter(), months=2)

    assert [m["month"].month for m in months] == [2, 3]
    assert cut is True


def test_top_terms_count_reviews_not_repeats(seeded):
    repo, _, target_id = seeded

    terms = {t["term"]: t["reviews"] for t in repo.top_terms(ReviewFilter())}

    assert terms["pizza"] == 4
    assert terms["great"] == 3  # "Great pizza, great price" counts once


@pytest.mark.parametrize(
    "order, first",
    [
        ("newest", "Friendly staff, great pizza"),
        ("oldest", "Slow service and cold pizza"),
        ("lowest", "Slow service and cold pizza"),
        ("longest", "Slow but the pizza was great"),
    ],
)
def test_search_orders(seeded, order, first):
    repo, _, target_id = seeded

    rows = repo.search(ReviewFilter(), order=order, limit=4)

    assert rows[0]["review_text"] == first


def test_search_by_relevance_puts_the_best_match_first(seeded):
    repo, _, target_id = seeded

    rows = repo.search(ReviewFilter(keywords="slow cold"), order="relevance")

    assert rows[0]["review_text"] == "Slow service and cold pizza"


@pytest.mark.parametrize(
    "review_filter, order",
    [(ReviewFilter(), "relevance"), (ReviewFilter(keywords="slow"), "random")],
)
def test_search_refuses_an_order_it_cannot_honour(seeded, review_filter, order):
    repo, _, target_id = seeded

    with pytest.raises(ValueError):
        repo.search(review_filter, order=order)


def test_get_many_leaves_out_reviews_of_other_targets(conn, make_user, seeded):
    repo, _, target_id = seeded
    other_target = _target(conn, make_user(), [("Slow pizza", 2, _day(1))])
    mine = repo.search(ReviewFilter(), order="newest", limit=1)[0]["id"]
    theirs = conn.execute(
        "select id from public.reviews where target_id = %s", (other_target,)
    ).fetchone()["id"]

    rows = repo.get_many([mine, theirs])

    assert [row["id"] for row in rows] == [mine]


def test_another_users_target_gets_no_repository(conn, make_user, seeded):
    _, _, target_id = seeded

    with pytest.raises(NotFoundError):
        ReviewSearchRepository.for_owner(conn, target_id, make_user())
