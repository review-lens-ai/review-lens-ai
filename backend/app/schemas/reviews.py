"""S1-021: the canonical Review that normalization produces and S1-022 saves.

Mirrors the reviews table (supabase/migrations/20261025120300_create_reviews.sql).
target_id and run_id are not here: the ingestion pipeline adds them when it saves
the rows, because they come from the run, not from the review source.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReviewCreate(BaseModel):
    # strict: the normalizer must hand over real ints and datetimes, so a bug
    # there fails loudly instead of being silently coerced.
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # Required (same rules as the table's CHECK constraints).
    rating: int = Field(ge=1, le=5)
    review_text: str = Field(min_length=1)

    # Optional source metadata, kept when the source provides it.
    author_name: str | None = None
    reviewed_at: datetime | None = None
    source_review_id: str | None = None
    source_url: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
