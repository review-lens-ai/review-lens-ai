"""S2-010: the Q&A answer contract the API returns.

    {
      "status":         "ANSWERED | INSUFFICIENT_EVIDENCE | OUT_OF_SCOPE",
      "answer":         "text for the user",
      "evidence":       [{"id", "rating", "review_text", "reviewed_at"}, ...],
      "steps":          [{"tool", "summary"}, ...],
      "refusal_reason": "GENERAL_KNOWLEDGE | OTHER_ENTITY | OTHER_LOCATION |
                         OTHER_PLATFORM | NO_EVIDENCE" or null,
      "suggestions":    ["a question ReviewLens can answer", ...],
      "context":        {"target_id", "target_name", "platform",
                         "reviews_considered", "reviews_total", "is_subset"}
    }

- status is one of the three the model may choose (S2-012, S2-014, S2-015). A
  target with no reviews never gets an answer: the API answers 409 QA_NO_REVIEWS
  before any model call (S2-BR-025).
- evidence is the cited reviews, read back from the database, never model text, so
  every quoted review really exists in the active dataset (S2-BR-010).
- steps are the tools the model ran, for the UI. context counts come from the
  database, not the model (techspecs 7.3).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class AnswerStatus(StrEnum):
    ANSWERED = "ANSWERED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class RefusalReason(StrEnum):
    """Why a question was declined. NO_EVIDENCE goes with INSUFFICIENT_EVIDENCE;
    the other four with OUT_OF_SCOPE."""

    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"  # homework, trivia, coding, advice
    OTHER_ENTITY = "OTHER_ENTITY"  # another business, product or person
    OTHER_LOCATION = "OTHER_LOCATION"  # another branch of the same chain
    OTHER_PLATFORM = "OTHER_PLATFORM"  # reviews from a platform we don't have
    NO_EVIDENCE = "NO_EVIDENCE"  # about this place, but the reviews don't say


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
