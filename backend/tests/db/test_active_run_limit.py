"""A target can have only one queued or running ingestion run at a time.

The database enforces it (a partial unique index), so two simultaneous requests
cannot both start a run, whatever the application code does.
"""

import psycopg
import pytest

from tests.db.conftest import insert_run

URL = "https://www.google.com/maps/place/x"
FINAL = ("COMPLETED", "PARTIAL", "FAILED")
ACTIVE = ("QUEUED", "RUNNING")


def _target(conn, owner):
    return conn.execute(
        "insert into public.analysis_targets (owner_id, name, platform, source_url) "
        "values (%s, 'Cafe', 'google_maps', %s) returning id",
        (owner, URL),
    ).fetchone()["id"]


def _run(conn, target, status):
    return insert_run(conn, target, status)


@pytest.mark.parametrize("first", ACTIVE)
@pytest.mark.parametrize("second", ACTIVE)
def test_a_second_active_run_for_the_same_target_is_refused(
    conn, make_user, first, second
):
    target = _target(conn, make_user())
    _run(conn, target, first)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _run(conn, target, second)


def test_a_new_run_defaults_to_queued_and_counts_as_active(conn, make_user):
    target = _target(conn, make_user())
    conn.execute("insert into public.ingestion_runs (target_id) values (%s)", (target,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "insert into public.ingestion_runs (target_id) values (%s)", (target,)
        )


@pytest.mark.parametrize("final", FINAL)
def test_finished_runs_never_block_a_new_run(conn, make_user, final):
    target = _target(conn, make_user())
    _run(conn, target, final)
    _run(conn, target, final)
    assert _run(conn, target, "QUEUED")


@pytest.mark.parametrize("status", ACTIVE)
def test_finishing_the_active_run_frees_the_target(conn, make_user, status):
    target = _target(conn, make_user())
    run = _run(conn, target, status)
    conn.execute(
        "update public.ingestion_runs set status = 'COMPLETED', "
        "started_at = coalesce(started_at, now()), finished_at = now(), "
        "reviews_ingested = 1 where id = %s",
        (run,),
    )
    assert _run(conn, target, "QUEUED")


def test_moving_a_run_from_queued_to_running_is_allowed(conn, make_user):
    target = _target(conn, make_user())
    run = _run(conn, target, "QUEUED")
    conn.execute(
        "update public.ingestion_runs set status = 'RUNNING', started_at = now() "
        "where id = %s",
        (run,),
    )
    status = conn.execute(
        "select status::text as s from public.ingestion_runs where id = %s", (run,)
    ).fetchone()["s"]
    assert status == "RUNNING"


def test_different_targets_can_each_have_an_active_run(conn, make_user):
    owner = make_user()
    first, second = _target(conn, owner), _target(conn, owner)
    _run(conn, first, "RUNNING")
    assert _run(conn, second, "QUEUED")


def test_two_connections_cannot_both_start_a_run_for_one_target(
    conn, make_user, migrated_database
):
    # The real race: both requests pass an application check, then both insert.
    from app.db import connect

    owner, target = make_user(), None
    target = _target(conn, owner)
    conn.commit()
    other_a, other_b = connect(migrated_database), connect(migrated_database)
    try:
        other_a.execute(
            "insert into public.ingestion_runs (target_id) values (%s)", (target,)
        )
        # B's insert waits on A's uncommitted row, then fails once A commits.
        import threading

        outcome = {}

        def second_start():
            try:
                other_b.execute(
                    "insert into public.ingestion_runs (target_id) values (%s)",
                    (target,),
                )
                other_b.commit()
                outcome["b"] = "started"
            except psycopg.errors.UniqueViolation:
                other_b.rollback()
                outcome["b"] = "refused"

        thread = threading.Thread(target=second_start)
        thread.start()
        other_a.commit()
        thread.join(timeout=20)
        assert outcome["b"] == "refused"
        active = conn.execute(
            "select count(*) as n from public.ingestion_runs "
            "where target_id = %s and status in ('QUEUED', 'RUNNING')",
            (target,),
        ).fetchone()["n"]
        assert active == 1
    finally:
        other_a.close()
        other_b.close()
        conn.execute("delete from public.analysis_targets where id = %s", (target,))
        conn.execute("delete from auth.users where id = %s", (owner,))
        conn.commit()
