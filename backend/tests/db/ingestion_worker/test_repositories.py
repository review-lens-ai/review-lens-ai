"""S1-020: the repository methods the worker relies on."""

import pytest

from app.adapters.serpapi import RawPage
from app.core.errors import NotFoundError
from app.repositories.ingestion_raw_pages import RawPageRepository
from app.repositories.ingestion_runs import ErrorCode, IngestionRunRepository
from app.repositories.ingestion_stages import (
    IngestionStageRepository,
    Stage,
    StageStatus,
)
from app.repositories.targets import DuplicatePlaceError, TargetRepository
from tests.db.ingestion_worker.conftest import (
    DATA_ID,
    PLACE_URL,
    run_row,
    stage_rows,
)


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
