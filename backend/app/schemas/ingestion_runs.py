from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# Same values as the database enum public.ingestion_status.
IngestionStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "PARTIAL", "FAILED"]


class IngestionRunResponse(BaseModel):
    """One ingestion attempt, as the workspace shows it (S1-023)."""

    id: UUID
    target_id: UUID
    job_type: Literal["INGESTION", "ENRICHMENT"]
    status: IngestionStatus
    # When the run (last) entered the queue.
    queued_at: datetime
    # None while the run waits in the queue, or if it failed before starting.
    started_at: datetime | None
    # None while the run is still going.
    finished_at: datetime | None
    reviews_ingested: int
    reviews_rejected: int
    reviews_duplicate: int
    # A short code from ErrorCode when status is "FAILED"; never raw error text.
    error_code: str | None


# Why a record was skipped: one code per app.services.normalize_reviews.RejectionReason,
# plus OTHER for any stored text we do not recognise. Codes, never raw text.
SkipReasonCode = Literal[
    "NOT_A_RECORD", "MISSING_TEXT", "MISSING_RATING", "INVALID_RATING", "OTHER"
]


class SkippedReason(BaseModel):
    """How many of a run's records were skipped for one reason (S2-007)."""

    reason: SkipReasonCode
    count: int


class LatestIngestionRun(BaseModel):
    """`run` is None when the target has never been ingested.

    `skipped_reasons` explains the run's skipped records, most common reason
    first. It is empty when nothing was skipped. Counts only: the skipped
    records themselves are never sent."""

    run: IngestionRunResponse | None
    skipped_reasons: list[SkippedReason] = []
