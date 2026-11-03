"""S1-023: every ingestion attempt records a result state, and ownership is enforced.

Lifecycle: queued -> running -> succeeded / partial / failed (and queued -> failed).
"""

import logging
import threading
import time
import uuid

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.db import connect
from app.repositories.ingestion_runs import (
    MAX_LOGGED_DETAIL,
    STALE_RUN_AFTER,
    ErrorCode,
    IngestionRunRepository,
    InvalidRunTransitionError,
    scrub_detail,
)
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


@pytest.fixture
def running_run(runs, owned_target):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)
    return runs.mark_running(run["id"])


def _age(conn, run_id, by):
    """Pretend a run was queued (and, if it started, started) `by` ago."""
    conn.execute(
        "update public.ingestion_runs set queued_at = now() - %(by)s, "
        "started_at = case when started_at is null then null else now() - %(by)s end "
        "where id = %(id)s",
        {"by": by, "id": run_id},
    )


# --- creating and starting ---------------------------------------------------


def test_new_run_starts_as_queued(runs, owned_target):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)

    assert run["status"] == "QUEUED"
    assert run["target_id"] == target_id
    assert run["finished_at"] is None
    assert run["reviews_ingested"] == 0


def test_queued_run_can_start_running(runs, owned_target):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)

    started = runs.mark_running(run["id"])
    assert started["status"] == "RUNNING"
    assert started["finished_at"] is None


def test_mark_running_twice_is_refused(runs, running_run):
    with pytest.raises(InvalidRunTransitionError):
        runs.mark_running(running_run["id"])


# --- finishing ----------------------------------------------------------------


def test_successful_run_records_counts_and_finish_time(runs, running_run):
    done = runs.mark_succeeded(running_run["id"], reviews_ingested=42)

    assert done["status"] == "COMPLETED"
    assert done["reviews_ingested"] == 42
    assert done["reviews_rejected"] == 0
    assert done["error_code"] is None
    assert done["finished_at"] is not None


def test_success_with_skipped_reviews_is_partial(runs, running_run):
    done = runs.mark_succeeded(
        running_run["id"], reviews_ingested=38, reviews_rejected=4
    )
    assert done["status"] == "PARTIAL"
    assert done["reviews_ingested"] == 38
    assert done["reviews_rejected"] == 4


def test_mark_partial_records_both_counts(runs, running_run):
    done = runs.mark_partial(running_run["id"], reviews_ingested=5, reviews_rejected=1)
    assert done["status"] == "PARTIAL"


def test_mark_partial_needs_saved_and_skipped_reviews(runs, running_run):
    with pytest.raises(ValueError):
        runs.mark_partial(running_run["id"], reviews_ingested=5, reviews_rejected=0)


def test_zero_saved_reviews_is_a_failure_not_a_success(runs, running_run):
    done = runs.mark_succeeded(running_run["id"], reviews_ingested=0)

    assert done["status"] == "FAILED"
    assert done["error_code"] == "NO_REVIEWS_FOUND"


def test_queued_run_cannot_succeed_without_running(runs, owned_target):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)

    # Guards against a fake success: nothing was fetched yet (S1-BR-024).
    with pytest.raises(InvalidRunTransitionError):
        runs.mark_succeeded(run["id"], reviews_ingested=10)


def test_queued_run_can_fail_when_it_cannot_start(runs, owned_target):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)

    failed = runs.mark_failed(run["id"], ErrorCode.QUOTA_EXCEEDED)
    assert failed["status"] == "FAILED"
    assert failed["error_code"] == "QUOTA_EXCEEDED"
    assert failed["finished_at"] is not None


def test_running_run_can_fail(runs, running_run):
    failed = runs.mark_failed(running_run["id"], ErrorCode.SOURCE_UNREACHABLE)

    assert failed["status"] == "FAILED"
    assert failed["error_code"] == "SOURCE_UNREACHABLE"
    assert failed["reviews_ingested"] == 0


def test_finished_run_cannot_be_changed_afterwards(runs, running_run):
    runs.mark_failed(running_run["id"], ErrorCode.TIMEOUT)

    # A failure must never be turned into a success later (S1-BR-024).
    with pytest.raises(InvalidRunTransitionError):
        runs.mark_succeeded(running_run["id"], reviews_ingested=10)
    with pytest.raises(InvalidRunTransitionError):
        runs.mark_running(running_run["id"])


def test_unknown_error_code_is_rejected(runs, running_run):
    with pytest.raises(ValueError):
        runs.mark_failed(running_run["id"], "Traceback: psycopg.OperationalError ...")


def test_negative_counts_are_rejected(runs, running_run):
    with pytest.raises(ValueError):
        runs.mark_succeeded(running_run["id"], reviews_ingested=-1)


# --- safe logging -----------------------------------------------------------------


def test_failure_detail_is_logged_scrubbed_and_never_stored(runs, running_run, caplog):
    detail = (
        "HTTPError 429 for url: https://serpapi.com/search.json"
        "?engine=google_maps_reviews&api_key=FAKE-KEY-123&data_id=abc"
    )
    with caplog.at_level(logging.WARNING):
        failed = runs.mark_failed(running_run["id"], ErrorCode.QUOTA_EXCEEDED, detail)

    assert "FAKE-KEY-123" not in caplog.text
    assert "serpapi.com/search.json" in caplog.text  # still useful for debugging
    assert "HTTPError 429" not in str(failed)


def test_scrub_removes_keys_and_tokens_outside_urls():
    text = scrub_detail("bad request token=abc123 key=zzz api_key=yyy")
    assert "abc123" not in text
    assert "zzz" not in text
    assert "yyy" not in text


def test_scrub_truncates_long_detail():
    assert len(scrub_detail("x" * 5000)) <= MAX_LOGGED_DETAIL + 1


# --- one active run per target -----------------------------------------------------


@pytest.mark.parametrize("move_to_running", [False, True])
def test_new_run_is_refused_while_one_is_queued_or_running(
    runs, owned_target, move_to_running
):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)
    if move_to_running:
        runs.mark_running(run["id"])

    with pytest.raises(ConflictError):
        runs.create_run(target_id, owner)


def test_new_run_can_start_after_previous_one_finished(runs, running_run):
    runs.mark_succeeded(running_run["id"], reviews_ingested=5)
    target_id = running_run["target_id"]
    owner = _owner_of(runs, target_id)

    second = runs.create_run(target_id, owner)
    assert second["status"] == "QUEUED"


def test_two_simultaneous_creates_give_one_run_and_one_conflict(migrated_database):
    """Two requests at the same moment both pass the friendly pre-check (neither
    can see the other's uncommitted run). The unique index still lets only one
    through, and the other caller gets a ConflictError (409), not a crash."""
    setup = connect(migrated_database)
    first, second = connect(migrated_database), connect(migrated_database)
    try:
        owner = setup.execute(
            "insert into auth.users (email) values (%s) returning id",
            (f"race-{uuid.uuid4().hex[:8]}@example.com",),
        ).fetchone()["id"]
        target_id = TargetRepository(setup).create(owner, "Race", "google_maps", URL)[
            "id"
        ]
        setup.commit()

        IngestionRunRepository(first).create_run(target_id, owner)  # not committed

        outcome = {}

        def second_request():
            try:
                IngestionRunRepository(second).create_run(target_id, owner)
                outcome["result"] = "created"
            except ConflictError:
                outcome["result"] = "conflict"

        racer = threading.Thread(target=second_request)
        racer.start()
        time.sleep(0.5)  # the second insert is now waiting on the first one
        first.commit()
        racer.join(timeout=10)

        assert outcome.get("result") == "conflict"
        count = setup.execute(
            "select count(*) as n from public.ingestion_runs where target_id = %s",
            (target_id,),
        ).fetchone()["n"]
        assert count == 1
    finally:
        for c in (first, second):
            c.rollback()
            c.close()
        setup.execute("delete from auth.users where id = %s", (owner,))
        setup.commit()
        setup.close()


# --- stale runs -----------------------------------------------------------------


@pytest.mark.parametrize("move_to_running", [False, True])
def test_stale_active_run_is_failed_as_timeout_and_a_new_run_can_start(
    conn, runs, owned_target, move_to_running
):
    target_id, owner = owned_target
    stuck = runs.create_run(target_id, owner)
    if move_to_running:
        runs.mark_running(stuck["id"])
    _age(conn, stuck["id"], STALE_RUN_AFTER + STALE_RUN_AFTER)

    new = runs.create_run(target_id, owner)

    assert new["status"] == "QUEUED"
    old = conn.execute(
        "select status::text as status, error_code, finished_at "
        "from public.ingestion_runs where id = %s",
        (stuck["id"],),
    ).fetchone()
    assert old["status"] == "FAILED"
    assert old["error_code"] == "TIMEOUT"
    assert old["finished_at"] is not None


def test_recent_active_run_is_not_treated_as_stale(conn, runs, owned_target):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)
    _age(conn, run["id"], STALE_RUN_AFTER / 2)

    with pytest.raises(ConflictError):
        runs.create_run(target_id, owner)


# --- reading and ownership ---------------------------------------------------------


def test_latest_is_none_before_any_ingestion(runs, owned_target):
    target_id, owner = owned_target
    assert runs.latest_for_target(target_id, owner) is None


def test_latest_returns_the_newest_run(conn, runs, owned_target):
    target_id, owner = owned_target
    first = runs.mark_running(runs.create_run(target_id, owner)["id"])
    runs.mark_succeeded(first["id"], reviews_ingested=42)
    _age(conn, first["id"], STALE_RUN_AFTER)

    second = runs.create_run(target_id, owner)
    runs.mark_failed(second["id"], ErrorCode.SOURCE_UNREACHABLE)

    latest = runs.latest_for_target(target_id, owner)
    assert latest["id"] == second["id"]
    assert latest["status"] == "FAILED"


def test_user_b_cannot_create_a_run_on_user_a_target(runs, owned_target, make_user):
    target_id, _owner = owned_target
    with pytest.raises(NotFoundError):
        runs.create_run(target_id, make_user())


def test_user_b_cannot_read_user_a_runs(runs, owned_target, make_user):
    target_id, owner = owned_target
    runs.create_run(target_id, owner)
    with pytest.raises(NotFoundError):
        runs.latest_for_target(target_id, make_user())


def _owner_of(runs, target_id):
    return runs._conn.execute(
        "select owner_id from public.analysis_targets where id = %s", (target_id,)
    ).fetchone()["owner_id"]


# --- duplicates and retry ------------------------------------------------------------


def test_duplicates_are_counted_apart_from_skipped_reviews(runs, running_run):
    done = runs.mark_succeeded(
        running_run["id"], reviews_ingested=55, reviews_rejected=0, reviews_duplicate=23
    )
    # A duplicate is not bad data, so it does not make the run partial.
    assert done["status"] == "COMPLETED"
    assert done["reviews_duplicate"] == 23


def test_a_new_run_is_an_ingestion_job_with_a_queue_time(runs, owned_target):
    target_id, owner = owned_target
    run = runs.create_run(target_id, owner)
    assert run["job_type"] == "INGESTION"
    assert run["queued_at"] is not None
    assert run["started_at"] is None


def test_retry_puts_the_same_failed_run_back_in_the_queue(runs, running_run):
    runs.mark_failed(running_run["id"], ErrorCode.TIMEOUT)

    retried = runs.requeue(running_run["id"])

    assert retried["id"] == running_run["id"]
    assert retried["status"] == "QUEUED"
    assert retried["queued_at"] is not None
    assert retried["started_at"] is None
    assert retried["finished_at"] is None
    assert retried["error_code"] is None


def test_retried_run_keeps_its_stages(conn, runs, running_run):
    conn.execute(
        "insert into public.ingestion_stages (run_id, stage, status) "
        "values (%s, 'INGESTION', 'COMPLETED')",
        (running_run["id"],),
    )
    runs.mark_failed(running_run["id"], ErrorCode.TIMEOUT)
    runs.requeue(running_run["id"])

    stage = conn.execute(
        "select status::text as status from public.ingestion_stages where run_id = %s",
        (running_run["id"],),
    ).fetchone()
    assert stage["status"] == "COMPLETED"


@pytest.mark.parametrize("finish", ["COMPLETED", "PARTIAL"])
def test_only_a_failed_run_can_be_retried(runs, running_run, finish):
    rejected = 2 if finish == "PARTIAL" else 0
    runs.mark_succeeded(
        running_run["id"], reviews_ingested=5, reviews_rejected=rejected
    )
    with pytest.raises(InvalidRunTransitionError):
        runs.requeue(running_run["id"])


def test_retry_is_refused_while_another_run_is_active(
    conn, runs, running_run, owned_target
):
    target_id, owner = owned_target
    runs.mark_failed(running_run["id"], ErrorCode.TIMEOUT)
    other = runs.create_run(target_id, owner)

    with pytest.raises(ConflictError):
        runs.requeue(running_run["id"])
    # The savepoint kept the transaction usable, and neither run changed.
    statuses = conn.execute(
        "select id, status::text as status from public.ingestion_runs "
        "where target_id = %s",
        (target_id,),
    ).fetchall()
    assert {r["id"]: r["status"] for r in statuses} == {
        running_run["id"]: "FAILED",
        other["id"]: "QUEUED",
    }
