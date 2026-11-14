"""The worker's EMBED step, with the real review store and FakeEmbedder.

A finished run's reviews get embeddings. If embedding fails, the run finishes
exactly as it would have (its reviews saved), and embed_missing (the backfill)
fills them later.
Skipped without TEST_DATABASE_URL.
"""

from app.repositories.reviews import ReviewRepository
from app.services.embed_reviews import embed_missing
from app.worker import process_next_run
from tests.conftest import FakeEmbedder
from tests.db.ingestion_worker.conftest import make_pipeline, run_row, serpapi


def _counts(conn, target_id):
    return conn.execute(
        """
        select count(*) as reviews, count(embedding) as embedded
          from public.reviews where target_id = %s
        """,
        (target_id,),
    ).fetchone()


def test_a_finished_run_embeds_its_reviews(conn, target, queued_run):
    open_source, _ = serpapi()

    process_next_run(make_pipeline(conn, open_source, ReviewRepository(conn)))

    counts = _counts(conn, target["id"])
    assert counts["reviews"] > 0
    assert counts["embedded"] == counts["reviews"]


def test_a_failed_embedding_never_fails_the_run_and_the_backfill_recovers(
    conn, target, queued_run
):
    open_source, _ = serpapi()
    broken = FakeEmbedder(broken=True)

    process_next_run(make_pipeline(conn, open_source, ReviewRepository(conn), broken))

    # The usual outcome for this recording (12 rating-only records are rejected).
    assert run_row(conn, queued_run["id"])["status"] == "PARTIAL"
    assert _counts(conn, target["id"])["embedded"] == 0

    embed_missing(ReviewRepository(conn), FakeEmbedder(), lambda: None, lambda: None)

    counts = _counts(conn, target["id"])
    assert counts["embedded"] == counts["reviews"]
