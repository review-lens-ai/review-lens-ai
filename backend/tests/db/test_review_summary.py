"""S2-008: GET /targets/{id}/summary is calculated from the active persisted dataset,
the same saved reviews Q&A reads.

The issue asks for four things: adding or removing a saved review changes the
summary, Target A's summary uses Target A's reviews, Target B's uses Target B's, and
User A cannot read User B's summary. Skipped without TEST_DATABASE_URL.
"""

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

    def _save(target_id, owner_id, ratings, text="Good") -> list[str]:
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
                    rating=rating, review_text=text, mapping_version="serpapi@1"
                )
                for rating in ratings
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
    }


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
    save_run(target_id, owner, [5, 4, 4, 1])
    save_run(target_id, owner, [3, 5])
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


# --- 2 and 3. Each target's summary uses only its own reviews ---


def test_each_target_summary_uses_only_its_own_reviews(
    api, make_user, make_token, make_target, save_run
):
    owner = make_user()
    target_a = make_target(owner, "Target A")
    target_b = make_target(owner, "Target B")
    save_run(target_a, owner, [5, 5, 5])
    save_run(target_b, owner, [1, 2])

    a = get_summary(api, make_token, owner, target_a).json()
    b = get_summary(api, make_token, owner, target_b).json()

    assert a == {
        "review_count": 3,
        "average_rating": 5.0,
        "rating_counts": counts(r5=3),
    }
    assert b == {
        "review_count": 2,
        "average_rating": 1.5,
        "rating_counts": counts(r1=1, r2=1),
    }


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
