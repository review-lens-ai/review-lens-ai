"""The one set of conditions every review tool uses (S2-010).

    ReviewFilter{keywords?, min_rating?, max_rating?, since?, until?}

The model fills it in; it never says whose reviews or which target. Those are
fixed by the ToolSet, so no filter can reach another user's or another target's
reviews.

How each field finds reviews (all deterministic; the model only writes the values):
- keywords: exact words, matched by their stem with Postgres full-text search
  ("slow" also finds "slows" and "slowed").
- min_rating / max_rating: the star range. Sentiment comes from here, not from
  the phrase.
- since / until: the review date range.
All set fields must match (AND).

Search by meaning (a phrase like "long wait" also finding "took forever") is not
here yet. It becomes a new field once embedding search exists, so the model is
never offered a field that does nothing.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PHRASE_LENGTH = 200


class ReviewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keywords: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PHRASE_LENGTH,
        description=(
            'Words a review must contain, e.g. "slow" or "cold fries". All of them'
            ' are required; write "slow or rude" for either.'
        ),
    )
    min_rating: int | None = Field(default=None, ge=1, le=5)
    max_rating: int | None = Field(default=None, ge=1, le=5)
    since: datetime | None = Field(default=None, description="Reviewed on or after.")
    until: datetime | None = Field(default=None, description="Reviewed before.")

    @model_validator(mode="after")
    def _valid_combination(self) -> "ReviewFilter":
        low, high = self.min_rating, self.max_rating
        if low is not None and high is not None and low > high:
            raise ValueError("min_rating cannot be higher than max_rating")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("since must be before until")
        return self
