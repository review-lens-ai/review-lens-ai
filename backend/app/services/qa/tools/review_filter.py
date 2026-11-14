"""The one set of conditions every review tool uses (S2-010).

    ReviewFilter{words?, meaning?, min_rating?, max_rating?, since?}

The model fills it in; it never says whose reviews or which target. Those are
fixed by the ToolSet, so no filter can reach another user's or another target's
reviews.

meaning is a phrase for search by meaning (embeddings). Until that exists the
filter accepts it and the tools ignore it.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PHRASE_LENGTH = 200


class ReviewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    words: str | None = Field(default=None, min_length=1, max_length=MAX_PHRASE_LENGTH)
    meaning: str | None = Field(
        default=None, min_length=1, max_length=MAX_PHRASE_LENGTH
    )
    min_rating: int | None = Field(default=None, ge=1, le=5)
    max_rating: int | None = Field(default=None, ge=1, le=5)
    since: datetime | None = None  # reviewed on or after
    until: datetime | None = None  # reviewed before

    @model_validator(mode="after")
    def _ranges_in_order(self) -> "ReviewFilter":
        low, high = self.min_rating, self.max_rating
        if low is not None and high is not None and low > high:
            raise ValueError("min_rating cannot be higher than max_rating")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("since must be before until")
        return self
