"""S1-020: whole worker runs against a real Postgres, including retries."""

import httpx

from app.repositories.ingestion_raw_pages import RawPageRepository
from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.targets import TargetRepository
from app.services.normalize_reviews import scrub_page
from app.worker import process_next_run
from tests.db.ingestion_worker.conftest import (
    DATA_ID,
    PAGE_1,
    PAGE_2,
    PLACE_URL,
    FakeReviewStore,
    make_pipeline,
    run_row,
    serpapi,
    stage_rows,
)


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
