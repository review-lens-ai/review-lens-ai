"""S2-003: the review context for one question.

    rows = select_reviews(reviews, target, owner_id, question)
    bundle = build_bundle(target, rows, reviews_total)

Rules:
- OWNERSHIP: the target comes from TargetRepository.get_owned and the rows from
  owner-checked repository methods, so only the caller's own data is read
  (S2-BR-022, S2-BR-026). owner_id must come from the verified token.
- ISOLATION CHECK: build_bundle refuses any row whose target_id is not the active
  target, before anything is sent to the model. Reviews have no owner column, so
  the target is what ties them to the owner. This catches a selector that forgets
  its target filter (S2-BR-009, S2-BR-023, S2-BR-024).
- TEMPORARY SELECTOR: select_all_reviews sends every review. S2-010 decides the
  real strategy; any selector with the ReviewSelector signature can replace it.
- FRESH EVERY QUESTION: nothing is cached, so a new question always reads the
  active target's reviews again.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Protocol
from uuid import UUID

from app.core.errors import AppError

logger = logging.getLogger(__name__)


class ContextIsolationError(AppError):
    """A selected review does not belong to the active target. Nothing is sent."""

    status_code = 500
    code = "QA_CONTEXT_ERROR"
    message = "We couldn't prepare the reviews for this question. Please try again."


class ReviewSource(Protocol):
    """The ReviewRepository methods Q&A uses."""

    def list_for_target(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]: ...

    def count_for_target(self, target_id: UUID | str, owner_id: UUID | str) -> int: ...


ReviewSelector = Callable[[ReviewSource, dict, UUID | str, str], list[dict]]


def select_all_reviews(
    reviews: ReviewSource, target: dict, owner_id: UUID | str, question: str
) -> list[dict]:
    """TEMPORARY (until S2-010): every review of the target, newest first."""
    return reviews.list_for_target(target["id"], owner_id)


@dataclass(frozen=True)
class ContextReview:
    id: str
    target_id: str
    rating: int
    review_text: str
    reviewed_at: datetime | None


@dataclass(frozen=True)
class ContextBundle:
    target_id: str
    target_name: str
    platform: str
    reviews: tuple[ContextReview, ...]
    reviews_total: int

    @property
    def is_subset(self) -> bool:
        return len(self.reviews) < self.reviews_total

    @cached_property
    def by_id(self) -> dict[str, ContextReview]:
        return {review.id: review for review in self.reviews}


def build_bundle(target: dict, rows: list[dict], reviews_total: int) -> ContextBundle:
    target_id = str(target["id"])
    reviews = []
    for row in rows:
        if str(row["target_id"]) != target_id:
            logger.error(
                "Q&A context for target %s included a review of another target; "
                "nothing was sent to the model.",
                target_id,
            )
            raise ContextIsolationError()
        reviews.append(
            ContextReview(
                id=str(row["id"]),
                target_id=target_id,
                rating=row["rating"],
                review_text=row["review_text"],
                reviewed_at=row["reviewed_at"],
            )
        )
    return ContextBundle(
        target_id=target_id,
        target_name=target["name"],
        platform=target["platform"],
        reviews=tuple(reviews),
        reviews_total=reviews_total,
    )
