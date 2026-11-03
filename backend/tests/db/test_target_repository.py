"""S1-014: targets persist with their owner, and ownership is enforced on every read."""

import pytest

from app.core.errors import NotFoundError
from app.repositories.targets import TargetRepository

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def repo(conn):
    return TargetRepository(conn)


def test_created_target_can_be_retrieved_with_its_values_and_owner(
    conn, repo, make_user
):
    owner = make_user()
    created = repo.create(owner, "Joe's Pizza", "google_maps", URL)

    fetched = repo.get_owned(created["id"], owner)
    assert fetched["name"] == "Joe's Pizza"
    assert fetched["platform"] == "google_maps"
    assert fetched["source_url"] == URL
    assert fetched["created_at"] is not None
    stored_owner = conn.execute(
        "select owner_id from public.analysis_targets where id = %s", (created["id"],)
    ).fetchone()["owner_id"]
    assert stored_owner == owner


def test_user_b_cannot_get_user_a_target(repo, make_user):
    target = repo.create(make_user(), "Joe's Pizza", "google_maps", URL)
    with pytest.raises(NotFoundError):
        repo.get_owned(target["id"], make_user())


def test_unknown_target_is_not_found(repo, make_user):
    with pytest.raises(NotFoundError):
        repo.get_owned("00000000-0000-0000-0000-000000000000", make_user())


def test_each_user_lists_only_their_own_targets(repo, make_user):
    user_a, user_b = make_user(), make_user()
    repo.create(user_a, "A one", "google_maps", URL)
    repo.create(user_a, "A two", "google_maps", URL)
    repo.create(user_b, "B one", "google_maps", URL)

    assert {t["name"] for t in repo.list_for_owner(user_a)} == {"A one", "A two"}
    assert {t["name"] for t in repo.list_for_owner(user_b)} == {"B one"}


def test_user_with_no_targets_gets_an_empty_list(repo, make_user):
    assert repo.list_for_owner(make_user()) == []


def test_ingestion_status_is_none_until_a_run_exists_then_shows_the_latest(
    conn, repo, make_user
):
    owner = make_user()
    target = repo.create(owner, "Joe's Pizza", "google_maps", URL)
    assert repo.get_owned(target["id"], owner)["ingestion_status"] is None

    conn.execute(
        "insert into public.ingestion_runs (target_id, status, finished_at) "
        "values (%s, 'failed', now())",
        (target["id"],),
    )
    assert repo.get_owned(target["id"], owner)["ingestion_status"] == "failed"
