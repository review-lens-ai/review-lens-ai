"""S1-020: the ingestion worker. Picks up QUEUED runs and collects their reviews.

Run it next to the API (scripts/dev.sh starts both):

    cd backend && uv run python -m app.worker

The API only queues work: S1-019's endpoint creates a QUEUED run and answers at
once. This process does the slow part, one run at a time:

    claim the oldest QUEUED run              -> RUNNING        (ingestion_runs)
    INGESTION stage                                            (ingestion_stages)
        find the place, save its id          -> analysis_targets.source_place_id
        fetch pages through SerpApi          (app/adapters/serpapi/)
        save the pages                       -> ingestion_raw_pages
        save place details and run details   -> *.source_metadata
    NORMALIZATION stage
        normalize the stored pages           (app/services/normalize_reviews.py)
        save reviews and rejections          (S1-022's ReviewRepository)
        finish the run                       -> COMPLETED / PARTIAL / FAILED
    EMBED step (after a finished run)
        embed the target's new reviews       -> reviews.embedding
                                                (app/services/embed_reviews.py)

Rules:
- ONE STAGE AT A TIME: a stage is marked COMPLETED only once its data is
  written. A retried run (requeue) skips COMPLETED stages and re-runs the first
  one that is not, from its beginning.
- ALL OR NOTHING: a failed stage's database work is rolled back. NORMALIZATION's
  writes, its COMPLETED mark and the run's final status are committed together.
- KEEP WHAT WE PAID FOR: pages fetched before a provider failure are still saved.
- NO LEAKED INTERNALS: a failure stores only an ErrorCode. Technical detail goes
  to the server log, scrubbed by mark_failed.
- NO CREDITS FOR DUPLICATES: the place id is resolved (free) and saved before any
  SerpApi search, so a place the owner already has fails without spending one.
- EMBEDDINGS NEVER FAIL A RUN: the run is already finished and its reviews saved
  when the EMBED step starts. If it fails, it is logged, and the next run of this
  target or ./scripts/embed-reviews.sh embeds what is left.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.adapters.serpapi import ReviewFetchError, SerpApiReviewSource
from app.core.config import get_settings
from app.db import connect
from app.repositories.ingestion_raw_pages import RawPageRepository
from app.repositories.ingestion_runs import (
    ErrorCode,
    IngestionRunRepository,
    scrub_detail,
)
from app.repositories.ingestion_stages import (
    IngestionStageRepository,
    Stage,
    StageStatus,
)
from app.repositories.targets import DuplicatePlaceError, TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.embed_reviews import (
    Embedder,
    EmbeddingStore,
    FastEmbedder,
    embed_missing,
)
from app.services.normalize_reviews import RejectedRecord, normalize_pages

logger = logging.getLogger(__name__)

# How long to wait before looking again when no run is queued.
POLL_SECONDS = 5


class ReviewStore(EmbeddingStore, Protocol):
    """Saves normalized reviews and their embeddings: S1-022's ReviewRepository
    (app/repositories/reviews.py)."""

    def bulk_insert(
        self,
        run_id: UUID | str,
        target_id: UUID | str,
        owner_id: UUID | str,
        reviews: list[ReviewCreate],
    ) -> int:
        """Save reviews; return how many were new (already-saved ones are skipped)."""
        ...

    def save_rejections(
        self,
        run_id: UUID | str,
        target_id: UUID | str,
        owner_id: UUID | str,
        rejected: list[RejectedRecord],
    ) -> Any: ...


class _MissingReviewStore:
    """Stands in until S1-022's ReviewRepository exists: the NORMALIZATION stage
    fails (UNEXPECTED) instead of reporting reviews it did not save."""

    def bulk_insert(self, *args: Any) -> int:
        raise RuntimeError(
            "Saving reviews (S1-022, app/repositories/reviews.py) is not available."
        )

    def save_rejections(self, *args: Any) -> None:
        raise RuntimeError(
            "Saving reviews (S1-022, app/repositories/reviews.py) is not available."
        )


def _review_store(conn: Any) -> ReviewStore:
    try:
        from app.repositories.reviews import ReviewRepository
    except ImportError:
        logger.warning("S1-022's ReviewRepository is missing; runs cannot finish.")
        return _MissingReviewStore()
    return ReviewRepository(conn)


@dataclass
class Pipeline:
    """What the worker needs, so tests can swap any part.

    `conn` is only used to commit and roll back; the repositories run the SQL.
    """

    conn: Any
    runs: IngestionRunRepository
    stages: IngestionStageRepository
    raw_pages: RawPageRepository
    targets: TargetRepository
    reviews: ReviewStore
    open_source: Callable[[], SerpApiReviewSource]
    max_reviews: int
    embedder: Embedder

    @classmethod
    def for_connection(cls, conn: Any, settings: Any, embedder: Embedder) -> "Pipeline":
        return cls(
            conn=conn,
            runs=IngestionRunRepository(conn),
            stages=IngestionStageRepository(conn),
            raw_pages=RawPageRepository(conn),
            targets=TargetRepository(conn),
            reviews=_review_store(conn),
            open_source=lambda: SerpApiReviewSource.from_settings(settings),
            max_reviews=settings.max_reviews_per_run,
            embedder=embedder,
        )


class _StageFailedError(Exception):
    def __init__(self, error_code: ErrorCode, detail: str):
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


# --- The loop -------------------------------------------------------------------


def process_next_run(pipeline: Pipeline) -> bool:
    """Claim and process the oldest QUEUED run. False if nothing was queued."""
    run = pipeline.runs.claim_next_queued()
    pipeline.conn.commit()  # RUNNING is visible to the workspace page at once
    if run is None:
        return False
    process_run(pipeline, run["id"])
    return True


def process_run(pipeline: Pipeline, run_id: UUID | str) -> None:
    """Run a claimed (RUNNING) run's remaining stages and finish it."""
    logger.info("Ingestion run %s started", run_id)
    try:
        target = pipeline.targets.get_for_run(run_id)
        done = pipeline.stages.statuses(run_id)
        if done.get(Stage.INGESTION) != StageStatus.COMPLETED:
            _run_stage(
                pipeline,
                run_id,
                Stage.INGESTION,
                lambda: _ingest(pipeline, run_id, target),
            )
        _run_stage(
            pipeline,
            run_id,
            Stage.NORMALIZATION,
            lambda: _normalize_and_finish(pipeline, run_id, target),
        )
        logger.info("Ingestion run %s finished", run_id)
        _embed(pipeline, run_id, target)
    except _StageFailedError as failure:
        _fail_run(pipeline, run_id, failure.error_code, failure.detail)
    except Exception as error:  # anything outside a stage, e.g. the run vanished
        pipeline.conn.rollback()
        _fail_run(pipeline, run_id, ErrorCode.UNEXPECTED, _describe(error))


def run_forever(poll_seconds: float = POLL_SECONDS) -> None:
    """Process queued runs until stopped (Ctrl+C). A failure in one pass is
    logged and the loop goes on, so one bad run never stops the worker."""
    settings = get_settings()
    embedder = FastEmbedder()  # one per process: the model loads once
    logger.info(
        "Ingestion worker started; checking for queued runs every %ss", poll_seconds
    )
    while True:
        worked = False
        try:
            with connect() as conn:
                worked = process_next_run(
                    Pipeline.for_connection(conn, settings, embedder)
                )
        except Exception as error:
            logger.error("Worker pass failed: %s", scrub_detail(_describe(error)))
        if not worked:
            time.sleep(poll_seconds)


def main() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run_forever()
    except KeyboardInterrupt:
        logger.info("Ingestion worker stopped")


# --- The stages -----------------------------------------------------------------


def _run_stage(
    pipeline: Pipeline, run_id: UUID | str, stage: Stage, work: Callable[[], Any]
) -> Any:
    """Mark the stage RUNNING, do the work, and mark it COMPLETED in the same
    commit as the work's writes. On failure, roll the writes back, mark the
    stage FAILED, and raise _StageFailedError for the run."""
    pipeline.stages.start(run_id, stage)
    pipeline.conn.commit()
    try:
        result = work()
    except ReviewFetchError as error:
        pipeline.conn.rollback()
        if error.pages:
            pipeline.raw_pages.replace_for_run(run_id, error.pages)
        code, detail = error.error_code, str(error)
    except DuplicatePlaceError:
        pipeline.conn.rollback()
        code = ErrorCode.SOURCE_UNREACHABLE
        detail = "The owner already has another target for this place."
    except Exception as error:
        pipeline.conn.rollback()
        code, detail = ErrorCode.UNEXPECTED, _describe(error)
    else:
        pipeline.stages.complete(run_id, stage)
        pipeline.conn.commit()
        return result
    pipeline.stages.fail(run_id, stage, code)
    pipeline.conn.commit()
    raise _StageFailedError(code, f"{stage} stage: {detail}")


def _ingest(pipeline: Pipeline, run_id: UUID | str, target: dict) -> None:
    """INGESTION: find the place, fetch and store its pages."""
    with pipeline.open_source() as source:
        place_id = target["source_place_id"]
        if not place_id:
            place_id = source.resolve_place(target["source_url"])
            pipeline.targets.save_place_id(target["id"], target["owner_id"], place_id)
            pipeline.conn.commit()  # remembered even if fetching fails below
        result = source.fetch_reviews(
            target["source_url"], pipeline.max_reviews, data_id=place_id
        )
    pipeline.raw_pages.replace_for_run(run_id, result.pages)
    if result.place_details:
        pipeline.targets.save_place_details(
            target["id"], target["owner_id"], result.place_details
        )
    pipeline.runs.record_source_metadata(run_id, result.run_details)
    logger.info(
        "Run %s fetched %s reviews in %s searches (%s)",
        run_id,
        result.reviews_fetched,
        result.searches_used,
        result.stopped_because,
    )


def _normalize_and_finish(pipeline: Pipeline, run_id: UUID | str, target: dict) -> None:
    """NORMALIZATION: turn the stored pages into reviews, save them, and finish
    the run with the counts (COMPLETED, PARTIAL, or FAILED if nothing saved)."""
    result = normalize_pages(pipeline.raw_pages.payloads_for_run(run_id))
    saved = pipeline.reviews.bulk_insert(
        run_id, target["id"], target["owner_id"], result.reviews
    )
    pipeline.reviews.save_rejections(
        run_id, target["id"], target["owner_id"], result.rejected
    )
    # Reviews an earlier run already saved are skipped by bulk_insert; they are
    # duplicates, so fetched = saved + rejected + duplicates still holds.
    duplicates = result.duplicates + (len(result.reviews) - saved)
    pipeline.runs.mark_succeeded(run_id, saved, len(result.rejected), duplicates)


def _embed(pipeline: Pipeline, run_id: UUID | str, target: dict) -> None:
    """EMBED: embed the target's reviews that have none yet. The run is already
    finished, so a failure is only logged; nothing here can fail the run."""
    try:
        count = embed_missing(
            pipeline.reviews, pipeline.embedder, pipeline.conn.commit, target["id"]
        )
        logger.info("Run %s embedded %s reviews", run_id, count)
    except Exception as error:
        pipeline.conn.rollback()
        logger.warning(
            "Run %s could not embed its reviews; ./scripts/embed-reviews.sh will: %s",
            run_id,
            scrub_detail(_describe(error)),
        )


def _fail_run(
    pipeline: Pipeline, run_id: UUID | str, error_code: ErrorCode, detail: str
) -> None:
    try:
        pipeline.runs.mark_failed(run_id, error_code, detail=detail)
        pipeline.conn.commit()
    except Exception as error:
        pipeline.conn.rollback()
        logger.error(
            "Could not mark run %s as failed: %s",
            run_id,
            scrub_detail(_describe(error)),
        )


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


if __name__ == "__main__":
    main()
