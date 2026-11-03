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


class LatestIngestionRun(BaseModel):
    """`run` is None when the target has never been ingested."""

    run: IngestionRunResponse | None
