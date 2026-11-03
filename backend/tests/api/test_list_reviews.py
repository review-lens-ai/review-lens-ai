"""S1-022: the reviews endpoint rejects bad requests before any query runs."""

import pytest

from app.api.targets import (
    MAX_REVIEW_OFFSET,
    MAX_REVIEW_PAGE_SIZE,
    get_review_repository,
)
from app.main import app
from tests.conftest import auth

UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


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
