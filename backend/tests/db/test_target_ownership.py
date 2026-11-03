"""S1-011: ownership comes only from the verified token, checked end to end in the database."""

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.targets import TargetRepository
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def api(client, conn):
    """The real endpoint and repository, writing to the test database (rolled back after)."""
    app.dependency_overrides[get_connection] = lambda: conn
    return client


def _owner_in_db(conn, target_id):
    return conn.execute(
        "select owner_id from public.analysis_targets where id = %s", (target_id,)
    ).fetchone()["owner_id"]


def test_owner_ids_in_the_body_are_ignored_and_owner_comes_from_token(
    api, conn, make_user, make_token
):
    user_a, user_b = make_user(), make_user()
    r = api.post(
        "/api/v1/targets",
        json={
            "name": "Joe's Pizza",
            "source_url": URL,
            "owner_id": str(user_b),
            "userId": str(user_b),
            "user_id": str(user_b),
        },
        headers=auth(make_token(str(user_a))),
    )

    assert r.status_code == 201
    target_id = r.json()["id"]
    assert _owner_in_db(conn, target_id) == user_a
    assert target_id not in {
        str(t["id"]) for t in TargetRepository(conn).list_for_owner(user_b)
    }


def test_request_without_a_token_creates_nothing_in_the_database(api, conn):
    count = "select count(*) as n from public.analysis_targets"
    before = conn.execute(count).fetchone()["n"]

    r = api.post("/api/v1/targets", json={"name": "Joe's Pizza", "source_url": URL})

    assert r.status_code == 401
    assert conn.execute(count).fetchone()["n"] == before
