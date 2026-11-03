"""Database tests run against a THROWAWAY Postgres, never the real Supabase project.

Set TEST_DATABASE_URL to enable them, for example:
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
Without it these tests are skipped, so `pytest` works on any machine.
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from app.db import connect
from app.db.migrate import apply_migrations

STUB_SQL = Path(__file__).with_name("supabase_stub.sql")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; skipping database tests")
    if "supabase.co" in url:
        pytest.fail(
            "TEST_DATABASE_URL points at Supabase. Use a throwaway local database."
        )
    return url


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> str:
    with connect(test_database_url) as conn:
        has_auth_users = conn.execute(
            "select to_regclass('auth.users') as t"
        ).fetchone()["t"]
        if has_auth_users is None:
            conn.execute(STUB_SQL.read_text(encoding="utf-8"))
            conn.commit()
        apply_migrations(conn)
    return test_database_url


@pytest.fixture
def conn(migrated_database: str):
    """A connection whose work is rolled back after every test."""
    connection = connect(migrated_database)
    yield connection
    connection.rollback()
    connection.close()


@pytest.fixture
def make_user(conn: psycopg.Connection):
    """Create a Supabase-style account; the trigger mirrors it into public.users."""

    def _make(email: str | None = None) -> uuid.UUID:
        email = email or f"{uuid.uuid4().hex[:8]}@example.com"
        return conn.execute(
            "insert into auth.users (email) values (%s) returning id", (email,)
        ).fetchone()["id"]

    return _make
