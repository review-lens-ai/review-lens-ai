"""S2-010: the model's final call (submit_answer) and the answer the API returns.

    spec = submit_answer_spec()                    # offered with the review tools
    response = build_answer(submitted, ...)        # SubmitAnswer | None -> AnswerResponse

Rules:
- EVIDENCE FROM THE DATABASE: cited labels (R1, R2, ...) are mapped back through the
  question's LabelBook and the reviews are read again, owner-checked. A label no tool
  handed out is dropped, duplicates are removed, and the list is capped at
  MAX_CITATIONS. Review text always comes from the database, never from the model.
- NO UNGROUNDED ANSWERS: an ANSWERED reply with no valid citation, or with no text,
  becomes INSUFFICIENT_EVIDENCE with a fixed message (S2-BR-010, S2-BR-011).
- REFUSALS: INSUFFICIENT_EVIDENCE and OUT_OF_SCOPE keep the model's explanation (a
  fixed one if it is blank), its refusal_reason and up to MAX_SUGGESTIONS
  suggestions, and never carry evidence (S2-BR-021). INSUFFICIENT_EVIDENCE without
  a reason gets NO_EVIDENCE. Checks on the refusal text itself come later (S2-013).
- NO REPLY: when the loop ends without a valid submit_answer, the answer is
  INSUFFICIENT_EVIDENCE.
"""

import logging
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.review_search import ReviewSearchRepository
from app.schemas.qa import (
    AnswerContext,
    AnswerResponse,
    AnswerStatus,
    EvidenceReview,
    RefusalReason,
    ToolStep,
)
from app.services.qa.tools.guard import require_in_scope
from app.services.qa.tools.labels import LabelBook

logger = logging.getLogger(__name__)

SUBMIT_ANSWER = "submit_answer"
MAX_CITATIONS = 10
MAX_SUGGESTIONS = 3

INSUFFICIENT_EVIDENCE_MESSAGE = (
    "The reviews for this target don't contain enough information to answer that."
)
OUT_OF_SCOPE_MESSAGE = (
    "ReviewLens can only answer questions about the reviews for this target."
)


class SubmitAnswer(BaseModel):
    """Finish the question. Cite the labels of the reviews the answer relies on;
    cite nothing unless status is ANSWERED."""

    model_config = ConfigDict(extra="forbid")

    status: AnswerStatus
    answer: str
    cited: list[str] = Field(default_factory=list)
    refusal_reason: RefusalReason | None = None
    suggestions: list[str] = Field(default_factory=list)


def submit_answer_spec() -> dict:
    """The tool spec the model sees, in the same shape as ToolRegistry.specs()."""
    return {
        "name": SUBMIT_ANSWER,
        "description": (
            "Finish the question with your answer. status ANSWERED needs at least one"
            " cited label (R1, R2, ...) from a tool result. Use INSUFFICIENT_EVIDENCE"
            " when the reviews don't cover the question, and OUT_OF_SCOPE when the"
            " question is not about these reviews."
        ),
        "parameters": SubmitAnswer.model_json_schema(),
    }


def build_answer(
    submitted: SubmitAnswer | None,
    *,
    target: dict,
    owner_id: UUID | str,
    reviews_total: int,
    labels: LabelBook,
    search: ReviewSearchRepository,
    shown_review_ids: set[UUID],
    steps: list[ToolStep],
) -> AnswerResponse:
    def respond(status, answer, evidence=(), reason=None, suggestions=()):
        return AnswerResponse(
            status=status,
            answer=answer,
            evidence=list(evidence),
            steps=steps,
            refusal_reason=reason,
            suggestions=list(suggestions),
            context=AnswerContext(
                target_id=target["id"],
                target_name=target["name"],
                platform=target["platform"],
                reviews_considered=len(shown_review_ids),
                reviews_total=reviews_total,
                is_subset=len(shown_review_ids) < reviews_total,
            ),
        )

    insufficient = (
        AnswerStatus.INSUFFICIENT_EVIDENCE,
        INSUFFICIENT_EVIDENCE_MESSAGE,
        (),
        RefusalReason.NO_EVIDENCE,
    )
    if submitted is None:
        return respond(*insufficient)

    text = submitted.answer.strip()
    if submitted.status is AnswerStatus.ANSWERED:
        evidence = _cited_evidence(submitted.cited, target, owner_id, labels, search)
        if not evidence or not text:
            logger.info(
                "Answer for target %s had no valid citation or text; "
                "returned as insufficient evidence.",
                target["id"],
            )
            return respond(*insufficient)
        return respond(AnswerStatus.ANSWERED, text, evidence)

    suggestions = [s.strip() for s in submitted.suggestions if s.strip()]
    suggestions = suggestions[:MAX_SUGGESTIONS]
    if submitted.status is AnswerStatus.INSUFFICIENT_EVIDENCE:
        return respond(
            AnswerStatus.INSUFFICIENT_EVIDENCE,
            text or INSUFFICIENT_EVIDENCE_MESSAGE,
            reason=submitted.refusal_reason or RefusalReason.NO_EVIDENCE,
            suggestions=suggestions,
        )
    return respond(
        AnswerStatus.OUT_OF_SCOPE,
        text or OUT_OF_SCOPE_MESSAGE,
        reason=submitted.refusal_reason,
        suggestions=suggestions,
    )


def _cited_evidence(
    cited: list[str],
    target: dict,
    owner_id: UUID | str,
    labels: LabelBook,
    search: ReviewSearchRepository,
) -> list[EvidenceReview]:
    review_ids: list[UUID] = []
    unknown = 0
    for label in cited:
        review_id = labels.review_id_for(label.strip())
        if review_id is None:
            unknown += 1
        elif review_id not in review_ids:
            review_ids.append(review_id)
    if unknown:
        logger.info(
            "Dropped %d cited label(s) no tool returned, for target %s.",
            unknown,
            target["id"],
        )
    review_ids = review_ids[:MAX_CITATIONS]
    if not review_ids:
        return []
    rows = search.get_many(target["id"], owner_id, review_ids)
    require_in_scope(rows, target["id"])
    by_id = {row["id"]: row for row in rows}
    return [
        EvidenceReview(
            id=row["id"],
            rating=row["rating"],
            review_text=row["review_text"],
            reviewed_at=row["reviewed_at"],
        )
        for row in (by_id.get(review_id) for review_id in review_ids)
        if row is not None
    ]
