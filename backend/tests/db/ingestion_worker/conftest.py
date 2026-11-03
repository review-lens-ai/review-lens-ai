"""S1-020: the ingestion worker against a real Postgres.

SerpApi is faked (the two recorded Smashburger pages); everything else is real:
the migrations, the repositories and the worker. S1-022's review store is a
small fake until its ReviewRepository lands.

The worker commits between steps. Here its "commit" and "rollback" move between
savepoints instead, so each test still runs in one transaction that the `conn`
fixture rolls back, and the test database stays clean.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.adapters.serpapi import SerpApiReviewSource
from app.repositories.ingestion_raw_pages import RawPageRepository
from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.ingestion_stages import (
    IngestionStageRepository,
)
from app.repositories.targets import TargetRepository
from app.worker import Pipeline

FIXTURES = Path(__file__).resolve().parents[3] / "app" / "fixtures"
PAGE_1 = json.loads(
    (FIXTURES / "serpapi_reviews_with_missing_text.json").read_text(encoding="utf-8")
)
PAGE_2 = json.loads(
    (FIXTURES / "serpapi_reviews_next_page.json").read_text(encoding="utf-8")
)
TOKEN_AFTER_PAGE_1 = PAGE_1["serpapi_pagination"]["next_page_token"]

DATA_ID = "0x89c2537da9b3163d:0x2be30fcff506fec8"
PLACE_URL = f"https://www.google.com/maps/place/Smashburger/data=!4m2!3m1!1s{DATA_ID}"


class SavepointConn:
    """Stands in for the connection's commit/rollback inside one test transaction."""

    def __init__(self, conn):
        self._conn = conn
        self._count = 0
        self._open()

    def _open(self):
        self._count += 1
        self._name = f"worker_step_{self._count}"
        self._conn.execute(f"savepoint {self._name}")

    def commit(self):
        self._conn.execute(f"release savepoint {self._name}")
        self._open()

    def rollback(self):
        self._conn.execute(f"rollback to savepoint {self._name}")


class FakeReviewStore:
    def __init__(self, broken=False):
        self.broken = broken
        self.saved = []
        self.rejected = []

    def bulk_insert(self, run_id, target_id, owner_id, reviews):
        if self.broken:
            raise RuntimeError("simulated save failure")
        self.saved = list(reviews)
        return len(reviews)

    def save_rejections(self, run_id, target_id, owner_id, rejected):
        self.rejected = list(rejected)


def serpapi(page_2=None):
    """Fake SerpApi: page 1, then page 2 (or the given failure)."""
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.params.get("next_page_token") == TOKEN_AFTER_PAGE_1:
            if isinstance(page_2, Exception):
                raise page_2
            return page_2 or httpx.Response(200, json=PAGE_2)
        return httpx.Response(200, json=PAGE_1)

    def open_source():
        client = httpx.Client(transport=httpx.MockTransport(handle))
        return SerpApiReviewSource("test-key-0123456789abcdef", client)

    return open_source, calls


@pytest.fixture
def target(conn, make_user):
    owner = make_user()
    created = TargetRepository(conn).create(
        owner, "Smashburger", "google_maps", PLACE_URL
    )
    # create() returns the API view of a target, which leaves out owner_id.
    return {**created, "owner_id": owner}


@pytest.fixture
def queued_run(conn, target):
    run = IngestionRunRepository(conn).create_run(target["id"], target["owner_id"])
    # Oldest in the queue, so the worker picks this run and no other.
    conn.execute(
        "update public.ingestion_runs set queued_at = now() - interval '100 years' "
        "where id = %s",
        (run["id"],),
    )
    return run


def make_pipeline(conn, open_source, reviews=None):
    return Pipeline(
        conn=SavepointConn(conn),
        runs=IngestionRunRepository(conn),
        stages=IngestionStageRepository(conn),
        raw_pages=RawPageRepository(conn),
        targets=TargetRepository(conn),
        reviews=reviews or FakeReviewStore(),
        open_source=open_source,
        max_reviews=30,
    )


def run_row(conn, run_id):
    return conn.execute(
        "select status::text as status, error_code, reviews_ingested, "
        "reviews_rejected, reviews_duplicate, source_metadata, started_at, finished_at "
        "from public.ingestion_runs where id = %s",
        (run_id,),
    ).fetchone()


def stage_rows(conn, run_id):
    rows = conn.execute(
        "select stage::text as stage, status::text as status, error_code "
        "from public.ingestion_stages where run_id = %s",
        (run_id,),
    ).fetchall()
    return {row["stage"]: (row["status"], row["error_code"]) for row in rows}
