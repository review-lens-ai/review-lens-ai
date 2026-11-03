"""S1-020: a queued run is fetched, normalized, saved and finished."""

from app.repositories.ingestion_stages import Stage, StageStatus
from app.services.normalize_reviews import scrub_page
from app.worker import process_next_run
from tests.unit.worker.conftest import (
    DATA_ID,
    PAGE_1,
    PAGE_2,
    FakeReviews,
    FakeRuns,
    FakeTargets,
    make_pipeline,
)


def test_nothing_queued_does_nothing(log):
    pipeline, google = make_pipeline(log, runs=FakeRuns(log, queued=()))

    assert process_next_run(pipeline) is False
    assert log == ["claim", "commit"]
    assert google.searches == 0


def test_queued_run_is_fetched_normalized_saved_and_finished(log):
    pipeline, google = make_pipeline(log)

    assert process_next_run(pipeline) is True

    assert pipeline.targets.saved_place_id == DATA_ID
    assert [p.page_number for p in pipeline.raw_pages.pages] == [1, 2]
    assert pipeline.raw_pages.stored == [scrub_page(PAGE_1), scrub_page(PAGE_2)]
    assert pipeline.targets.details["title"] == "Smashburger"
    assert pipeline.runs.metadata["pages_fetched"] == 2
    assert pipeline.runs.metadata["stopped_because"] == "CAP_REACHED"
    assert len(pipeline.reviews.reviews) == 18
    assert len(pipeline.reviews.rejected) == 12  # rating-only reviews
    # saved, rejected, duplicates -> mark_succeeded decides COMPLETED / PARTIAL
    assert pipeline.runs.result == ("SUCCEEDED", 18, 12, 0)
    assert pipeline.stages.rows == {
        Stage.INGESTION: StageStatus.COMPLETED,
        Stage.NORMALIZATION: StageStatus.COMPLETED,
    }
    assert google.searches == 2


def test_place_id_is_saved_before_any_serpapi_search(log):
    pipeline, _ = make_pipeline(log)

    process_next_run(pipeline)

    steps = [
        s for s in log if s in ("open short link", "save place id", "serpapi search")
    ]
    assert steps == [
        "open short link",
        "save place id",
        "serpapi search",
        "serpapi search",
    ]


def test_known_place_id_skips_the_lookup(log):
    pipeline, google = make_pipeline(log, targets=FakeTargets(log, place_id=DATA_ID))

    process_next_run(pipeline)

    assert "open short link" not in log
    assert pipeline.targets.saved_place_id is None
    assert google.searches == 2


def test_run_status_is_committed_together_with_normalization(log):
    pipeline, _ = make_pipeline(log)

    process_next_run(pipeline)

    # Saving reviews, finishing the run and completing the stage share one commit,
    # so a crash can never leave NORMALIZATION done but the run still RUNNING.
    assert log[-4:] == [
        "save 18 reviews",
        "mark_succeeded",
        f"complete {Stage.NORMALIZATION}",
        "commit",
    ]


def test_reviews_saved_by_an_earlier_run_count_as_duplicates(log):
    pipeline, _ = make_pipeline(log, reviews=FakeReviews(log, already_saved=3))

    process_next_run(pipeline)

    assert pipeline.runs.result == ("SUCCEEDED", 15, 12, 3)
