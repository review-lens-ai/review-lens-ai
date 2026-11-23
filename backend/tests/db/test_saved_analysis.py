"""S3-001: a saved analysis keeps the name the user chose, independent of its source
link. The provider's own title for the place is kept separately (source_metadata)
and never replaces the name, not when place details or the place id are saved.

The other parts of the saved record (owner, source URL, identity, review dataset)
are proven in test_targets_endpoints.py, test_target_repository*.py,
tests/api/test_get_target.py and test_active_target_dataset.py.
Skipped without TEST_DATABASE_URL.
"""

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.targets import TargetRepository
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza/data=!4m2!3m1!1s0x1:0x2"
NAME = "Mom's favorite spot"


@pytest.fixture
def repo(conn):
    return TargetRepository(conn)


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


def _stored_name(conn, target_id) -> str:
    return conn.execute(
        "select name from public.analysis_targets where id = %s", (target_id,)
    ).fetchone()["name"]


def test_a_name_unrelated_to_the_link_is_saved_as_given(
    api, conn, make_user, make_token
):
    owner = make_user()
    headers = auth(make_token(str(owner)))

    created = api.post(
        "/api/v1/targets", json={"name": NAME, "source_url": URL}, headers=headers
    )

    assert created.status_code == 201
    target_id = created.json()["id"]
    assert _stored_name(conn, target_id) == NAME
    reopened = api.get(f"/api/v1/targets/{target_id}", headers=headers).json()
    assert reopened["name"] == NAME
    assert reopened["source_url"] == URL
    listed = api.get("/api/v1/targets", headers=headers).json()["items"]
    assert [item["name"] for item in listed] == [NAME]


def test_saving_place_details_never_changes_the_name(conn, repo, make_user):
    owner = make_user()
    target = repo.create(owner, NAME, "google_maps", URL)

    repo.save_place_details(
        target["id"],
        owner,
        {"title": "Joe's Pizza - Official", "address": "7 Carmine St"},
    )

    assert _stored_name(conn, target["id"]) == NAME
    assert repo.get_owned(target["id"], owner)["name"] == NAME


def test_saving_the_place_id_never_changes_the_name(conn, repo, make_user):
    owner = make_user()
    target = repo.create(owner, NAME, "google_maps", URL)

    repo.save_place_id(target["id"], owner, "0x1:0x2")

    assert _stored_name(conn, target["id"]) == NAME
