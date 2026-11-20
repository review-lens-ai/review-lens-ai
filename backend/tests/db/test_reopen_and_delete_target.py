"""S3-004 and S3-005, checked against the database.

S3-004: a returning user (a new sign-in, so a new token and nothing cached) can find
their target again and get back its name, source, ingestion state, saved reviews and
summary. Everything comes from the database, so nothing depends on the earlier visit.

S3-005: once the owner deletes a target it is gone from every normal read: the list,
the target itself, its reviews and its summary. Other users' targets are untouched.
The cascade itself (runs, reviews, rejections) is covered in test_schema.py; another
user's delete being refused is in test_target_access.py.

Skipped without TEST_DATABASE_URL.
"""

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.targets import TargetRepository
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


def _target(conn, owner_id, name: str) -> str:
    return str(TargetRepository(conn).create(owner_id, name, "google_maps", URL)["id"])


def test_returning_owner_reopens_target_with_its_reviews_and_summary(
    api, conn, make_user, make_token, save_run
):
    owner = make_user()
    target_id = _target(conn, owner, "Joe's Pizza")
    save_run(target_id, owner, [5, 4, 1])

    # A later sign-in: a fresh token, no state carried over from the first visit.
    headers = auth(make_token(str(owner)))

    listed = api.get("/api/v1/targets", headers=headers).json()["items"]
    assert [t["id"] for t in listed] == [target_id]

    opened = api.get(f"/api/v1/targets/{target_id}", headers=headers).json()
    assert opened["name"] == "Joe's Pizza"
    assert opened["source_url"] == URL
    assert opened["ingestion_status"] == "COMPLETED"

    reviews = api.get(f"/api/v1/targets/{target_id}/reviews", headers=headers).json()
    assert reviews["total"] == 3
    assert sorted(r["rating"] for r in reviews["items"]) == [1, 4, 5]

    summary = api.get(f"/api/v1/targets/{target_id}/summary", headers=headers).json()
    assert summary["review_count"] == 3


def test_reopening_lists_only_the_returning_users_own_targets(
    api, conn, make_user, make_token
):
    owner, other = make_user(), make_user()
    mine = _target(conn, owner, "Mine")
    _target(conn, other, "Not mine")

    listed = api.get("/api/v1/targets", headers=auth(make_token(str(owner)))).json()

    assert [t["id"] for t in listed["items"]] == [mine]


def test_deleted_target_is_gone_from_the_list_and_every_read(
    api, conn, make_user, make_token, save_run
):
    owner = make_user()
    target_id = _target(conn, owner, "Joe's Pizza")
    kept_id = _target(conn, owner, "Kept Cafe")
    save_run(target_id, owner, [5, 3])
    headers = auth(make_token(str(owner)))

    assert (
        api.delete(f"/api/v1/targets/{target_id}", headers=headers).status_code == 204
    )

    listed = api.get("/api/v1/targets", headers=headers).json()["items"]
    assert [t["id"] for t in listed] == [kept_id]
    for path in ("", "/reviews", "/summary"):
        r = api.get(f"/api/v1/targets/{target_id}{path}", headers=headers)
        assert r.status_code == 404, path


def test_deleting_one_users_target_leaves_another_users_target_and_reviews(
    api, conn, make_user, make_token, save_run
):
    owner, other = make_user(), make_user()
    target_id = _target(conn, owner, "Joe's Pizza")
    others_id = _target(conn, other, "Joe's Pizza")
    save_run(others_id, other, [4])

    api.delete(f"/api/v1/targets/{target_id}", headers=auth(make_token(str(owner))))

    other_headers = auth(make_token(str(other)))
    r = api.get(f"/api/v1/targets/{others_id}/reviews", headers=other_headers)
    assert r.status_code == 200
    assert r.json()["total"] == 1
