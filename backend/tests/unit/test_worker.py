"""S1-020: the ingestion worker's steps, without a database or network.

The real SerpApi adapter answers from the two recorded Smashburger pages through
a fake transport. The repositories are small in-memory fakes that write every
call to one shared event log, so the tests can check the order of steps.
The same flow against a real Postgres is in tests/db/test_ingestion_worker.py.
"""

import json
import logging
from pathlib import Path

import httpx
import pytest

from app.adapters.serpapi import (
    ReviewProviderConfigError,
    SerpApiReviewSource,
    scrub_page,
)
from app.repositories.ingestion_runs import ErrorCode
from app.repositories.ingestion_stages import Stage, StageStatus
from app.repositories.targets import DuplicatePlaceError
from app.worker import Pipeline, _MissingReviewStore, process_next_run

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "fixtures"
PAGE_1 = json.loads(
    (FIXTURES / "serpapi_reviews_with_missing_text.json").read_text(encoding="utf-8")
)
PAGE_2 = json.loads(
    (FIXTURES / "serpapi_reviews_next_page.json").read_text(encoding="utf-8")
)
TOKEN_AFTER_PAGE_1 = PAGE_1["serpapi_pagination"]["next_page_token"]

API_KEY = "test-key-0123456789abcdef"
DATA_ID = "0x89c2537da9b3163d:0x2be30fcff506fec8"
SHORT_URL = "https://maps.app.goo.gl/AbC123xyz"
PLACE_URL = f"https://www.google.com/maps/place/Smashburger/data=!4m2!3m1!1s{DATA_ID}"
RUN_ID = "run-1"


# --- Fakes ----------------------------------------------------------------------


class FakeGoogleAndSerpApi:
    """The short link redirects to the place; SerpApi serves the two pages."""

    def __init__(self, log, page_2=None):
        self.log = log
        self.page_2 = page_2 or httpx.Response(200, json=PAGE_2)
        self.searches = 0

    def __call__(self, request):
        if request.url.host == "maps.app.goo.gl":
            self.log.append("open short link")
            return httpx.Response(302, headers={"location": PLACE_URL})
        self.searches += 1
        self.log.append("serpapi search")
        if request.url.params.get("next_page_token") == TOKEN_AFTER_PAGE_1:
            if isinstance(self.page_2, Exception):
                raise self.page_2
            return self.page_2
        return httpx.Response(200, json=PAGE_1)


class FakeConn:
    def __init__(self, log):
        self.log = log

    def commit(self):
        self.log.append("commit")

    def rollback(self):
        self.log.append("rollback")


class FakeRuns:
    def __init__(self, log, queued=(RUN_ID,)):
        self.log = log
        self.queued = list(queued)
        self.metadata = None
        self.result = None

    def claim_next_queued(self):
        self.log.append("claim")
        return {"id": self.queued.pop(0)} if self.queued else None

    def record_source_metadata(self, run_id, details):
        self.metadata = details

    def mark_succeeded(self, run_id, saved, rejected=0, duplicates=0):
        self.log.append("mark_succeeded")
        self.result = ("SUCCEEDED", saved, rejected, duplicates)

    def mark_failed(self, run_id, error_code, detail=None):
        self.log.append("mark_failed")
        self.result = ("FAILED", ErrorCode(error_code))
        self.detail = detail


class FakeStages:
    def __init__(self, log, statuses=None):
        self.log = log
        self.rows = dict(statuses or {})

    def statuses(self, run_id):
        return dict(self.rows)

    def start(self, run_id, stage):
        self.rows[stage] = StageStatus.RUNNING
        self.log.append(f"start {stage}")

    def complete(self, run_id, stage):
        self.rows[stage] = StageStatus.COMPLETED
        self.log.append(f"complete {stage}")

    def fail(self, run_id, stage, error_code):
        self.rows[stage] = StageStatus.FAILED
        self.log.append(f"fail {stage} {error_code}")


class FakeRawPages:
    def __init__(self, log, stored=()):
        self.log = log
        self.pages = None
        self.stored = list(stored)

    def replace_for_run(self, run_id, pages):
        self.log.append(f"save {len(pages)} raw pages")
        self.pages = list(pages)
        self.stored = [page.payload for page in pages]

    def payloads_for_run(self, run_id):
        return list(self.stored)


class FakeTargets:
    def __init__(self, log, place_id=None, duplicate=False, missing=False):
        self.log = log
        self.place_id = place_id
        self.duplicate = duplicate
        self.missing = missing
        self.saved_place_id = None
        self.details = None

    def get_for_run(self, run_id):
        if self.missing:
            raise RuntimeError("run vanished")
        return {
            "id": "target-1",
            "owner_id": "owner-1",
            "platform": "google_maps",
            "source_url": SHORT_URL,
            "source_place_id": self.place_id,
        }

    def save_place_id(self, target_id, owner_id, place_id):
        if self.duplicate:
            raise DuplicatePlaceError()
        self.log.append("save place id")
        self.saved_place_id = place_id

    def save_place_details(self, target_id, owner_id, details):
        self.details = details


class FakeReviews:
    def __init__(self, log, already_saved=0, broken=False):
        self.log = log
        self.already_saved = already_saved
        self.broken = broken
        self.reviews = None
        self.rejected = None

    def bulk_insert(self, run_id, target_id, owner_id, reviews):
        if self.broken:
            raise RuntimeError("database went away")
        self.log.append(f"save {len(reviews)} reviews")
        self.reviews = list(reviews)
        return len(reviews) - self.already_saved

    def save_rejections(self, run_id, target_id, owner_id, rejected):
        self.rejected = list(rejected)


@pytest.fixture
def log():
    return []


def make_pipeline(
    log,
    *,
    google=None,
    targets=None,
    stages=None,
    raw_pages=None,
    reviews=None,
    runs=None,
    open_source=None,
    max_reviews=30,
):
    google = google or FakeGoogleAndSerpApi(log)
    pipeline = Pipeline(
        conn=FakeConn(log),
        runs=runs or FakeRuns(log),
        stages=stages or FakeStages(log),
        raw_pages=raw_pages or FakeRawPages(log),
        targets=targets or FakeTargets(log),
        reviews=reviews or FakeReviews(log),
        open_source=open_source
        or (
            lambda: SerpApiReviewSource(
                API_KEY, httpx.Client(transport=httpx.MockTransport(google))
            )
        ),
        max_reviews=max_reviews,
    )
    return pipeline, google


# --- A normal run ---------------------------------------------------------------


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


# --- Retry ----------------------------------------------------------------------


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


# --- Failures -------------------------------------------------------------------


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
