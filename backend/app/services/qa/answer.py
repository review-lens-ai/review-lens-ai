"""S2-003: turn the model's reply into the AnswerResponse the API returns.

Rules:
- EVIDENCE FROM CONTEXT: cited ids are looked up in the reviews that were sent.
  Unknown ids are dropped, duplicates removed and the list capped at
  MAX_CITATIONS. Review text always comes from the bundle, never from the model.
- NO UNGROUNDED ANSWERS: an ANSWERED reply with no valid citation, or with no
  text, becomes INSUFFICIENT_EVIDENCE with a fixed message (S2-BR-010, S2-BR-011).
- REFUSALS: INSUFFICIENT_EVIDENCE and OUT_OF_SCOPE keep the model's explanation
  (or a fixed one if it is empty) and never carry evidence (S2-BR-021).
- A status outside ReplyStatus is a ModelReplyError, handled like a failed call.
"""

import logging

from app.llm import ModelReply, ModelReplyError, ReplyStatus
from app.schemas.qa import AnswerContext, AnswerResponse, AnswerStatus, EvidenceReview
from app.services.qa.context import ContextBundle

logger = logging.getLogger(__name__)

MAX_CITATIONS = 10

NO_REVIEWS_MESSAGE = (
    "This target has no ingested reviews yet. "
    "Run an ingestion before asking questions about it."
)
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "The reviews for this target don't contain enough information to answer that."
)
OUT_OF_SCOPE_MESSAGE = (
    "ReviewLens can only answer questions about the reviews for this target."
)


def context_summary(bundle: ContextBundle) -> AnswerContext:
    return AnswerContext(
        target_id=bundle.target_id,
        target_name=bundle.target_name,
        platform=bundle.platform,
        reviews_considered=len(bundle.reviews),
        reviews_total=bundle.reviews_total,
        is_subset=bundle.is_subset,
    )


def _respond(
    status: AnswerStatus,
    answer: str,
    bundle: ContextBundle,
    evidence: list[EvidenceReview] | None = None,
) -> AnswerResponse:
    return AnswerResponse(
        status=status,
        answer=answer,
        evidence=evidence or [],
        context=context_summary(bundle),
    )


def no_reviews_answer(bundle: ContextBundle) -> AnswerResponse:
    return _respond(AnswerStatus.NO_REVIEWS, NO_REVIEWS_MESSAGE, bundle)


def insufficient_answer(bundle: ContextBundle) -> AnswerResponse:
    return _respond(
        AnswerStatus.INSUFFICIENT_EVIDENCE, INSUFFICIENT_EVIDENCE_MESSAGE, bundle
    )


def cited_evidence(
    cited_ids: tuple[str, ...], bundle: ContextBundle
) -> list[EvidenceReview]:
    evidence: list[EvidenceReview] = []
    seen: set[str] = set()
    unknown = 0
    for review_id in cited_ids:
        review = bundle.by_id.get(str(review_id))
        if review is None:
            unknown += 1
            continue
        if review.id in seen:
            continue
        seen.add(review.id)
        evidence.append(
            EvidenceReview(
                id=review.id,
                rating=review.rating,
                review_text=review.review_text,
                reviewed_at=review.reviewed_at,
            )
        )
        if len(evidence) == MAX_CITATIONS:
            break
    if unknown:
        logger.info(
            "Dropped %d cited review id(s) that were not in the context of target %s.",
            unknown,
            bundle.target_id,
        )
    return evidence


def build_answer(reply: ModelReply, bundle: ContextBundle) -> AnswerResponse:
    try:
        status = ReplyStatus(reply.status)
    except ValueError:
        raise ModelReplyError("The model returned an unknown status.") from None
    text = (reply.answer or "").strip()

    if status is ReplyStatus.ANSWERED:
        evidence = cited_evidence(reply.cited_review_ids, bundle)
        if not evidence or not text:
            logger.info(
                "Answer for target %s had no valid citation or text; "
                "returned as insufficient evidence.",
                bundle.target_id,
            )
            return insufficient_answer(bundle)
        return _respond(AnswerStatus.ANSWERED, text, bundle, evidence)

    if status is ReplyStatus.INSUFFICIENT_EVIDENCE:
        return _respond(
            AnswerStatus.INSUFFICIENT_EVIDENCE,
            text or INSUFFICIENT_EVIDENCE_MESSAGE,
            bundle,
        )
    return _respond(AnswerStatus.OUT_OF_SCOPE, text or OUT_OF_SCOPE_MESSAGE, bundle)
