"""S1-022: GET /targets/{id}/reviews, from token to database row.

Skipped without TEST_DATABASE_URL.
"""

import pytest

from app.api.targets import MAX_REVIEW_PAGE_SIZE, REVIEW_PAGE_SIZE
from app.db import get_connection
from app.main import app
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def make_target_with_review(conn):
    def _make(owner_id, text="Great slice", count=1) -> str:
        target = TargetRepository(conn).create(owner_id, "Joe's", "google_maps", URL)
        run_id = conn.execute(
            """
            insert into public.ingestion_runs
                (target_id, status, started_at, finished_at, reviews_ingested)
            values (%s, 'COMPLETED', now(), now(), 1)
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
                    rating=5,
                    review_text=text,
                    mapping_version="serpapi@1",
                    source_metadata={"likes": 1},
                )
                for _ in range(count)
            ],
        )
        return str(target["id"])

    return _make


def test_owner_gets_the_saved_reviews(
    api, make_user, make_token, make_target_with_review
):
    owner = make_user()
    target_id = make_target_with_review(owner)

    r = api.get(
        f"/api/v1/targets/{target_id}/reviews", headers=auth(make_token(str(owner)))
    )

    assert r.status_code == 200
    [review] = r.json()["items"]
    assert review["target_id"] == target_id
    assert review["review_text"] == "Great slice"
    assert review["rating"] == 5
    assert review["source_metadata"] == {"likes": 1}


def test_target_with_no_reviews_returns_an_empty_list(api, conn, make_user, make_token):
    owner = make_user()
    target = TargetRepository(conn).create(owner, "Empty", "google_maps", URL)

    r = api.get(
        f"/api/v1/targets/{target['id']}/reviews", headers=auth(make_token(str(owner)))
    )

    assert r.status_code == 200
    assert r.json() == {
        "items": [],
        "total": 0,
        "limit": REVIEW_PAGE_SIZE,
        "offset": 0,
    }


def test_first_page_is_one_page_size_and_the_total_counts_all(
    api, make_user, make_token, make_target_with_review
):
    owner = make_user()
    target_id = make_target_with_review(owner, count=REVIEW_PAGE_SIZE + 1)

    r = api.get(
        f"/api/v1/targets/{target_id}/reviews", headers=auth(make_token(str(owner)))
    )

    body = r.json()
    assert len(body["items"]) == REVIEW_PAGE_SIZE
    assert body["total"] == REVIEW_PAGE_SIZE + 1
    assert (body["limit"], body["offset"]) == (REVIEW_PAGE_SIZE, 0)


def test_pages_return_every_review_once_in_order(
    api, make_user, make_token, make_target_with_review
):
    owner = make_user()
    target_id = make_target_with_review(owner, count=5)
    url = f"/api/v1/targets/{target_id}/reviews"
    headers = auth(make_token(str(owner)))

    pages = [
        api.get(f"{url}?limit=2&offset={offset}", headers=headers).json()
        for offset in (0, 2, 4)
    ]
    everything = api.get(f"{url}?limit={MAX_REVIEW_PAGE_SIZE}", headers=headers).json()

    assert [len(p["items"]) for p in pages] == [2, 2, 1]
    assert {p["total"] for p in pages} == {5}
    paged_ids = [review["id"] for p in pages for review in p["items"]]
    assert paged_ids == [review["id"] for review in everything["items"]]


def test_offset_past_the_end_returns_no_reviews_but_the_total(
    api, make_user, make_token, make_target_with_review
):
    owner = make_user()
    target_id = make_target_with_review(owner, count=3)

    r = api.get(
        f"/api/v1/targets/{target_id}/reviews?offset=10",
        headers=auth(make_token(str(owner))),
    )

    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total"] == 3


def test_each_target_returns_only_its_own_reviews(
    api, make_user, make_token, make_target_with_review
):
    owner = make_user()
    target_a = make_target_with_review(owner, "Only A")
    target_b = make_target_with_review(owner, "Only B")
    headers = auth(make_token(str(owner)))

    a = api.get(f"/api/v1/targets/{target_a}/reviews", headers=headers).json()
    b = api.get(f"/api/v1/targets/{target_b}/reviews", headers=headers).json()

    assert [r["review_text"] for r in a["items"]] == ["Only A"]
    assert [r["review_text"] for r in b["items"]] == ["Only B"]


def test_user_b_gets_the_same_404_as_for_a_target_that_does_not_exist(
    api, make_user, make_token, make_target_with_review
):
    target_id = make_target_with_review(make_user(), "Private to A")
    headers = auth(make_token(str(make_user())))

    denied = api.get(f"/api/v1/targets/{target_id}/reviews", headers=headers)
    unknown = api.get(f"/api/v1/targets/{UNKNOWN_ID}/reviews", headers=headers)

    assert denied.status_code == 404
    assert "Private to A" not in denied.text
    assert denied.json() == unknown.json()
