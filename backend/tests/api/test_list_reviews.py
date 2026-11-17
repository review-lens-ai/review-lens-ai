"""S1-022: the reviews endpoint, through HTTP. No database: the repository is an
in-memory fake with the same ownership rule (the real SQL is in tests/db)."""

import uuid
from datetime import UTC, datetime

import pytest

from app.api.targets import (
    MAX_REVIEW_OFFSET,
    MAX_REVIEW_PAGE_SIZE,
    get_review_repository,
)
from app.core.errors import NotFoundError
from app.main import app
from tests.conftest import auth

UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"
TARGET_ID = str(uuid.uuid4())
KNOWN_REVIEW_ID = str(uuid.uuid4())


class FakeReviewRepository:
    """One target, owned by user-a, with one review."""

    def _require_owner(self, target_id, owner_id):
        if str(target_id) != TARGET_ID or owner_id != "user-a":
            raise NotFoundError("Target not found.")

    def count_for_target(self, target_id, owner_id):
        self._require_owner(target_id, owner_id)
        return 1

    def page_offset_of(self, target_id, owner_id, review_id, *, limit):
        self._require_owner(target_id, owner_id)
        if str(review_id) != KNOWN_REVIEW_ID:
            raise NotFoundError("Review not found.")
        return 0

    def list_for_target(self, target_id, owner_id, *, limit=None, offset=0):
        self._require_owner(target_id, owner_id)
        return [
            {
                "id": uuid.uuid4(),
                "target_id": TARGET_ID,
                "run_id": uuid.uuid4(),
                "rating": 2,
                "review_text": "Private to A",
                "mapping_version": "serpapi@1",
                "reviewed_at": None,
                "source_review_id": "r1",
                "source_url": None,
                "source_metadata": {},
                "created_at": datetime.now(UTC),
            }
        ]


def test_user_a_reads_their_own_reviews(client, make_token):
    app.dependency_overrides[get_review_repository] = FakeReviewRepository

    r = client.get(
        f"/api/v1/targets/{TARGET_ID}/reviews", headers=auth(make_token("user-a"))
    )

    assert r.status_code == 200
    assert [x["review_text"] for x in r.json()["items"]] == ["Private to A"]


def test_user_b_reading_user_a_reviews_gets_not_found_and_no_review_data(
    client, make_token
):
    app.dependency_overrides[get_review_repository] = FakeReviewRepository
    headers = auth(make_token("user-b"))

    denied = client.get(f"/api/v1/targets/{TARGET_ID}/reviews", headers=headers)
    unknown = client.get(f"/api/v1/targets/{UNKNOWN_ID}/reviews", headers=headers)

    assert denied.status_code == 404
    assert list(denied.json()) == ["error"]
    assert "Private to A" not in denied.text
    assert denied.json() == unknown.json()


def test_listing_reviews_without_a_token_is_unauthorized(client):
    r = client.get(f"/api/v1/targets/{UNKNOWN_ID}/reviews")
    assert r.status_code == 401
    assert "items" not in r.json()


def test_listing_reviews_with_a_malformed_target_id_is_rejected(client, make_token):
    app.dependency_overrides[get_review_repository] = lambda: None

    r = client.get("/api/v1/targets/not-a-uuid/reviews", headers=auth(make_token()))

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        f"limit={MAX_REVIEW_PAGE_SIZE + 1}",
        "limit=abc",
        "offset=-1",
        f"offset={MAX_REVIEW_OFFSET + 1}",
        f"offset={2**63}",  # past Postgres bigint: a 422, not a database error
        "offset=abc",
    ],
)
def test_page_outside_the_allowed_range_is_rejected(client, make_token, query):
    app.dependency_overrides[get_review_repository] = lambda: None

    r = client.get(
        f"/api/v1/targets/{UNKNOWN_ID}/reviews?{query}", headers=auth(make_token())
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_around_returns_the_page_with_that_review(client, make_token):
    app.dependency_overrides[get_review_repository] = FakeReviewRepository

    r = client.get(
        f"/api/v1/targets/{TARGET_ID}/reviews?around={KNOWN_REVIEW_ID}",
        headers=auth(make_token("user-a")),
    )

    assert r.status_code == 200
    assert r.json()["offset"] == 0


def test_around_a_review_that_is_not_in_the_target_is_not_found(client, make_token):
    app.dependency_overrides[get_review_repository] = FakeReviewRepository

    r = client.get(
        f"/api/v1/targets/{TARGET_ID}/reviews?around={UNKNOWN_ID}",
        headers=auth(make_token("user-a")),
    )

    assert r.status_code == 404
