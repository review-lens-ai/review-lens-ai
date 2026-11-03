"""Database access. FastAPI connects as the `postgres` role (see DATABASE_URL).

That role bypasses Row Level Security, so every query that touches user-owned
data must filter by owner itself. See the RLS notes in
supabase/migrations/20261025120000_create_users.sql.
"""

from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings


def connect(database_url: str | None = None) -> psycopg.Connection:
    """Open a connection that returns rows as dicts.

    prepare_threshold=None turns off server-side prepared statements, which
    Supabase's transaction-mode pooler (port 6543) does not support.
    """
    url = database_url or get_settings().database_url.get_secret_value()
    return psycopg.connect(url, row_factory=dict_row, prepare_threshold=None)


def get_connection() -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one connection per request.

    Leaving the `with` block commits if the request succeeded and rolls back if
    it raised, so a failed request never leaves half-written data.
    """
    with connect() as conn:
        yield conn
