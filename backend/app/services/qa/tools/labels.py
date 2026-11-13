"""Short labels (R1, R2, ...) for the reviews one question touches.

A new LabelBook is made for every question, so numbering restarts at R1 and
nothing carries over between questions (S2-019). Asking for the same review
twice in one question gives the same label.
"""

from uuid import UUID

from app.services.qa.tools.types import Source


class LabelBook:
    def __init__(self) -> None:
        self._by_review: dict[UUID, Source] = {}

    def source_for(self, review_id: UUID, detail: str) -> Source:
        known = self._by_review.get(review_id)
        if known is not None:
            return known
        source = Source(
            label=f"R{len(self._by_review) + 1}",
            review_id=review_id,
            detail=detail,
        )
        self._by_review[review_id] = source
        return source
