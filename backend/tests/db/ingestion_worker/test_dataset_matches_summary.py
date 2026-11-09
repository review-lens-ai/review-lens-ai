"""S2-007 / S2-BR-006 / S2-BR-007: what the summary says is what Q&A will read.

A whole worker run, against a real Postgres and the real review store, with a fake
SerpApi (no network, no searches spent): 30 records come back, 18 are usable and 12
are rating-only. The ingestion summary, the skipped records and the review dataset
must agree with each other.
"""

from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.reviews import ReviewRepository
from app.worker import process_next_run
from tests.db.ingestion_worker.conftest import make_pipeline, run_row, serpapi


def _run_worker(conn, queued_run, target):
    """Run the queued run to the end and return what the summary and dataset hold."""
    open_source, _calls = serpapi()
    reviews = ReviewRepository(conn)
    process_next_run(make_pipeline(conn, open_source, reviews))
    return {
        "run": run_row(conn, queued_run["id"]),
        # The same two calls the page (count) and the Q&A loader (list) will use.
        "count": reviews.count_for_target(target["id"], target["owner_id"]),
        "dataset": reviews.list_for_target(
            target["id"], target["owner_id"], limit=None
        ),
        "reasons": IngestionRunRepository(conn).skipped_reasons(
            target["id"], target["owner_id"], queued_run["id"]
        ),
    }


def test_the_summary_count_is_the_dataset_q_and_a_will_read(conn, target, queued_run):
    result = _run_worker(conn, queued_run, target)

    assert result["run"]["status"] == "PARTIAL"
    # Three ways to count the same dataset, and they agree.
    assert result["run"]["reviews_ingested"] == 18
    assert result["count"] == 18
    assert len(result["dataset"]) == 18


def test_skipped_records_never_enter_the_dataset(conn, target, queued_run):
    result = _run_worker(conn, queued_run, target)

    in_dataset = {review["source_review_id"] for review in result["dataset"]}
    skipped = {
        row["review_id"]
        for row in conn.execute(
            "select raw_record->>'review_id' as review_id "
            "from public.ingestion_rejections where run_id = %s",
            (queued_run["id"],),
        ).fetchall()
    }
    assert len(skipped) == 12
    assert in_dataset.isdisjoint(skipped)
    # Every record in the dataset has the words Q&A needs.
    assert all(review["review_text"] for review in result["dataset"])


def test_the_skip_reasons_add_up_to_the_skipped_count(conn, target, queued_run):
    result = _run_worker(conn, queued_run, target)

    assert result["run"]["reviews_rejected"] == 12
    assert result["reasons"] == [{"reason": "MISSING_TEXT", "count": 12}]
    assert (
        sum(r["count"] for r in result["reasons"]) == result["run"]["reviews_rejected"]
    )


def test_every_record_is_accounted_for_exactly_once(conn, target, queued_run):
    run = _run_worker(conn, queued_run, target)["run"]

    # fetched = ingested + skipped + duplicate: nothing is lost or counted twice.
    assert (
        run["reviews_ingested"] + run["reviews_rejected"] + run["reviews_duplicate"]
        == 30
    )
