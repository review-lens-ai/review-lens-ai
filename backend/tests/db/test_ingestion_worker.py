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

from app.adapters.serpapi import RawPage, SerpApiReviewSource
from app.core.errors import NotFoundError
from app.repositories.ingestion_raw_pages import RawPageRepository
from app.repositories.ingestion_runs import ErrorCode, IngestionRunRepository
from app.repositories.ingestion_stages import (
    IngestionStageRepository,
    Stage,
    StageStatus,
)
from app.repositories.targets import DuplicatePlaceError, TargetRepository
from app.services.normalize_reviews import scrub_page
from app.worker import Pipeline, process_next_run

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "fixtures"
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


# --- The whole run --------------------------------------------------------------


def test_worker_takes_a_queued_run_to_partial(conn, target, queued_run):
    open_source, calls = serpapi()
    store = FakeReviewStore()

    assert process_next_run(make_pipeline(conn, open_source, store)) is True

    run = run_row(conn, queued_run["id"])
    assert run["status"] == "PARTIAL"  # 12 rating-only reviews were rejected
    assert (run["reviews_ingested"], run["reviews_rejected"]) == (18, 12)
    assert run["reviews_duplicate"] == 0
    assert run["error_code"] is None
    assert run["started_at"] is not None and run["finished_at"] is not None
    assert run["source_metadata"]["pages_fetched"] == 2
    assert stage_rows(conn, queued_run["id"]) == {
        "INGESTION": ("COMPLETED", None),
        "NORMALIZATION": ("COMPLETED", None),
    }
    assert RawPageRepository(conn).payloads_for_run(queued_run["id"]) == [
        scrub_page(PAGE_1),
        scrub_page(PAGE_2),
    ]
    saved_target = TargetRepository(conn).get_for_run(queued_run["id"])
    assert saved_target["source_place_id"] == DATA_ID
    assert len(store.saved) == 18
    assert len(calls) == 2


def test_failed_fetch_keeps_page_one_and_a_retry_finishes(conn, target, queued_run):
    runs = IngestionRunRepository(conn)
    timeout = httpx.ReadTimeout("simulated")
    open_source, _ = serpapi(page_2=timeout)

    process_next_run(make_pipeline(conn, open_source))

    run = run_row(conn, queued_run["id"])
    assert (run["status"], run["error_code"]) == ("FAILED", "TIMEOUT")
    assert stage_rows(conn, queued_run["id"]) == {"INGESTION": ("FAILED", "TIMEOUT")}
    assert len(RawPageRepository(conn).payloads_for_run(queued_run["id"])) == 1
    # The place id was saved before the fetch failed.
    assert TargetRepository(conn).get_for_run(queued_run["id"])["source_place_id"]

    runs.requeue(queued_run["id"])
    open_source, _ = serpapi()
    process_next_run(make_pipeline(conn, open_source))

    assert run_row(conn, queued_run["id"])["status"] == "PARTIAL"
    assert len(RawPageRepository(conn).payloads_for_run(queued_run["id"])) == 2


def test_retry_after_failed_normalization_does_not_fetch_again(
    conn, target, queued_run
):
    open_source, calls = serpapi()
    process_next_run(make_pipeline(conn, open_source, FakeReviewStore(broken=True)))

    assert run_row(conn, queued_run["id"])["error_code"] == "UNEXPECTED"
    assert stage_rows(conn, queued_run["id"]) == {
        "INGESTION": ("COMPLETED", None),
        "NORMALIZATION": ("FAILED", "UNEXPECTED"),
    }

    IngestionRunRepository(conn).requeue(queued_run["id"])
    process_next_run(make_pipeline(conn, open_source))

    assert run_row(conn, queued_run["id"])["status"] == "PARTIAL"
    assert len(calls) == 2  # only the first attempt searched


def test_place_the_owner_already_has_fails_without_a_search(conn, target, queued_run):
    targets = TargetRepository(conn)
    other = targets.create(target["owner_id"], "Same place", "google_maps", PLACE_URL)
    targets.save_place_id(other["id"], target["owner_id"], DATA_ID)
    open_source, calls = serpapi()

    process_next_run(make_pipeline(conn, open_source))

    run = run_row(conn, queued_run["id"])
    assert (run["status"], run["error_code"]) == ("FAILED", "SOURCE_UNREACHABLE")
    assert calls == []


# --- Repository pieces ----------------------------------------------------------


def test_claim_next_queued_takes_the_oldest_run_once(conn, queued_run):
    runs = IngestionRunRepository(conn)

    claimed = runs.claim_next_queued()

    assert claimed["id"] == queued_run["id"]
    assert claimed["status"] == "RUNNING"
    again = runs.claim_next_queued()
    assert again is None or again["id"] != queued_run["id"]


def test_record_source_metadata_stores_json(conn, queued_run):
    IngestionRunRepository(conn).record_source_metadata(
        queued_run["id"], {"sort_by": "newestFirst", "pages_fetched": 2}
    )
    assert run_row(conn, queued_run["id"])["source_metadata"] == {
        "sort_by": "newestFirst",
        "pages_fetched": 2,
    }


def test_stages_record_progress_and_a_retry_clears_the_failure(conn, queued_run):
    stages = IngestionStageRepository(conn)
    run_id = queued_run["id"]
    assert stages.statuses(run_id) == {}

    stages.start(run_id, Stage.INGESTION)
    stages.fail(run_id, Stage.INGESTION, ErrorCode.TIMEOUT)
    assert stage_rows(conn, run_id) == {"INGESTION": ("FAILED", "TIMEOUT")}

    stages.start(run_id, Stage.INGESTION)
    stages.complete(run_id, Stage.INGESTION)
    assert stages.statuses(run_id) == {Stage.INGESTION: StageStatus.COMPLETED}


def test_a_stage_must_start_before_it_completes(conn, queued_run):
    with pytest.raises(ValueError):
        IngestionStageRepository(conn).complete(queued_run["id"], Stage.NORMALIZATION)


def test_raw_pages_are_replaced_and_read_back_in_order(conn, queued_run):
    pages = RawPageRepository(conn)
    run_id = queued_run["id"]
    pages.replace_for_run(run_id, [RawPage(1, {"old": True}, "t1")])

    pages.replace_for_run(
        run_id, [RawPage(2, {"page": 2}, None), RawPage(1, {"page": 1}, "t1")]
    )

    assert pages.payloads_for_run(run_id) == [{"page": 1}, {"page": 2}]


def test_get_for_run_finds_the_target_and_its_owner(conn, target, queued_run):
    found = TargetRepository(conn).get_for_run(queued_run["id"])
    assert (found["id"], found["owner_id"]) == (target["id"], target["owner_id"])


def test_get_for_run_of_a_missing_run_is_not_found(conn):
    with pytest.raises(NotFoundError):
        TargetRepository(conn).get_for_run("00000000-0000-0000-0000-000000000000")


def test_duplicate_place_id_is_refused_and_the_transaction_survives(conn, target):
    targets = TargetRepository(conn)
    owner = target["owner_id"]
    other = targets.create(owner, "Same place", "google_maps", PLACE_URL)
    targets.save_place_id(target["id"], owner, DATA_ID)

    with pytest.raises(DuplicatePlaceError):
        targets.save_place_id(other["id"], owner, DATA_ID)

    assert targets.get_owned(other["id"], owner)["id"] == other["id"]


def test_another_owner_can_have_the_same_place(conn, make_user, target):
    targets = TargetRepository(conn)
    stranger = make_user()
    theirs = targets.create(stranger, "Smashburger", "google_maps", PLACE_URL)

    targets.save_place_id(target["id"], target["owner_id"], DATA_ID)
    targets.save_place_id(theirs["id"], stranger, DATA_ID)  # no error


def test_place_details_need_the_owner(conn, make_user, target):
    targets = TargetRepository(conn)
    targets.save_place_details(target["id"], target["owner_id"], {"title": "S"})

    with pytest.raises(NotFoundError):
        targets.save_place_details(target["id"], make_user(), {"title": "S"})
