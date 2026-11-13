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
    return ReviewSearchRepository(conn), owner, target_id


def test_stats_count_average_and_stars(seeded):
    repo, owner, target_id = seeded

    stats = repo.stats(target_id, owner, ReviewFilter())

    assert stats["total"] == 4
    assert stats["average"] == pytest.approx(3.75)
    assert stats["per_star"] == {1: 1, 2: 0, 3: 0, 4: 1, 5: 2}
    assert stats["first_reviewed_at"] == _day(1)
    assert stats["last_reviewed_at"] == _day(3)


def test_stats_of_nothing_is_zero_with_no_average(seeded):
    repo, owner, target_id = seeded

    stats = repo.stats(target_id, owner, ReviewFilter(words="parking"))

    assert stats["total"] == 0
    assert stats["average"] is None


def test_the_date_range_splits_reviews_into_before_and_after(seeded):
    repo, owner, target_id = seeded
    split = _day(2, 1)

    before = repo.stats(target_id, owner, ReviewFilter(until=split))
    after = repo.stats(target_id, owner, ReviewFilter(since=split))

    assert (before["total"], after["total"]) == (
        1,
        2,
    )  # the undated review is in neither


def test_by_month_is_oldest_first(seeded):
    repo, owner, target_id = seeded

    months = repo.by_month(target_id, owner, ReviewFilter())

    assert [m["month"].month for m in months] == [1, 2, 3]
    assert [m["total"] for m in months] == [1, 1, 1]


def test_top_terms_count_reviews_not_repeats(seeded):
    repo, owner, target_id = seeded

    terms = {
        t["term"]: t["reviews"]
        for t in repo.top_terms(target_id, owner, ReviewFilter())
    }

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
    repo, owner, target_id = seeded

    rows = repo.search(target_id, owner, ReviewFilter(), order=order, limit=4)

    assert rows[0]["review_text"] == first


def test_search_by_relevance_puts_the_best_match_first(seeded):
    repo, owner, target_id = seeded

    rows = repo.search(
        target_id, owner, ReviewFilter(words="slow cold"), order="relevance"
    )

    assert rows[0]["review_text"] == "Slow service and cold pizza"


def test_get_many_leaves_out_reviews_of_other_targets(conn, make_user, seeded):
    repo, owner, target_id = seeded
    other_target = _target(conn, make_user(), [("Slow pizza", 2, _day(1))])
    mine = repo.search(target_id, owner, ReviewFilter(), limit=1)[0]["id"]
    theirs = conn.execute(
        "select id from public.reviews where target_id = %s", (other_target,)
    ).fetchone()["id"]

    rows = repo.get_many(target_id, owner, [mine, theirs])

    assert [row["id"] for row in rows] == [mine]


def test_another_users_target_is_not_found_for_every_query(conn, make_user, seeded):
    repo, _, target_id = seeded
    stranger = make_user()

    for call in (
        lambda: repo.stats(target_id, stranger, ReviewFilter()),
        lambda: repo.by_month(target_id, stranger, ReviewFilter()),
        lambda: repo.top_terms(target_id, stranger, ReviewFilter()),
        lambda: repo.search(target_id, stranger, ReviewFilter()),
        lambda: repo.get_many(target_id, stranger, []),
    ):
        with pytest.raises(NotFoundError):
            call()
