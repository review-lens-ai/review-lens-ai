"""S1-023: every ingestion attempt records a result state, and ownership is enforced."""

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.repositories.ingestion_runs import ErrorCode, IngestionRunRepository
from app.repositories.targets import TargetRepository

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def runs(conn):
    return IngestionRunRepository(conn)


@pytest.fixture
def owned_target(conn, make_user):
    owner = make_user()
    target = TargetRepository(conn).create(owner, "Joe's Pizza", "google_maps", URL)
    return target["id"], owner


def test_start_run_records_a_running_attempt(runs, owned_target):
    target_id, owner = owned_target
    run = runs.start_run(target_id, owner)

    assert run["status"] == "running"
    assert run["target_id"] == target_id
    assert run["finished_at"] is None
    assert run["reviews_ingested"] == 0


def test_successful_run_records_counts_and_finish_time(runs, owned_target):
    target_id, owner = owned_target
    run = runs.start_run(target_id, owner)

    done = runs.mark_succeeded(run["id"], reviews_ingested=42, reviews_rejected=3)

    assert done["status"] == "succeeded"
    assert done["reviews_ingested"] == 42
    assert done["reviews_rejected"] == 3
    assert done["error_code"] is None
    assert done["finished_at"] is not None


def test_failed_run_records_error_code_and_no_reviews(runs, owned_target):
    target_id, owner = owned_target
    run = runs.start_run(target_id, owner)

    failed = runs.mark_failed(run["id"], ErrorCode.QUOTA_EXCEEDED)

    assert failed["status"] == "failed"
    assert failed["error_code"] == "quota_exceeded"
    assert failed["reviews_ingested"] == 0
    assert failed["finished_at"] is not None


def test_failure_detail_is_logged_but_never_stored(runs, owned_target, caplog):
    target_id, owner = owned_target
    run = runs.start_run(target_id, owner)
    secret_detail = "HTTP 429 from provider, api_key=sk-should-not-leak"

    failed = runs.mark_failed(run["id"], ErrorCode.QUOTA_EXCEEDED, detail=secret_detail)

    assert secret_detail not in str(failed)
    assert secret_detail in caplog.text


def test_unknown_error_code_is_rejected(runs, owned_target):
    target_id, owner = owned_target
    run = runs.start_run(target_id, owner)
    with pytest.raises(ValueError):
        runs.mark_failed(run["id"], "Traceback: psycopg.OperationalError ...")


def test_finished_run_cannot_be_changed_afterwards(runs, owned_target):
    target_id, owner = owned_target
    run = runs.start_run(target_id, owner)
    runs.mark_failed(run["id"], ErrorCode.TIMEOUT)

    # A failure must never be turned into a success later (S1-BR-024).
    with pytest.raises(NotFoundError):
        runs.mark_succeeded(run["id"], reviews_ingested=10)


def test_second_run_cannot_start_while_one_is_running(runs, owned_target):
    target_id, owner = owned_target
    runs.start_run(target_id, owner)
    with pytest.raises(ConflictError):
        runs.start_run(target_id, owner)


def test_new_run_can_start_after_previous_one_finished(runs, owned_target):
    target_id, owner = owned_target
    first = runs.start_run(target_id, owner)
    runs.mark_succeeded(first["id"], reviews_ingested=5)

    second = runs.start_run(target_id, owner)
    assert second["status"] == "running"


def test_latest_is_none_before_any_ingestion(runs, owned_target):
    target_id, owner = owned_target
    assert runs.latest_for_target(target_id, owner) is None


def test_latest_returns_the_newest_run(conn, runs, owned_target):
    target_id, owner = owned_target
    first = runs.start_run(target_id, owner)
    runs.mark_succeeded(first["id"], reviews_ingested=42)
    # Make the first run clearly older than the next one.
    conn.execute(
        "update public.ingestion_runs set started_at = started_at - interval '1 hour', "
        "finished_at = finished_at - interval '1 hour' where id = %s",
        (first["id"],),
    )
    second = runs.start_run(target_id, owner)
    runs.mark_failed(second["id"], ErrorCode.SOURCE_UNREACHABLE)

    latest = runs.latest_for_target(target_id, owner)
    assert latest["id"] == second["id"]
    assert latest["status"] == "failed"


def test_user_b_cannot_start_a_run_on_user_a_target(runs, owned_target, make_user):
    target_id, _owner = owned_target
    with pytest.raises(NotFoundError):
        runs.start_run(target_id, make_user())


def test_user_b_cannot_read_user_a_runs(runs, owned_target, make_user):
    target_id, owner = owned_target
    runs.start_run(target_id, owner)
    with pytest.raises(NotFoundError):
        runs.latest_for_target(target_id, make_user())
