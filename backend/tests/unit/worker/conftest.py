"""S1-020: the ingestion worker's steps, without a database or network.

The real SerpApi adapter answers from the two recorded Smashburger pages through
a fake transport. The repositories are small in-memory fakes that write every
call to one shared event log, so the tests can check the order of steps.
The same flow against a real Postgres is in tests/db/test_ingestion_worker.py.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.adapters.serpapi import SerpApiReviewSource
from app.repositories.ingestion_runs import ErrorCode
from app.repositories.ingestion_stages import StageStatus
from app.repositories.targets import DuplicatePlaceError
from app.worker import Pipeline

FIXTURES = Path(__file__).resolve().parents[3] / "app" / "fixtures"
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
