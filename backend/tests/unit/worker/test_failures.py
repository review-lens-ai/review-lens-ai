"""S1-020: every failure ends the run as FAILED with a safe error code."""

import logging

import httpx

from app.adapters.serpapi import ReviewProviderConfigError
from app.repositories.ingestion_runs import ErrorCode
from app.repositories.ingestion_stages import Stage, StageStatus
from app.worker import _MissingReviewStore, process_next_run
from tests.unit.worker.conftest import (
    API_KEY,
    FakeGoogleAndSerpApi,
    FakeReviews,
    FakeTargets,
    make_pipeline,
)


def test_owner_already_having_the_place_fails_before_any_search(log):
    pipeline, google = make_pipeline(log, targets=FakeTargets(log, duplicate=True))

    process_next_run(pipeline)

    assert google.searches == 0
    assert pipeline.stages.rows == {Stage.INGESTION: StageStatus.FAILED}
    assert pipeline.runs.result == ("FAILED", ErrorCode.SOURCE_UNREACHABLE)


def test_provider_failure_keeps_fetched_pages_and_fails_the_run(log):
    timeout = httpx.ReadTimeout("simulated")
    google = FakeGoogleAndSerpApi(log, page_2=timeout)
    pipeline, _ = make_pipeline(log, google=google)

    process_next_run(pipeline)

    assert "rollback" in log
    assert [p.page_number for p in pipeline.raw_pages.pages] == [1]  # paid for, kept
    assert f"fail {Stage.INGESTION} {ErrorCode.TIMEOUT}" in log
    assert Stage.NORMALIZATION not in pipeline.stages.rows
    assert pipeline.runs.result == ("FAILED", ErrorCode.TIMEOUT)


def test_found_place_id_is_committed_before_a_failed_fetch_rolls_back(log):
    timeout = httpx.ReadTimeout("simulated")
    pipeline, _ = make_pipeline(log, google=FakeGoogleAndSerpApi(log, page_2=timeout))

    process_next_run(pipeline)

    after_save = log[log.index("save place id") + 1 :]
    assert after_save[0] == "commit"
    assert after_save.index("commit") < after_save.index("rollback")


def test_used_up_quota_fails_the_run_with_quota_exceeded(log):
    quota = httpx.Response(429, json={"error": "Your account has run out of searches."})
    pipeline, _ = make_pipeline(log, google=FakeGoogleAndSerpApi(log, page_2=quota))

    process_next_run(pipeline)

    assert pipeline.runs.result == ("FAILED", ErrorCode.QUOTA_EXCEEDED)


def test_missing_api_key_fails_the_run_as_unexpected(log):
    def no_key():
        raise ReviewProviderConfigError("REVIEW_PROVIDER_API_KEY is not set.")

    pipeline, _ = make_pipeline(log, open_source=no_key)

    process_next_run(pipeline)

    assert pipeline.runs.result == ("FAILED", ErrorCode.UNEXPECTED)


def test_failed_save_is_rolled_back_and_fails_the_run(log):
    pipeline, _ = make_pipeline(log, reviews=FakeReviews(log, broken=True))

    process_next_run(pipeline)

    assert log.count("rollback") == 1
    assert "mark_succeeded" not in log
    assert pipeline.stages.rows[Stage.NORMALIZATION] == StageStatus.FAILED
    assert pipeline.runs.result == ("FAILED", ErrorCode.UNEXPECTED)


def test_run_fails_instead_of_faking_success_without_s1_022(log):
    pipeline, _ = make_pipeline(log, reviews=_MissingReviewStore())

    process_next_run(pipeline)

    assert "mark_succeeded" not in log
    assert pipeline.runs.result == ("FAILED", ErrorCode.UNEXPECTED)


def test_error_outside_a_stage_still_fails_the_run(log):
    pipeline, _ = make_pipeline(log, targets=FakeTargets(log, missing=True))

    process_next_run(pipeline)

    assert pipeline.runs.result == ("FAILED", ErrorCode.UNEXPECTED)


def test_failure_detail_names_the_stage_but_not_the_api_key(log, caplog):
    caplog.set_level(logging.DEBUG)
    leak = httpx.Response(500, json={"error": f"Bad key {API_KEY}"})
    pipeline, _ = make_pipeline(log, google=FakeGoogleAndSerpApi(log, page_2=leak))

    process_next_run(pipeline)

    assert pipeline.runs.result == ("FAILED", ErrorCode.SOURCE_UNREACHABLE)
    assert pipeline.runs.detail.startswith("INGESTION stage:")
    assert API_KEY not in pipeline.runs.detail
    assert API_KEY not in caplog.text
