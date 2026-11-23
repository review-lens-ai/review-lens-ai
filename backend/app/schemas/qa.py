"""S2-010: the Q&A answer contract the API returns.

    {
      "status":         "ANSWERED | INSUFFICIENT_EVIDENCE | OUT_OF_SCOPE | NO_REVIEWS",
      "answer":         "text for the user",
      "evidence":       [{"id", "rating", "review_text", "reviewed_at"}, ...],
      "steps":          [{"tool", "summary"}, ...],
      "refusal_reason": "GENERAL_KNOWLEDGE | OTHER_ENTITY | OTHER_LOCATION |
                         OTHER_PLATFORM | NO_EVIDENCE" or null,
      "suggestions":    ["a question the user could ask instead", ...],
      "context":        {"target_id", "target_name", "platform",
                         "reviews_considered", "reviews_total", "is_subset"}
    }

- evidence is the cited reviews, read back from the database, never model text, so
  every quoted review really exists in the active dataset (S2-BR-010).
- steps are the tools the model ran, for the UI.
- refusal_reason says why ReviewLens declined, so the UI can explain it
  (S2-BR-021); null when answered or when the target has no reviews.
  suggestions (at most 2) are only given with a refusal.
- context counts come from the database, not the model (techspecs 7.3).
- NO_REVIEWS is decided by the app alone: the model is not called (S2-BR-025).
  The model can only choose the other three (ReplyStatus in app/llm/client.py).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class AnswerStatus(StrEnum):
    ANSWERED = "ANSWERED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    NO_REVIEWS = "NO_REVIEWS"


class RefusalReason(StrEnum):
    """Why the model declined (S2-012, S2-014, S2-015). The model picks one in
    submit_answer; the first four go with OUT_OF_SCOPE, NO_EVIDENCE with
    INSUFFICIENT_EVIDENCE. Unset when the question was answered."""

    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"
    OTHER_ENTITY = "OTHER_ENTITY"
    OTHER_LOCATION = "OTHER_LOCATION"
    OTHER_PLATFORM = "OTHER_PLATFORM"
    NO_EVIDENCE = "NO_EVIDENCE"


class QuestionCreate(BaseModel):
    # Checked by answer_question (services/qa/orchestrator.py), not here, so the
    # rule lives in one place.
    question: str


class EvidenceReview(BaseModel):
    id: UUID
    rating: int
    review_text: str
    reviewed_at: datetime | None


class ToolStep(BaseModel):
    """One tool the model ran, e.g. {"tool": "review_stats", "summary": "61 of 640"}."""

    tool: str
    summary: str


class AnswerContext(BaseModel):
    target_id: UUID
    target_name: str
    platform: str
    reviews_considered: int  # reviews whose text the model was shown
    reviews_total: int  # all of the target's reviews
    is_subset: bool


class AnswerResponse(BaseModel):
    status: AnswerStatus
    answer: str
    evidence: list[EvidenceReview]
    context: AnswerContext
    steps: list[ToolStep] = Field(default_factory=list)
    refusal_reason: RefusalReason | None = None
    suggestions: list[str] = Field(default_factory=list)


class QuestionResponse(BaseModel):
    """S3-006: one saved question and the response the user saw, evidence read
    back from the target's reviews (QuestionRepository.list_for_target)."""

    id: UUID
    question: str
    created_at: datetime
    response: AnswerResponse


class QuestionList(BaseModel):
    """A target's Q&A history, oldest first. Not paginated (known limit)."""

    items: list[QuestionResponse]
