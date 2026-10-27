"""Applies the SQL files in supabase/migrations/ to the database in DATABASE_URL.

    ./scripts/migrate.sh             apply every pending migration
    ./scripts/migrate.sh --status    list applied and pending migrations

Every schema change is a committed .sql file applied by this script, never a
click in the Supabase dashboard, so teammates can see exactly what changed.

Applied versions are recorded in supabase_migrations.schema_migrations, the same
table the Supabase CLI uses, so both tools agree on what has run.
"""

import argparse
import logging
import sys
from pathlib import Path

import psycopg

from app.db import connect

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"

_CREATE_TRACKING_TABLE = """
create schema if not exists supabase_migrations;
create table if not exists supabase_migrations.schema_migrations (
    version    text primary key,
    statements text[],
    name       text
);
"""


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Migration files in the order they must run (timestamp prefix sorts)."""
    return sorted(directory.glob("*.sql"))


def _split(path: Path) -> tuple[str, str]:
    version, _, name = path.stem.partition("_")
    return version, name


def applied_versions(conn: psycopg.Connection) -> set[str]:
    conn.execute(_CREATE_TRACKING_TABLE)
    rows = conn.execute("select version from supabase_migrations.schema_migrations")
    return {row["version"] for row in rows}


def apply_migrations(
    conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR
) -> list[str]:
    """Run each pending migration in its own transaction. Returns what was applied."""
    done = applied_versions(conn)
    conn.commit()
    applied: list[str] = []
    for path in migration_files(directory):
        version, name = _split(path)
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.execute(sql)
            conn.execute(
                "insert into supabase_migrations.schema_migrations (version, statements, name) "
                "values (%s, %s, %s)",
                (version, [sql], name),
            )
            conn.commit()
        except psycopg.Error:
            conn.rollback()
            logger.exception("migration %s failed and was rolled back", path.name)
            raise
        applied.append(path.name)
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply database migrations.")
    parser.add_argument(
        "--status", action="store_true", help="list migrations and exit"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        with connect() as conn:
            if args.status:
                done = applied_versions(conn)
                for path in migration_files():
                    mark = "applied" if _split(path)[0] in done else "PENDING"
                    print(f"{mark:8} {path.name}")
                return 0
            applied = apply_migrations(conn)
    except psycopg.Error:
        # Details were logged above; never print the connection string.
        print(
            "Migration failed. The failing migration was rolled back; earlier ones stay applied. "
            "Fix the error above and run the script again.",
            file=sys.stderr,
        )
        return 1

    if applied:
        for name in applied:
            print(f"applied  {name}")
    else:
        print("Database is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
