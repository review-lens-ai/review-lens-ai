"""S1-014: ordering, ownership edges and constraint behavior of TargetRepository.

test_target_repository.py covers the main paths. These tests pin down the parts a
quick manual check would miss: which target comes first, which ingestion run
decides the status, and that one owner's data never leaks into another's results.
"""

import uuid

import psycopg
import pytest

from app.core.errors import NotFoundError
from app.repositories.targets import TargetRepository
from tests.db.conftest import insert_run

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def repo(conn):
    return TargetRepository(conn)


def _backdate(conn, target_id, interval):
    conn.execute(
        "update public.analysis_targets set created_at = now() - %s::interval where id = %s",
        (interval, target_id),
    )


def _run(conn, target_id, status, queued_ago):
    insert_run(conn, target_id, status, age=queued_ago)


# --- order ------------------------------------------------------------------------


def test_targets_are_listed_newest_first(conn, repo, make_user):
    owner = make_user()
    oldest = repo.create(owner, "Oldest", "google_maps", URL)
    newest = repo.create(owner, "Newest", "google_maps", URL)
    middle = repo.create(owner, "Middle", "google_maps", URL)
    _backdate(conn, oldest["id"], "3 days")
    _backdate(conn, middle["id"], "2 days")
    _backdate(conn, newest["id"], "1 day")

    assert [t["name"] for t in repo.list_for_owner(owner)] == [
        "Newest",
        "Middle",
        "Oldest",
    ]


# --- which ingestion run decides the status ---------------------------------------------


@pytest.mark.parametrize("newest_first", [True, False])
def test_the_status_comes_from_the_newest_run_whatever_order_runs_were_saved(
    conn, repo, make_user, newest_first
):
    owner = make_user()
    target = repo.create(owner, "Joe's Pizza", "google_maps", URL)
    runs = [("FAILED", "1 hour"), ("COMPLETED", "2 hours")]  # failed is the newer run
    for status, ago in runs if newest_first else reversed(runs):
        _run(conn, target["id"], status, ago)

    assert repo.get_owned(target["id"], owner)["ingestion_status"] == "FAILED"
    assert repo.list_for_owner(owner)[0]["ingestion_status"] == "FAILED"


def test_an_unfinished_run_shows_as_its_own_status(conn, repo, make_user):
    owner = make_user()
    target = repo.create(owner, "Joe's Pizza", "google_maps", URL)
    _run(conn, target["id"], "RUNNING", "1 minute")

    assert repo.get_owned(target["id"], owner)["ingestion_status"] == "RUNNING"


def test_another_targets_run_never_changes_this_targets_status(conn, repo, make_user):
    owner = make_user()
    never_ingested = repo.create(owner, "Never ingested", "google_maps", URL)
    ingested = repo.create(owner, "Ingested", "google_maps", URL)
    _run(conn, ingested["id"], "FAILED", "1 hour")

    assert repo.get_owned(never_ingested["id"], owner)["ingestion_status"] is None
    assert repo.get_owned(ingested["id"], owner)["ingestion_status"] == "FAILED"


# --- ownership edges -----------------------------------------------------------------


def test_every_target_an_owner_has_is_listed_and_none_of_anyone_elses(repo, make_user):
    user_a, user_b = make_user(), make_user()
    for index in range(3):
        repo.create(user_a, f"A{index}", "google_maps", URL)
    repo.create(user_b, "B0", "google_maps", URL)

    assert sorted(t["name"] for t in repo.list_for_owner(user_a)) == ["A0", "A1", "A2"]
    assert [t["name"] for t in repo.list_for_owner(user_b)] == ["B0"]


def test_someone_elses_target_looks_exactly_like_a_target_that_does_not_exist(
    repo, make_user
):
    target = repo.create(make_user(), "Joe's Pizza", "google_maps", URL)

    with pytest.raises(NotFoundError) as someone_elses:
        repo.get_owned(target["id"], make_user())
    with pytest.raises(NotFoundError) as missing:
        repo.get_owned(uuid.uuid4(), make_user())

    assert str(someone_elses.value) == str(missing.value)
    assert someone_elses.value.status_code == missing.value.status_code == 404


def test_ids_may_be_given_as_uuid_objects_or_as_text(repo, make_user):
    owner = make_user()
    target = repo.create(owner, "Joe's Pizza", "google_maps", URL)

    assert repo.get_owned(target["id"], owner)["name"] == "Joe's Pizza"
    assert repo.get_owned(str(target["id"]), str(owner))["name"] == "Joe's Pizza"


def test_the_owner_is_not_part_of_what_the_repository_returns(repo, make_user):
    target = repo.create(make_user(), "Joe's Pizza", "google_maps", URL)
    assert "owner_id" not in target


# --- what is stored ----------------------------------------------------------------------


def test_each_value_is_stored_in_its_own_column(conn, repo, make_user):
    owner = make_user()
    created = repo.create(owner, "Joe's Pizza", "google_maps", URL)

    row = conn.execute(
        "select owner_id, name, platform, source_url from public.analysis_targets "
        "where id = %s",
        (created["id"],),
    ).fetchone()
    assert row == {
        "owner_id": owner,
        "name": "Joe's Pizza",
        "platform": "google_maps",
        "source_url": URL,
    }


def test_a_one_character_name_is_stored(repo, make_user):
    created = repo.create(make_user(), "J", "google_maps", URL)
    assert created["name"] == "J"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_a_blank_name_is_refused_by_the_database(repo, make_user, name):
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.create(make_user(), name, "google_maps", URL)


def test_a_target_for_an_owner_who_does_not_exist_is_refused(repo):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        repo.create(uuid.uuid4(), "Joe's Pizza", "google_maps", URL)
