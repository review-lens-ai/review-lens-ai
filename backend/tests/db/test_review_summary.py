"""S2-008: GET /targets/{id}/summary is calculated from the active persisted dataset,
the same saved reviews Q&A reads.

The issue asks for four things: adding or removing a saved review changes the
summary, Target A's summary uses Target A's reviews, Target B's uses Target B's, and
User A cannot read User B's summary. Skipped without TEST_DATABASE_URL.
"""

from datetime import UTC, datetime

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from tests.conftest import auth
from tests.db.conftest import insert_run

URL = "https://www.google.com/maps/place/Joes+Pizza"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def make_target(conn):
    def _make(owner_id, name="Joe's") -> str:
        target = TargetRepository(conn).create(owner_id, name, "google_maps", URL)
        return str(target["id"])

    return _make


@pytest.fixture
def save_run(conn):
    """Save one finished run's reviews the way the worker does: inserted while the
    run is RUNNING, then the run is marked COMPLETED. Returns the saved review ids."""

    def _save(target_id, owner_id, ratings, text="Good", dates=None) -> list[str]:
        # dates: one review date per rating (None for an undated review). No dates
        # by default, so those tests see first/last_reviewed_at as None.
        dates = dates or [None] * len(ratings)
        run_id = conn.execute(
            """
            insert into public.ingestion_runs (target_id, status, started_at)
            values (%s, 'RUNNING', now())
            returning id
            """,
            (target_id,),
        ).fetchone()["id"]
        ReviewRepository(conn).bulk_insert(
            run_id,
            target_id,
            owner_id,
            [
                ReviewCreate(
                    rating=rating,
                    review_text=text,
                    mapping_version="serpapi@1",
                    reviewed_at=reviewed_at,
                )
                for rating, reviewed_at in zip(ratings, dates, strict=True)
            ],
        )
        conn.execute(
            """
            update public.ingestion_runs
               set status = 'COMPLETED', finished_at = now(), reviews_ingested = %s
             where id = %s
            """,
            (len(ratings), run_id),
        )
        return [
            str(row["id"])
            for row in conn.execute(
                "select id from public.reviews where run_id = %s", (run_id,)
            ).fetchall()
        ]

    return _save


# The date range of reviews saved without a date (save_run's default).
NO_DATES = {"first_reviewed_at": None, "last_reviewed_at": None}


def day(text: str) -> datetime:
    """A review date at midnight UTC, e.g. day("2024-03-02")."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def date_range(body: dict) -> tuple[datetime | None, datetime | None]:
    """first/last_reviewed_at from a summary response, parsed back to datetimes, so
    the check doesn't depend on the database session's time zone."""
    return tuple(
        None if body[key] is None else datetime.fromisoformat(body[key])
        for key in ("first_reviewed_at", "last_reviewed_at")
    )


def counts(**by_rating) -> list[dict]:
    """rating_counts as the endpoint returns it, e.g. counts(r5=2, r1=1)."""
    return [
        {"rating": rating, "count": by_rating.get(f"r{rating}", 0)}
        for rating in range(1, 6)
    ]


def get_summary(api, make_token, owner_id, target_id):
    return api.get(
        f"/api/v1/targets/{target_id}/summary", headers=auth(make_token(str(owner_id)))
    )


# --- What the summary contains (a known fixture, exact values) ---


def test_summary_of_a_known_set_of_reviews(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    save_run(target_id, owner, [5, 5, 4, 2, 1])

    r = get_summary(api, make_token, owner, target_id)

    assert r.status_code == 200
    assert r.json() == {
        "review_count": 5,
        "average_rating": 3.4,
        "rating_counts": counts(r1=1, r2=1, r4=1, r5=2),
        **NO_DATES,
    }


def test_average_is_rounded_to_two_decimal_places(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    save_run(target_id, owner, [5, 4, 4])

    assert (
        get_summary(api, make_token, owner, target_id).json()["average_rating"] == 4.33
    )


def test_target_with_no_reviews_has_a_zero_count_and_no_average(
    api, make_user, make_token, make_target
):
    owner = make_user()
    target_id = make_target(owner)

    r = get_summary(api, make_token, owner, target_id)

    assert r.status_code == 200
    assert r.json() == {
        "review_count": 0,
        "average_rating": None,
        "rating_counts": counts(),
        **NO_DATES,
    }


def test_the_rating_counts_always_add_up_to_the_review_count(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    # Two runs and every star rating, so a bucket missing from the query would show.
    save_run(target_id, owner, [1, 2, 3, 4, 5, 5])
    save_run(target_id, owner, [1, 3, 3, 5], text="Another run")

    body = get_summary(api, make_token, owner, target_id).json()

    assert body["review_count"] == 10
    assert [c["rating"] for c in body["rating_counts"]] == [1, 2, 3, 4, 5]
    assert sum(c["count"] for c in body["rating_counts"]) == body["review_count"]


def test_summary_gives_the_oldest_and_newest_review_date(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    # Saved out of order, across two runs, so the range can't come from save order.
    save_run(target_id, owner, [4, 5], dates=[day("2025-07-19"), day("2024-03-02")])
    save_run(target_id, owner, [2], dates=[day("2026-11-08")])

    body = get_summary(api, make_token, owner, target_id).json()

    assert date_range(body) == (day("2024-03-02"), day("2026-11-08"))


def test_reviews_without_a_date_are_left_out_of_the_date_range(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    save_run(target_id, owner, [5, 3, 1], dates=[None, day("2025-01-10"), None])

    body = get_summary(api, make_token, owner, target_id).json()

    # The undated reviews still count; they just have no date to add to the range.
    assert body["review_count"] == 3
    assert date_range(body) == (day("2025-01-10"), day("2025-01-10"))


# --- 1. Adding or removing a saved review changes the summary ---


def test_reviews_saved_by_a_later_run_are_added_to_the_summary(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    save_run(target_id, owner, [5, 5])
    before = get_summary(api, make_token, owner, target_id).json()

    save_run(target_id, owner, [2])
    after = get_summary(api, make_token, owner, target_id).json()

    assert before["review_count"] == 2
    # The dataset is every run's reviews, not only the latest run's.
    assert after == {
        "review_count": 3,
        "average_rating": 4.0,
        "rating_counts": counts(r2=1, r5=2),
        **NO_DATES,
    }


def test_removing_a_saved_review_changes_the_summary(
    api, conn, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    review_ids = save_run(target_id, owner, [5, 1])
    one_star = conn.execute(
        "select id from public.reviews where id = any(%s) and rating = 1",
        (review_ids,),
    ).fetchone()["id"]

    # Nothing in the app deletes single reviews; the test removes the row directly.
    conn.execute("delete from public.reviews where id = %s", (one_star,))

    assert get_summary(api, make_token, owner, target_id).json() == {
        "review_count": 1,
        "average_rating": 5.0,
        "rating_counts": counts(r5=1),
        **NO_DATES,
    }


def test_a_failed_later_run_leaves_the_summary_unchanged(
    api, conn, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    save_run(target_id, owner, [4, 2])
    before = get_summary(api, make_token, owner, target_id).json()

    insert_run(conn, target_id, status="FAILED")

    assert get_summary(api, make_token, owner, target_id).json() == before


def test_summary_comes_from_saved_reviews_not_a_runs_counters(
    api, conn, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    save_run(target_id, owner, [3])
    # A run's counter describes that run only; the summary must not read it.
    conn.execute(
        "update public.ingestion_runs set reviews_ingested = 99 where target_id = %s",
        (target_id,),
    )

    assert get_summary(api, make_token, owner, target_id).json()["review_count"] == 1


# --- The summary describes the dataset Q&A will read ---


def test_summary_matches_the_reviews_the_q_and_a_loader_reads(
    conn, make_user, make_target, save_run
):
    owner = make_user()
    target_id = make_target(owner)
    save_run(
        target_id,
        owner,
        [5, 4, 4, 1],
        dates=[day("2025-05-01"), None, day("2024-09-12"), day("2025-02-20")],
    )
    save_run(target_id, owner, [3, 5], dates=[day("2026-01-03"), None])
    reviews = ReviewRepository(conn)

    summary = reviews.summary_for_target(target_id, owner)
    # Every review of the target, as the Q&A context will load them.
    dataset = reviews.list_for_target(target_id, owner, limit=None)

    ratings = [review["rating"] for review in dataset]
    assert summary["review_count"] == len(dataset)
    assert summary["average_rating"] == round(sum(ratings) / len(ratings), 2)
    assert summary["rating_counts"] == [
        {"rating": rating, "count": ratings.count(rating)} for rating in range(1, 6)
    ]
    dates = [review["reviewed_at"] for review in dataset if review["reviewed_at"]]
    assert summary["first_reviewed_at"] == min(dates)
    assert summary["last_reviewed_at"] == max(dates)


# --- 2 and 3. Each target's summary uses only its own reviews ---


def test_each_target_summary_uses_only_its_own_reviews(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_a = make_target(owner, "Target A")
    target_b = make_target(owner, "Target B")
    save_run(
        target_a,
        owner,
        [5, 5, 5],
        dates=[day("2024-01-05"), day("2024-06-01"), day("2024-12-31")],
    )
    save_run(target_b, owner, [1, 2], dates=[day("2026-02-10"), day("2026-03-15")])

    a = get_summary(api, make_token, owner, target_a).json()
    b = get_summary(api, make_token, owner, target_b).json()

    assert {k: a[k] for k in ("review_count", "average_rating", "rating_counts")} == {
        "review_count": 3,
        "average_rating": 5.0,
        "rating_counts": counts(r5=3),
    }
    assert {k: b[k] for k in ("review_count", "average_rating", "rating_counts")} == {
        "review_count": 2,
        "average_rating": 1.5,
        "rating_counts": counts(r1=1, r2=1),
    }
    # Each target's date range comes from its own reviews only.
    assert date_range(a) == (day("2024-01-05"), day("2024-12-31"))
    assert date_range(b) == (day("2026-02-10"), day("2026-03-15"))


# --- 4. A user cannot read another user's summary ---


def test_another_users_summary_is_the_same_404_as_a_missing_target(
    api, make_user, make_token, make_target, save_run
):
    user_a = make_user()
    user_b = make_user()
    target_b = make_target(user_b)
    save_run(target_b, user_b, [5, 4])

    denied = get_summary(api, make_token, user_a, target_b)
    unknown = get_summary(api, make_token, user_a, UNKNOWN_ID)

    assert denied.status_code == 404
    # Nothing about User B's reviews leaks, and the answer can't be told apart
    # from a target that does not exist.
    assert denied.json() == unknown.json()
    assert "review_count" not in denied.text


def test_summary_requires_a_signed_in_user(api, make_user, make_target):
    target_id = make_target(make_user())

    assert api.get(f"/api/v1/targets/{target_id}/summary").status_code == 401
