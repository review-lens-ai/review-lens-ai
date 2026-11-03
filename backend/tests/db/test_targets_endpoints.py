"""Endpoints + real SQL together: the whole path from token to database row.

Complements tests/api (fake repository) by proving the owner filter works in the
actual queries. Skipped without TEST_DATABASE_URL.
"""

import pytest

from app.db import get_connection
from app.main import app
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


def _token_for(make_token, user_id):
    return make_token(str(user_id))


def test_created_target_is_stored_with_the_token_users_id_as_owner(
    api, conn, make_user, make_token
):
    user_a, user_b = make_user(), make_user()
    r = api.post(
        "/api/v1/targets",
        json={"name": "Joe's", "source_url": URL, "owner_id": str(user_b)},
        headers=auth(_token_for(make_token, user_a)),
    )
    assert r.status_code == 201
    owner = conn.execute(
        "select owner_id from public.analysis_targets where id = %s", (r.json()["id"],)
    ).fetchone()["owner_id"]
    assert owner == user_a


def test_users_only_list_and_open_their_own_targets(api, make_user, make_token):
    user_a, user_b = make_user(), make_user()
    token_a, token_b = _token_for(make_token, user_a), _token_for(make_token, user_b)
    created = api.post(
        "/api/v1/targets",
        json={"name": "A only", "source_url": URL},
        headers=auth(token_a),
    ).json()

    assert (
        api.get(f"/api/v1/targets/{created['id']}", headers=auth(token_a)).status_code
        == 200
    )
    assert api.get("/api/v1/targets", headers=auth(token_b)).json() == {"items": []}
    denied = api.get(f"/api/v1/targets/{created['id']}", headers=auth(token_b))
    assert denied.status_code == 404
    assert "A only" not in denied.text
