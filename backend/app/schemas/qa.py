"""S2-003: the Q&A answer contract the API returns.

    {
      "status":   "ANSWERED | INSUFFICIENT_EVIDENCE | OUT_OF_SCOPE | NO_REVIEWS",
      "answer":   "text for the user",
      "evidence": [{"id", "rating", "review_text", "reviewed_at"}, ...],
      "context":  {"target_id", "target_name", "platform",
                   "reviews_considered", "reviews_total", "is_subset"}
    }

- evidence is copied from the reviews ReviewLens sent, never from model text, so
  every quoted review really exists in the active dataset (S2-BR-010).
- context counts come from the database, not the model (techspecs 7.3).
- NO_REVIEWS is decided by the app alone: the model is not called (S2-BR-025).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class AnswerStatus(StrEnum):
    ANSWERED = "ANSWERED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    NO_REVIEWS = "NO_REVIEWS"


class QuestionCreate(BaseModel):
    # Checked by answer_question (services/qa/orchestrator.py), not here, so the
    # rule lives in one place.
    question: str


class EvidenceReview(BaseModel):
    id: UUID
    rating: int
    review_text: str
    reviewed_at: datetime | None


class AnswerContext(BaseModel):
    target_id: UUID
    target_name: str
    platform: str
    reviews_considered: int  # reviews sent to the model
    reviews_total: int  # all of the target's reviews
    is_subset: bool


class AnswerResponse(BaseModel):
    status: AnswerStatus
    answer: str
    evidence: list[EvidenceReview]
    context: AnswerContext
