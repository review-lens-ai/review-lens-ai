"""S3-009: refreshing a target against a real Postgres and the real review store.

SerpApi is faked. The place has three pages, newest first:
    page 1   10 reviews (5 rating-only)
    page 2   20 reviews (7 rating-only)      the first run stops here (cap of 30)
    page 3    5 older reviews with text      the place's last page
So the first run saves 18 and rejects 12; a refresh finds nothing new on page 1,
jumps to page 3 with the saved cursor, and adds its 5 older reviews.
"""

import httpx
import pytest

from app.adapters.serpapi import SerpApiReviewSource
from app.core.errors import NotFoundError
from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.worker import process_next_run
from tests.db.ingestion_worker.conftest import (
    PAGE_1,
    PAGE_2,
    TOKEN_AFTER_PAGE_1,
    make_pipeline,
    run_row,
)

TOKEN_AFTER_PAGE_2 = PAGE_2["serpapi_pagination"]["next_page_token"]
PAGE_3 = {
    "search_metadata": {"status": "Success"},
    "reviews": [
        {**review, "review_id": f"older-{i}"}
        for i, review in enumerate(r for r in PAGE_2["reviews"] if r.get("snippet"))
    ][:5],
}


def place(older=None):
    """Fake SerpApi for the three-page place. `older` replaces page 3's answer
    (e.g. a timeout). Returns (open_source, tokens asked for)."""
    asked = []

    def handle(request):
        token = request.url.params.get("next_page_token")
        asked.append(token)
        if token is None:
            return httpx.Response(200, json=PAGE_1)
        if token == TOKEN_AFTER_PAGE_1:
            return httpx.Response(200, json=PAGE_2)
        if token == TOKEN_AFTER_PAGE_2:
            if isinstance(older, Exception):
                raise older
            return older or httpx.Response(200, json=PAGE_3)
        return httpx.Response(200, json={"reviews": []})

    def open_source():
        client = httpx.Client(transport=httpx.MockTransport(handle))
        return SerpApiReviewSource("test-key-0123456789abcdef", client)

    return open_source, asked


def queue_run(conn, target):
    run = IngestionRunRepository(conn).create_run(target["id"], target["owner_id"])
    conn.execute(
        "update public.ingestion_runs set queued_at = now() - interval '50 years' "
        "where id = %s",
        (run["id"],),
    )
    return run


def run_once(conn, open_source):
    process_next_run(make_pipeline(conn, open_source, ReviewRepository(conn)))


def cursor(conn, target):
    return conn.execute(
        "select older_page_token, all_reviews_collected "
        "from public.analysis_targets where id = %s",
        (target["id"],),
    ).fetchone()


def review_count(conn, target):
    return ReviewRepository(conn).count_for_target(target["id"], target["owner_id"])


@pytest.fixture
def first_run(conn, target, queued_run):
    """The first ingestion: pages 1 and 2, 18 saved, 12 rejected."""
    open_source, _ = place()
    run_once(conn, open_source)
    return queued_run


def test_the_first_run_saves_where_older_reviews_continue(conn, target, first_run):
    assert run_row(conn, first_run["id"])["status"] == "PARTIAL"
    assert review_count(conn, target) == 18
    assert cursor(conn, target) == {
        "older_page_token": TOKEN_AFTER_PAGE_2,
        "all_reviews_collected": False,
    }


def test_seen_review_ids_hold_saved_and_rejected_reviews(conn, target, first_run):
    seen = ReviewRepository(conn).seen_review_ids_for_target(
        target["id"], target["owner_id"]
    )

    every_id = {r["review_id"] for r in PAGE_1["reviews"] + PAGE_2["reviews"]}
    assert seen == every_id  # 18 saved + 12 rating-only rejected


def test_seen_review_ids_of_another_users_target_are_not_found(
    conn, make_user, target, first_run
):
    with pytest.raises(NotFoundError):
        ReviewRepository(conn).seen_review_ids_for_target(target["id"], make_user())


def test_a_refresh_continues_with_older_reviews_on_the_same_target(
    conn, target, first_run
):
    refresh = queue_run(conn, target)
    open_source, asked = place()

    run_once(conn, open_source)

    row = run_row(conn, refresh["id"])
    # Page 1 (nothing new), then straight to page 3: page 2 is not read again.
    assert asked == [None, TOKEN_AFTER_PAGE_2]
    assert row["status"] == "COMPLETED"
    assert (row["reviews_ingested"], row["reviews_rejected"]) == (5, 0)
    # Page 1's 10 were seen before: 5 saved earlier, 5 rejected earlier.
    assert row["reviews_duplicate"] == 10
    assert row["source_metadata"]["known_reviews_before"] == 30
    assert row["source_metadata"]["resumed_older"] is True
    assert review_count(conn, target) == 23
    assert cursor(conn, target) == {
        "older_page_token": None,
        "all_reviews_collected": True,
    }
    # Still one target, and every run belongs to it.
    targets = TargetRepository(conn).list_for_owner(target["owner_id"])
    assert [t["id"] for t in targets] == [target["id"]]


def test_once_everything_is_collected_a_refresh_costs_one_search(
    conn, target, first_run
):
    queue_run(conn, target)
    run_once(conn, place()[0])  # collects page 3
    last = queue_run(conn, target)
    open_source, asked = place()

    run_once(conn, open_source)

    row = run_row(conn, last["id"])
    assert asked == [None]
    assert row["status"] == "COMPLETED"
    assert row["reviews_ingested"] == 0
    assert row["source_metadata"]["stopped_because"] == "CAUGHT_UP"
    assert review_count(conn, target) == 23


def test_a_failed_refresh_keeps_the_reviews_and_the_cursor(conn, target, first_run):
    refresh = queue_run(conn, target)
    open_source, _ = place(older=httpx.ReadTimeout("simulated"))

    run_once(conn, open_source)

    row = run_row(conn, refresh["id"])
    assert (row["status"], row["error_code"]) == ("FAILED", "TIMEOUT")
    assert review_count(conn, target) == 18
    assert cursor(conn, target) == {
        "older_page_token": TOKEN_AFTER_PAGE_2,
        "all_reviews_collected": False,
    }


def test_the_target_reports_how_many_reviews_the_place_lists(conn, target, first_run):
    shown = TargetRepository(conn).get_owned(target["id"], target["owner_id"])

    assert shown["place_review_count"] == PAGE_1["place_info"]["reviews"]
    assert shown["all_reviews_collected"] is False
