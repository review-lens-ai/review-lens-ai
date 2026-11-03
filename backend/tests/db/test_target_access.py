"""S1-012: user B cannot open or delete user A's target, checked against the database."""

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.targets import TargetRepository
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza/@40.73,-73.99,17z/data=!3m1!4b1!4m6!3m5!1s0x89c2537da9b3163d:0x2be30fcff506fec8!8m2!3d40.73!4d-73.99"


@pytest.fixture
def api(client, conn):
    """The real endpoints and repository, writing to the test database (rolled back after)."""
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def target_of_user_a(conn, make_user):
    user_a = make_user()
    target = TargetRepository(conn).create(user_a, "A's pizza", "google_maps", URL)
    return user_a, target


def _stored(conn, target_id):
    return conn.execute(
        "select owner_id, name, source_url from public.analysis_targets where id = %s",
        (target_id,),
    ).fetchone()


def test_user_b_cannot_open_user_a_target_and_gets_no_data(
    api, make_user, make_token, target_of_user_a
):
    _, target = target_of_user_a
    user_b = make_user()

    r = api.get(
        f"/api/v1/targets/{target['id']}", headers=auth(make_token(str(user_b)))
    )

    assert r.status_code == 404
    assert "A's pizza" not in r.text
    assert URL not in r.text


def test_user_b_cannot_delete_user_a_target_and_it_is_unchanged(
    api, conn, make_user, make_token, target_of_user_a
):
    _, target = target_of_user_a
    user_b = make_user()
    before = _stored(conn, target["id"])

    r = api.delete(
        f"/api/v1/targets/{target['id']}", headers=auth(make_token(str(user_b)))
    )

    assert r.status_code == 404
    assert _stored(conn, target["id"]) == before


def test_owner_opens_and_deletes_their_own_target(
    api, conn, make_token, target_of_user_a
):
    user_a, target = target_of_user_a
    url = f"/api/v1/targets/{target['id']}"
    headers = auth(make_token(str(user_a)))

    assert api.get(url, headers=headers).status_code == 200
    assert api.delete(url, headers=headers).status_code == 204
    assert _stored(conn, target["id"]) is None


def test_deleting_without_a_token_is_rejected_and_nothing_changes(
    api, conn, target_of_user_a
):
    _, target = target_of_user_a
    before = _stored(conn, target["id"])

    r = api.delete(f"/api/v1/targets/{target['id']}")

    assert r.status_code == 401
    assert _stored(conn, target["id"]) == before
