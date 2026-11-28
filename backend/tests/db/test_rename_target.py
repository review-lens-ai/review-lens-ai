"""S3-002: PATCH /api/v1/targets/{id} renames a target its owner owns, and changes
nothing else (S3-BR-001, S3-BR-002, S3-BR-004), against real Postgres.

No token is a 401: tests/api/test_openapi_contract.py checks every route for that.
Q&A history staying the same is added once S3-006 (#176) is on dev.
Skipped without TEST_DATABASE_URL.
"""

import uuid

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza/data=!4m2!3m1!1s0x1:0x2"
OTHER_URL = "https://www.google.com/maps/place/Blue+Bottle/data=!4m2!3m1!1s0x3:0x4"
OLD_NAME = "Joe's Pizza"
REVIEWS = [("The crust is perfect", 5), ("Slow service at lunch", 2)]


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def make_target(conn):
    """A target named OLD_NAME with REVIEWS saved by one finished run."""

    def _make(owner_id) -> str:
        target = TargetRepository(conn).create(owner_id, OLD_NAME, "google_maps", URL)
        run_id = conn.execute(
            "insert into public.ingestion_runs (target_id, status, started_at) "
            "values (%s, 'RUNNING', now()) returning id",
            (target["id"],),
        ).fetchone()["id"]
        ReviewRepository(conn).bulk_insert(
            run_id,
            target["id"],
            owner_id,
            [
                ReviewCreate(rating=r, review_text=t, mapping_version="serpapi@1")
                for t, r in REVIEWS
            ],
        )
        return str(target["id"])

    return _make


def rename(api, make_token, user, target_id, body):
    return api.patch(
        f"/api/v1/targets/{target_id}", json=body, headers=auth(make_token(str(user)))
    )


def stored(conn, target_id) -> dict:
    """The target's row and everything under it that a rename must not change."""
    target = conn.execute(
        "select owner_id, name, platform, source_url from public.analysis_targets "
        "where id = %s",
        (target_id,),
    ).fetchone()
    reviews = conn.execute(
        "select id, rating, review_text, run_id from public.reviews "
        "where target_id = %s order by id",
        (target_id,),
    ).fetchall()
    runs = conn.execute(
        "select id, status, reviews_ingested from public.ingestion_runs "
        "where target_id = %s order by id",
        (target_id,),
    ).fetchall()
    return {"target": target, "reviews": reviews, "runs": runs}


def test_owner_renames_their_target(api, make_user, make_target, make_token):
    owner = make_user()
    target_id = make_target(owner)
    headers = auth(make_token(str(owner)))

    response = rename(api, make_token, owner, target_id, {"name": "  Mom's spot  "})

    assert response.status_code == 200
    assert response.json()["id"] == target_id
    assert response.json()["name"] == "Mom's spot"  # trimmed, same rules as create
    reopened = api.get(f"/api/v1/targets/{target_id}", headers=headers).json()
    assert reopened["name"] == "Mom's spot"
    listed = api.get("/api/v1/targets", headers=headers).json()["items"]
    assert [item["name"] for item in listed] == ["Mom's spot"]


def test_rename_keeps_source_url_platform_and_reviews(
    api, conn, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    headers = auth(make_token(str(owner)))
    before = stored(conn, target_id)
    summary_before = api.get(f"/api/v1/targets/{target_id}/summary", headers=headers)

    rename(api, make_token, owner, target_id, {"name": "Mom's spot"})

    after = stored(conn, target_id)
    assert after["target"] == before["target"] | {"name": "Mom's spot"}
    assert after["reviews"] == before["reviews"]
    assert len(after["reviews"]) == len(REVIEWS)
    assert after["runs"] == before["runs"]
    summary_after = api.get(f"/api/v1/targets/{target_id}/summary", headers=headers)
    assert summary_after.json() == summary_before.json()


def test_rename_ignores_a_source_url_or_owner_id_in_the_body(
    api, conn, make_user, make_target, make_token
):
    owner, other = make_user(), make_user()
    target_id = make_target(owner)

    response = rename(
        api,
        make_token,
        owner,
        target_id,
        {"name": "Mom's spot", "source_url": OTHER_URL, "owner_id": str(other)},
    )

    assert response.status_code == 200
    row = stored(conn, target_id)["target"]
    assert row["source_url"] == URL
    assert row["owner_id"] == owner


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "!!!"},
        {"name": "x" * 201},
        {"name": "Joe\nPizza"},
        {},
    ],
    ids=["empty", "spaces", "no-letter", "too-long", "line-break", "missing"],
)
def test_a_bad_name_is_422_and_nothing_changes(
    api, conn, make_user, make_target, make_token, body
):
    owner = make_user()
    target_id = make_target(owner)
    before = stored(conn, target_id)

    response = rename(api, make_token, owner, target_id, body)

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "name"
    assert stored(conn, target_id) == before


def test_user_b_cannot_rename_user_a_target(
    api, conn, make_user, make_target, make_token
):
    user_a, user_b = make_user(), make_user()
    target_id = make_target(user_a)
    before = stored(conn, target_id)

    response = rename(api, make_token, user_b, target_id, {"name": "B's name now"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert OLD_NAME not in response.text
    assert stored(conn, target_id) == before


def test_renaming_a_missing_target_is_404(api, make_user, make_token):
    response = rename(api, make_token, make_user(), uuid.uuid4(), {"name": "Anything"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
