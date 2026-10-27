"""S1-014: the schema keeps data owned, consistent and closed to the public Data API."""

import psycopg
import pytest

from app.db.migrate import apply_migrations

APP_TABLES = [
    "users",
    "analysis_targets",
    "ingestion_runs",
    "reviews",
    "ingestion_rejections",
]


def _target(conn, owner_id) -> str:
    return conn.execute(
        "insert into public.analysis_targets (owner_id, name, platform, source_url) "
        "values (%s, 'Cafe', 'google_maps', 'https://www.google.com/maps/place/x') returning id",
        (owner_id,),
    ).fetchone()["id"]


def _run(conn, target_id, status="succeeded") -> str:
    finished = "now()" if status != "running" else "null"
    return conn.execute(
        f"insert into public.ingestion_runs (target_id, status, finished_at) "
        f"values (%s, %s, {finished}) returning id",
        (target_id, status),
    ).fetchone()["id"]


def _review(conn, target_id, run_id, **overrides):
    values = {"rating": 5, "review_text": "Great", "source_review_id": None} | overrides
    return conn.execute(
        "insert into public.reviews (target_id, run_id, rating, review_text, source_review_id) "
        "values (%s, %s, %s, %s, %s) returning id",
        (
            target_id,
            run_id,
            values["rating"],
            values["review_text"],
            values["source_review_id"],
        ),
    ).fetchone()["id"]


@pytest.mark.parametrize("table", APP_TABLES)
def test_every_app_table_has_row_level_security_enabled(conn, table):
    row = conn.execute(
        "select relrowsecurity from pg_class where oid = %s::regclass",
        (f"public.{table}",),
    ).fetchone()
    assert row["relrowsecurity"] is True


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", APP_TABLES)
def test_public_api_roles_hold_no_table_privileges(conn, role, table):
    for privilege in ("select", "insert", "update", "delete"):
        granted = conn.execute(
            "select has_table_privilege(%s, %s, %s) as ok",
            (role, f"public.{table}", privilege),
        ).fetchone()["ok"]
        assert granted is False, f"{role} has {privilege} on {table}"


def test_anon_role_cannot_read_targets(conn):
    conn.execute("set local role anon")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from public.analysis_targets")


def test_signing_up_creates_a_users_row(conn, make_user):
    user_id = make_user("a@example.com")
    row = conn.execute(
        "select email from public.users where id = %s", (user_id,)
    ).fetchone()
    assert row == {"email": "a@example.com"}


def test_blank_target_name_is_rejected(conn, make_user):
    owner = make_user()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into public.analysis_targets (owner_id, name, platform, source_url) "
            "values (%s, '   ', 'google_maps', 'https://x')",
            (owner,),
        )


def test_target_requires_an_existing_owner(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "insert into public.analysis_targets (owner_id, name, platform, source_url) "
            "values (gen_random_uuid(), 'Cafe', 'google_maps', 'https://x')"
        )


def test_updating_a_target_refreshes_updated_at(conn, make_user):
    target = _target(conn, make_user())
    # now() is frozen inside a transaction, so backdate the row to make the change visible.
    conn.execute(
        "update public.analysis_targets set updated_at = now() - interval '1 day' where id = %s",
        (target,),
    )
    conn.execute(
        "update public.analysis_targets set name = 'Renamed' where id = %s", (target,)
    )
    fresh = conn.execute(
        "select updated_at > now() - interval '1 minute' as fresh "
        "from public.analysis_targets where id = %s",
        (target,),
    ).fetchone()["fresh"]
    assert fresh is True


def test_review_rating_must_be_between_1_and_5(conn, make_user):
    target = _target(conn, make_user())
    run = _run(conn, target)
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, target, run, rating=6)


def test_review_text_cannot_be_blank(conn, make_user):
    target = _target(conn, make_user())
    run = _run(conn, target)
    with pytest.raises(psycopg.errors.CheckViolation):
        _review(conn, target, run, review_text="  ")


def test_review_cannot_point_at_another_targets_run(conn, make_user):
    owner = make_user()
    target_a, target_b = _target(conn, owner), _target(conn, owner)
    run_b = _run(conn, target_b)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _review(conn, target_a, run_b)


def test_same_source_review_cannot_be_stored_twice_for_one_target(conn, make_user):
    target = _target(conn, make_user())
    run = _run(conn, target)
    _review(conn, target, run, source_review_id="r1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _review(conn, target, run, source_review_id="r1")


def test_running_run_cannot_have_a_finish_time(conn, make_user):
    target = _target(conn, make_user())
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into public.ingestion_runs (target_id, status, finished_at) "
            "values (%s, 'running', now())",
            (target,),
        )


def test_deleting_a_target_deletes_its_runs_reviews_and_rejections(conn, make_user):
    target = _target(conn, make_user())
    run = _run(conn, target)
    _review(conn, target, run)
    conn.execute(
        "insert into public.ingestion_rejections (run_id, reason) values (%s, 'missing text')",
        (run,),
    )
    conn.execute("delete from public.analysis_targets where id = %s", (target,))
    for table in ("ingestion_runs", "reviews", "ingestion_rejections"):
        count = conn.execute(f"select count(*) as n from public.{table}").fetchone()[
            "n"
        ]
        assert count == 0, table


def test_applying_migrations_twice_changes_nothing(conn):
    assert apply_migrations(conn) == []
