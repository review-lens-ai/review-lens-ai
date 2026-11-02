from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class IngestionRunResponse(BaseModel):
    """One ingestion attempt, as the workspace shows it (S1-023)."""

    id: UUID
    target_id: UUID
    status: Literal["running", "succeeded", "partial", "failed"]
    started_at: datetime
    # None while the run is still going.
    finished_at: datetime | None
    reviews_ingested: int
    reviews_rejected: int
    # A short code from ErrorCode when status is "failed"; never raw error text.
    error_code: str | None


class LatestIngestionRun(BaseModel):
    """`run` is None when the target has never been ingested."""

    run: IngestionRunResponse | None
