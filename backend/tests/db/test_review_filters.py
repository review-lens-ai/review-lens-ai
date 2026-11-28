"""S3-016: filtering GET /targets/{id}/reviews by rating and date, from token to
database row.

One target with a review at each star rating, on known dates (and one with no
date), so every test can name exactly which reviews must come back. Covers the
nine cases the story lists: min, max, both, inclusive dates, rating plus date,
clearing, zero results, scoped to the active target, and another user.

Skipped without TEST_DATABASE_URL.
"""

from datetime import UTC, datetime

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"

# (text, rating, reviewed_at). The texts double as names in the assertions.
DATASET = [
    ("one star", 1, datetime(2026, 1, 10, 12, 0, tzinfo=UTC)),
    ("two stars late at night", 2, datetime(2026, 2, 15, 23, 30, tzinfo=UTC)),
    ("three stars at midnight", 3, datetime(2026, 3, 1, 0, 0, tzinfo=UTC)),
    ("four stars", 4, datetime(2026, 3, 20, 9, 0, tzinfo=UTC)),
    ("five stars", 5, datetime(2026, 4, 5, 9, 0, tzinfo=UTC)),
    ("five stars, no date", 5, None),
]


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


def _save(conn, owner_id, name: str, reviews: list[tuple]) -> str:
    """A target with these reviews, saved by a finished run as the worker does."""
    target = TargetRepository(conn).create(owner_id, name, "google_maps", URL)
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
    conn.execute(
        """
        update public.ingestion_runs
           set status = 'COMPLETED', finished_at = now(), reviews_ingested = %s
         where id = %s
        """,
        (len(reviews), run_id),
    )
    return str(target["id"])


@pytest.fixture
def world(conn, make_user, make_token):
    """User A owns the main target and a second one; user B owns their own."""
    owner, other = make_user(), make_user()
    same_day = datetime(2026, 1, 10, tzinfo=UTC)
    return {
        "target": _save(conn, owner, "Joe's", DATASET),
        "second": _save(conn, owner, "Joe's 2", [("second one star", 1, same_day)]),
        "theirs": _save(conn, other, "Bella's", [("user B one star", 1, same_day)]),
        "headers": auth(make_token(str(owner))),
    }


def _get(api, world, query: str = "", *, target: str = "target", limit: int = 100):
    return api.get(
        f"/api/v1/targets/{world[target]}/reviews?limit={limit}{query}",
        headers=world["headers"],
    )


def _texts(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {review["review_text"] for review in response.json()["items"]}


def test_minimum_rating_keeps_that_rating_and_above(api, world):
    r = _get(api, world, "&min_rating=4")

    assert _texts(r) == {"four stars", "five stars", "five stars, no date"}
    assert r.json()["total"] == 3


def test_maximum_rating_keeps_that_rating_and_below(api, world):
    r = _get(api, world, "&max_rating=2")

    assert _texts(r) == {"one star", "two stars late at night"}
    assert r.json()["total"] == 2


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("&min_rating=1&max_rating=2", {"one star", "two stars late at night"}),
        ("&min_rating=4&max_rating=5", {"four stars", "five stars", "five stars, no date"}),
        ("&min_rating=3&max_rating=3", {"three stars at midnight"}),
    ],
)  # fmt: skip
def test_minimum_and_maximum_rating_are_an_inclusive_range(api, world, query, expected):
    assert _texts(_get(api, world, query)) == expected


def test_date_range_includes_the_whole_start_and_end_days(api, world):
    # 23:30 on the start day and 00:00 on the end day are both inside the range.
    r = _get(api, world, "&start_date=2026-02-15&end_date=2026-03-01")

    assert _texts(r) == {"two stars late at night", "three stars at midnight"}


def test_end_date_keeps_reviews_from_late_that_day(api, world):
    r = _get(api, world, "&end_date=2026-02-15")

    assert _texts(r) == {"one star", "two stars late at night"}


def test_dates_are_days_in_the_users_timezone(api, world):
    # 2026-03-01 00:00 UTC is 7 pm on February 28 in New York.
    feb_28 = "&start_date=2026-02-28&end_date=2026-02-28"

    in_new_york = _get(api, world, f"{feb_28}&timezone=America/New_York")
    in_utc = _get(api, world, feb_28)

    assert _texts(in_new_york) == {"three stars at midnight"}
    assert _texts(in_utc) == set()


def test_a_review_without_a_date_never_matches_a_date_filter(api, world):
    texts = _texts(_get(api, world, "&start_date=2000-01-01"))

    assert "five stars, no date" not in texts
    assert len(texts) == 5


def test_rating_and_date_filters_combine_with_and(api, world):
    r = _get(api, world, "&min_rating=3&start_date=2026-03-01")

    # Dated, 3 stars or more, on or after March 1. The undated 5-star review is out.
    assert _texts(r) == {"three stars at midnight", "four stars", "five stars"}
    assert r.json()["total"] == 3


def test_no_filter_returns_the_complete_dataset(api, world):
    _get(api, world, "&min_rating=5")  # a filtered request changes nothing stored

    r = _get(api, world)

    assert _texts(r) == {text for text, _, _ in DATASET}
    assert r.json()["total"] == len(DATASET)


def test_no_match_is_an_empty_page_not_an_error(api, world):
    r = _get(api, world, "&min_rating=5&end_date=2026-01-31")

    assert r.status_code == 200
    assert (r.json()["items"], r.json()["total"]) == ([], 0)


def test_total_and_pages_follow_the_filter(api, world):
    pages = [_get(api, world, f"&min_rating=4&offset={n}", limit=1) for n in range(3)]

    assert [len(p.json()["items"]) for p in pages] == [1, 1, 1]
    assert [p.json()["total"] for p in pages] == [3, 3, 3]
    assert set().union(*(_texts(p) for p in pages)) == {
        "four stars",
        "five stars",
        "five stars, no date",
    }


def test_filters_only_read_the_active_target(api, world):
    # The second target has a 1-star review from the same day; it is not here.
    r = _get(api, world, "&max_rating=1&start_date=2026-01-10&end_date=2026-01-10")

    assert _texts(r) == {"one star"}
    assert {review["target_id"] for review in r.json()["items"]} == {world["target"]}


def test_another_users_target_is_the_same_404_with_a_filter(api, world):
    theirs = _get(api, world, "&max_rating=1", target="theirs")
    unknown = api.get(
        f"/api/v1/targets/{UNKNOWN_ID}/reviews?max_rating=1", headers=world["headers"]
    )

    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json()


def test_around_a_review_that_does_not_match_the_filter_is_a_404(api, world, conn):
    one_star = conn.execute(
        "select id from public.reviews where review_text = 'one star'"
    ).fetchone()["id"]

    r = _get(api, world, f"&min_rating=4&around={one_star}")

    assert r.status_code == 404


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("&min_rating=4&max_rating=2", "max_rating"),
        ("&start_date=2026-03-01&end_date=2026-02-01", "end_date"),
        ("&min_rating=0", "min_rating"),
        ("&max_rating=6", "max_rating"),
        ("&start_date=not-a-date", "start_date"),
        ("&start_date=2026-01-01&timezone=Mars/Olympus_Mons", "timezone"),
    ],
)
def test_an_impossible_filter_is_a_422_naming_the_field(api, world, query, field):
    r = _get(api, world, query)

    assert r.status_code == 422
    assert [d["field"] for d in r.json()["error"]["details"]] == [field]
