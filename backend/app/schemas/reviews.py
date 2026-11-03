"""S1-021: the canonical Review that normalization produces and S1-022 saves.

Mirrors the reviews table (supabase/migrations/20261025120300_create_reviews.sql,
updated by 20261102140300_update_reviews_for_pipeline.sql). target_id and run_id
are not here: the ingestion pipeline adds them when it saves the rows, because
they come from the run, not from the review source.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReviewCreate(BaseModel):
    # strict: the normalizer must hand over real ints and datetimes, so a bug
    # there fails loudly instead of being silently coerced.
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # Required (same rules as the table's CHECK constraints).
    rating: int = Field(ge=1, le=5)
    review_text: str = Field(min_length=1)
    # Which mapping wrote the row, e.g. "serpapi@1".
    mapping_version: str = Field(min_length=1)

    # Optional source metadata, kept when the source provides it. Never the
    # reviewer's name or anything else that identifies them.
    reviewed_at: datetime | None = None
    source_review_id: str | None = None
    source_url: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewResponse(BaseModel):
    id: UUID
    target_id: UUID
    run_id: UUID
    rating: int
    review_text: str
    author_name: str | None
    reviewed_at: datetime | None
    source_review_id: str | None
    source_url: str | None
    source_metadata: dict[str, Any]
    created_at: datetime


class ReviewList(BaseModel):
    items: list[ReviewResponse]
