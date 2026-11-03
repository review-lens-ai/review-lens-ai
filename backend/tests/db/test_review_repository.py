"""S1-022: reviews persist under the right target and stay behind its owner."""

from datetime import UTC, datetime

import psycopg
import pytest

from app.core.errors import NotFoundError
from app.repositories.ingestion_runs import InvalidRunTransitionError
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.normalize_reviews import RejectedRecord, RejectionReason

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def repo(conn):
    return ReviewRepository(conn)


@pytest.fixture
def make_target(conn):
    def _make(owner_id, name="Joe's Pizza") -> dict:
        return TargetRepository(conn).create(owner_id, name, "google_maps", URL)

    return _make


@pytest.fixture
def make_run(conn):
    """A run in the given state. RUNNING by default: reviews can only be saved
    while a run is running, as in the worker. A target has at most one QUEUED or
    RUNNING run, so finish_run the current one before starting another. The
    schema requires a start time unless QUEUED, at least one ingested review for
    COMPLETED, and an error code for FAILED."""

    def _make(target_id, status="RUNNING"):
        now = datetime.now(UTC)
        return conn.execute(
            """
            insert into public.ingestion_runs
                (target_id, status, started_at, finished_at, reviews_ingested, error_code)
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                target_id,
                status,
                None if status == "QUEUED" else now,
                now if status in ("COMPLETED", "FAILED") else None,
                1 if status == "COMPLETED" else 0,
                "UNEXPECTED" if status == "FAILED" else None,
            ),
        ).fetchone()["id"]

    return _make


@pytest.fixture
def finish_run(conn):
    """Finish a RUNNING run as COMPLETED, as the worker does after saving."""

    def _finish(run_id):
        conn.execute(
            """
            update public.ingestion_runs
               set status = 'COMPLETED', finished_at = now(), reviews_ingested = 1
             where id = %s
            """,
            (run_id,),
        )

    return _finish


def _rejection_count(conn, run_id) -> int:
    return conn.execute(
        "select count(*) as n from public.ingestion_rejections where run_id = %s",
        (run_id,),
    ).fetchone()["n"]


def _review(text="Great slice", rating=5, **fields) -> ReviewCreate:
    return ReviewCreate(
        rating=rating, review_text=text, mapping_version="serpapi@1", **fields
    )


def test_saved_review_text_and_rating_match_the_normalized_input(
    repo, make_user, make_target, make_run
):
    owner = make_user()
    target = make_target(owner)
    run_id = make_run(target["id"])
    review = _review(
        "Crust was perfect",
        4,
        source_review_id="r1",
        source_url="https://example.com/r1",
        source_metadata={"likes": 3},
    )

    assert repo.bulk_insert(run_id, target["id"], owner, [review]) == 1

    [saved] = repo.list_for_target(target["id"], owner)
    assert saved["review_text"] == "Crust was perfect"
    assert saved["rating"] == 4
    assert saved["mapping_version"] == "serpapi@1"
    assert saved["source_review_id"] == "r1"
    assert saved["source_url"] == "https://example.com/r1"
    assert saved["source_metadata"] == {"likes": 3}


def test_saved_review_is_linked_to_its_target_and_run(
    repo, make_user, make_target, make_run
):
    owner = make_user()
    target = make_target(owner)
    run_id = make_run(target["id"])

    repo.bulk_insert(run_id, target["id"], owner, [_review()])

    [saved] = repo.list_for_target(target["id"], owner)
    assert saved["target_id"] == target["id"]
    assert saved["run_id"] == run_id


def test_target_a_reviews_do_not_appear_under_target_b(
    repo, make_user, make_target, make_run
):
    owner = make_user()
    target_a, target_b = make_target(owner, "A"), make_target(owner, "B")
    repo.bulk_insert(make_run(target_a["id"]), target_a["id"], owner, [_review("A")])
    repo.bulk_insert(make_run(target_b["id"]), target_b["id"], owner, [_review("B")])

    texts_a = [r["review_text"] for r in repo.list_for_target(target_a["id"], owner)]
    texts_b = [r["review_text"] for r in repo.list_for_target(target_b["id"], owner)]
    assert texts_a == ["A"]
    assert texts_b == ["B"]


def test_user_b_cannot_read_reviews_through_user_a_target(
    repo, make_user, make_target, make_run
):
    user_a, user_b = make_user(), make_user()
    target = make_target(user_a)
    repo.bulk_insert(make_run(target["id"]), target["id"], user_a, [_review()])

    with pytest.raises(NotFoundError):
        repo.list_for_target(target["id"], user_b)


def test_user_b_cannot_save_reviews_under_user_a_target(
    conn, repo, make_user, make_target, make_run
):
    user_a, user_b = make_user(), make_user()
    target = make_target(user_a)

    with pytest.raises(NotFoundError):
        repo.bulk_insert(make_run(target["id"]), target["id"], user_b, [_review()])

    assert repo.list_for_target(target["id"], user_a) == []


def test_user_b_cannot_save_rejections_under_user_a_run(
    conn, repo, make_user, make_target, make_run
):
    user_a, user_b = make_user(), make_user()
    target = make_target(user_a)
    run_id = make_run(target["id"])
    record = RejectedRecord(RejectionReason.MISSING_RATING, {"snippet": "ok"})

    with pytest.raises(NotFoundError):
        repo.save_rejections(run_id, target["id"], user_b, [record])

    assert _rejection_count(conn, run_id) == 0


def test_saving_with_another_targets_run_is_not_found(
    repo, make_user, make_target, make_run
):
    owner = make_user()
    target_a, target_b = make_target(owner, "A"), make_target(owner, "B")

    with pytest.raises(NotFoundError):
        repo.bulk_insert(make_run(target_a["id"]), target_b["id"], owner, [_review()])


@pytest.mark.parametrize("status", ["QUEUED", "COMPLETED", "FAILED"])
def test_nothing_can_be_saved_under_a_run_that_is_not_running(
    conn, repo, make_user, make_target, make_run, status
):
    owner = make_user()
    target = make_target(owner)
    run_id = make_run(target["id"], status=status)
    record = RejectedRecord(RejectionReason.MISSING_RATING, {"snippet": "ok"})

    with pytest.raises(InvalidRunTransitionError):
        repo.bulk_insert(run_id, target["id"], owner, [_review()])
    with pytest.raises(InvalidRunTransitionError):
        repo.save_rejections(run_id, target["id"], owner, [record])

    assert repo.list_for_target(target["id"], owner) == []
    assert _rejection_count(conn, run_id) == 0


def test_another_users_finished_run_is_not_found_rather_than_revealing_its_state(
    repo, make_user, make_target, make_run
):
    user_a, user_b = make_user(), make_user()
    target = make_target(user_a)
    run_id = make_run(target["id"], status="COMPLETED")

    with pytest.raises(NotFoundError):
        repo.bulk_insert(run_id, target["id"], user_b, [_review()])


def test_duplicate_source_review_is_skipped_and_the_first_copy_is_kept(
    repo, make_user, make_target, make_run, finish_run
):
    owner = make_user()
    target = make_target(owner)
    first_run = make_run(target["id"])
    repo.bulk_insert(
        first_run, target["id"], owner, [_review("First", source_review_id="r1")]
    )
    finish_run(first_run)
    later_run = make_run(target["id"])

    saved = repo.bulk_insert(
        later_run,
        target["id"],
        owner,
        [
            _review("Second", source_review_id="r1"),
            _review("New", source_review_id="r2"),
        ],
    )

    assert saved == 1
    rows = {r["source_review_id"]: r for r in repo.list_for_target(target["id"], owner)}
    assert rows["r1"]["review_text"] == "First"
    assert rows["r1"]["run_id"] == first_run
    assert rows["r2"]["run_id"] == later_run


def test_same_source_review_can_be_saved_under_two_targets(
    repo, make_user, make_target, make_run
):
    owner = make_user()
    target_a, target_b = make_target(owner, "A"), make_target(owner, "B")
    review = _review(source_review_id="r1")

    assert (
        repo.bulk_insert(make_run(target_a["id"]), target_a["id"], owner, [review]) == 1
    )
    assert (
        repo.bulk_insert(make_run(target_b["id"]), target_b["id"], owner, [review]) == 1
    )


def test_reviews_without_a_source_id_are_never_treated_as_duplicates(
    repo, make_user, make_target, make_run
):
    owner = make_user()
    target = make_target(owner)

    saved = repo.bulk_insert(
        make_run(target["id"]), target["id"], owner, [_review(), _review()]
    )

    assert saved == 2


def test_failed_run_leaves_earlier_reviews_in_place(
    repo, make_user, make_target, make_run, finish_run
):
    owner = make_user()
    target = make_target(owner)
    earlier_run = make_run(target["id"])
    repo.bulk_insert(earlier_run, target["id"], owner, [_review("Earlier")])
    finish_run(earlier_run)

    make_run(target["id"], status="FAILED")

    texts = [r["review_text"] for r in repo.list_for_target(target["id"], owner)]
    assert texts == ["Earlier"]


def test_batch_that_fails_midway_saves_nothing_and_keeps_earlier_reviews(
    repo, make_user, make_target, make_run, finish_run
):
    owner = make_user()
    target = make_target(owner)
    earlier_run = make_run(target["id"])
    repo.bulk_insert(earlier_run, target["id"], owner, [_review("Earlier")])
    finish_run(earlier_run)
    # Passes the schema's min_length but breaks the table's non-blank CHECK.
    batch = [_review("Good"), _review("   ")]

    with pytest.raises(psycopg.errors.CheckViolation):
        repo.bulk_insert(make_run(target["id"]), target["id"], owner, batch)

    texts = [r["review_text"] for r in repo.list_for_target(target["id"], owner)]
    assert texts == ["Earlier"]


def test_rejected_records_are_saved_with_a_reason_and_are_not_reviews(
    conn, repo, make_user, make_target, make_run
):
    owner = make_user()
    target = make_target(owner)
    run_id = make_run(target["id"])
    rejected = [
        RejectedRecord(RejectionReason.MISSING_TEXT, {"rating": 5}),
        RejectedRecord(RejectionReason.NOT_A_RECORD, "not a dict"),
    ]

    repo.save_rejections(run_id, target["id"], owner, rejected)

    rows = conn.execute(
        """
        select reason, raw_record from public.ingestion_rejections
         where run_id = %s order by reason
        """,
        (run_id,),
    ).fetchall()
    assert [(r["reason"], r["raw_record"]) for r in rows] == [
        ("missing review text", {"rating": 5}),
        ("not a review record", "not a dict"),
    ]
    assert repo.list_for_target(target["id"], owner) == []
