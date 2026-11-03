"""S1-020: a retried run skips the stages it already completed."""

from app.repositories.ingestion_stages import Stage, StageStatus
from app.services.normalize_reviews import scrub_page
from app.worker import process_next_run
from tests.unit.worker.conftest import (
    PAGE_1,
    PAGE_2,
    FakeRawPages,
    FakeStages,
    make_pipeline,
)


def test_retry_skips_completed_ingestion_and_spends_no_searches(log):
    def no_provider():
        raise AssertionError("a completed INGESTION stage must not fetch again")

    pipeline, _ = make_pipeline(
        log,
        stages=FakeStages(log, {Stage.INGESTION: StageStatus.COMPLETED}),
        raw_pages=FakeRawPages(log, stored=[scrub_page(PAGE_1), scrub_page(PAGE_2)]),
        open_source=no_provider,
    )

    process_next_run(pipeline)

    assert f"start {Stage.INGESTION}" not in log
    assert pipeline.runs.result == ("SUCCEEDED", 18, 12, 0)
