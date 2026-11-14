"""S2-003: turn the model's reply into the AnswerResponse the API returns.

Rules:
- EVIDENCE FROM CONTEXT: cited ids are looked up in the reviews that were sent.
  Unknown ids are dropped, duplicates removed and the list capped at
  MAX_CITATIONS. Review text always comes from the bundle, never from the model.
- NO UNGROUNDED ANSWERS: an ANSWERED reply with no valid citation, or with no
  text, becomes INSUFFICIENT_EVIDENCE with a fixed message (S2-BR-010, S2-BR-011).
- REFUSALS: INSUFFICIENT_EVIDENCE and OUT_OF_SCOPE keep the model's explanation
  when it passes usable_refusal (not blank, at most MAX_REFUSAL_CHARACTERS);
  otherwise they get the fixed template for their reason. They never carry
  evidence (S2-BR-021).
- REFUSAL REASONS: every INSUFFICIENT_EVIDENCE answer has reason NO_EVIDENCE.
  OUT_OF_SCOPE keeps the model's reason when it is one of the four out-of-scope
  reasons, otherwise none. A refusal keeps at most MAX_SUGGESTIONS non-blank,
  distinct suggestions, or one fallback suggestion for its reason when the model
  gave none; an answer has neither.
- A status outside ReplyStatus is a ModelReplyError, handled like a failed call.
"""

import logging

from pydantic import BaseModel, Field

from app.llm import ModelReply, ModelReplyError, ReplyStatus
from app.schemas.qa import (
    AnswerContext,
    AnswerResponse,
    AnswerStatus,
    EvidenceReview,
    RefusalReason,
)
from app.services.qa.context import ContextBundle

logger = logging.getLogger(__name__)

MAX_CITATIONS = 10
MAX_SUGGESTIONS = 2

# The reasons that go with OUT_OF_SCOPE; NO_EVIDENCE goes with INSUFFICIENT_EVIDENCE.
OUT_OF_SCOPE_REASONS = frozenset(
    {
        RefusalReason.GENERAL_KNOWLEDGE,
        RefusalReason.OTHER_ENTITY,
        RefusalReason.OTHER_LOCATION,
        RefusalReason.OTHER_PLATFORM,
    }
)


class SubmitAnswer(BaseModel):
    """Finish the question: give your answer, or say why you cannot answer it.

    The model's final tool call (submit_answer). The Q&A loop binds this class as a
    tool, so the docstring and each field description are what the model reads:
    they are part of the prompt (S2-013). Nothing here is trusted as-is; the answer
    builder still checks citations against what was sent and checks refusals.

    The schema stays permissive on purpose. A status that doesn't match its
    refusal_reason is corrected by the answer builder, not rejected here, so the
    model is never stuck retrying a call it can't get past validation.
    """

    status: ReplyStatus = Field(
        description=(
            "ANSWERED if the reviews answer the question. INSUFFICIENT_EVIDENCE if "
            "the question is about this place but the reviews don't cover it. "
            "OUT_OF_SCOPE if the question is not about this place's reviews."
        )
    )
    answer: str = Field(
        description=(
            "What the user reads. When you decline, say briefly why and what you "
            "can help with instead. Never pretend you analyzed information you "
            "were not given."
        )
    )
    cited: list[str] = Field(
        default_factory=list,
        description=(
            "Labels (R1, R2, ...) of the reviews your answer relies on, only labels "
            f"you were shown, at most {MAX_CITATIONS}. Empty unless ANSWERED."
        ),
    )
    refusal_reason: RefusalReason | None = Field(
        default=None,
        description=(
            "Why you declined. GENERAL_KNOWLEDGE: not about these reviews. "
            "OTHER_ENTITY: another business, product or person. OTHER_LOCATION: "
            "another branch of the same chain. OTHER_PLATFORM: a review site other "
            "than the one these reviews come from. NO_EVIDENCE: about this place, "
            "but the reviews don't cover it. Leave empty when ANSWERED."
        ),
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description=(
            f"When you decline, up to {MAX_SUGGESTIONS} questions about these "
            "reviews the user could ask instead. Empty when ANSWERED."
        ),
    )


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

# A refusal says briefly why it declined (the prompt asks for that). One much longer
# than this has most likely answered the question anyway, so it is replaced.
MAX_REFUSAL_CHARACTERS = 500

# The fixed message for each reason, used when the model's own refusal is blank or
# too long (architecture doc, "Refusal message"). {name}, {platform} and {location}
# come from the bundle; the name is the user's own label, shown as typed.
REFUSAL_TEMPLATES = {
    RefusalReason.GENERAL_KNOWLEDGE: (
        "I can only answer questions about the {platform} reviews for {name}, "
        "so I can't help with that."
    ),
    RefusalReason.OTHER_ENTITY: (
        "I only have reviews for {name}, so I can't speak to other businesses, "
        "products or people."
    ),
    RefusalReason.OTHER_LOCATION: (
        "These reviews are for {name}{location} only, not other locations."
    ),
    RefusalReason.OTHER_PLATFORM: (
        "I only have {platform} reviews for {name}, not reviews from other sites."
    ),
    RefusalReason.NO_EVIDENCE: INSUFFICIENT_EVIDENCE_MESSAGE,
}

# Offered when the model declines without suggestions, so the user always has an
# in-scope question to try. Kept general: they must fit any target.
FALLBACK_SUGGESTIONS = {
    RefusalReason.GENERAL_KNOWLEDGE: "What do reviewers like most?",
    RefusalReason.OTHER_ENTITY: "What do reviewers like most here?",
    RefusalReason.OTHER_LOCATION: "What do reviewers say about the service here?",
    RefusalReason.OTHER_PLATFORM: "What's the overall rating on {platform}?",
    RefusalReason.NO_EVIDENCE: "What do reviewers mention most often?",
}


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
    refusal_reason: RefusalReason | None = None,
    suggestions: list[str] | None = None,
) -> AnswerResponse:
    return AnswerResponse(
        status=status,
        answer=answer,
        evidence=evidence or [],
        context=context_summary(bundle),
        refusal_reason=refusal_reason,
        suggestions=suggestions or [],
    )


def clean_suggestions(suggestions: tuple[str, ...]) -> list[str]:
    """The model's suggestions, trimmed, without blanks or repeats, at most
    MAX_SUGGESTIONS, in the model's order."""
    cleaned: list[str] = []
    for suggestion in suggestions:
        text = (suggestion or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) == MAX_SUGGESTIONS:
            break
    return cleaned


def out_of_scope_reason(value: object) -> RefusalReason | None:
    """The model's reason if it is one of the four out-of-scope reasons, else None.
    An unknown or mismatched reason is not an error: the refusal itself stands."""
    try:
        reason = RefusalReason(value)
    except ValueError:
        return None
    return reason if reason in OUT_OF_SCOPE_REASONS else None


def usable_refusal(text: str) -> bool:
    """A light check on the model's own refusal: not blank and brief. It checks the
    model's output, never the user's question, so it is not a blocklist
    (S2-BR-020). A failing refusal is replaced by the template for its reason."""
    return bool(text) and len(text) <= MAX_REFUSAL_CHARACTERS


def refusal_template(reason: RefusalReason | None, bundle: ContextBundle) -> str:
    """The fixed message for a reason, filled in for this target. Without a reason,
    the general out-of-scope message."""
    if reason is None:
        return OUT_OF_SCOPE_MESSAGE
    return REFUSAL_TEMPLATES[reason].format(
        name=bundle.target_name,
        platform=bundle.platform_label,
        location=f" at {bundle.address}" if bundle.address else "",
    )


def fallback_suggestions(
    reason: RefusalReason | None, bundle: ContextBundle
) -> list[str]:
    if reason is None:
        return []
    return [FALLBACK_SUGGESTIONS[reason].format(platform=bundle.platform_label)]


def _refusal(
    status: AnswerStatus,
    reason: RefusalReason | None,
    text: str,
    suggestions: list[str],
    bundle: ContextBundle,
) -> AnswerResponse:
    """A refusal with the model's text when usable, otherwise the reason's template,
    and the model's suggestions, otherwise the reason's fallback one."""
    if not usable_refusal(text):
        if text:
            logger.info(
                "Refusal for target %s was %d characters; replaced with the %s "
                "template.",
                bundle.target_id,
                len(text),
                reason or "general",
            )
        text = refusal_template(reason, bundle)
    return _respond(
        status,
        text,
        bundle,
        refusal_reason=reason,
        suggestions=suggestions or fallback_suggestions(reason, bundle),
    )


def no_reviews_answer(bundle: ContextBundle) -> AnswerResponse:
    return _respond(AnswerStatus.NO_REVIEWS, NO_REVIEWS_MESSAGE, bundle)


def insufficient_answer(bundle: ContextBundle) -> AnswerResponse:
    return _refusal(
        AnswerStatus.INSUFFICIENT_EVIDENCE, RefusalReason.NO_EVIDENCE, "", [], bundle
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

    suggestions = clean_suggestions(reply.suggestions)
    if status is ReplyStatus.INSUFFICIENT_EVIDENCE:
        return _refusal(
            AnswerStatus.INSUFFICIENT_EVIDENCE,
            RefusalReason.NO_EVIDENCE,
            text,
            suggestions,
            bundle,
        )
    return _refusal(
        AnswerStatus.OUT_OF_SCOPE,
        out_of_scope_reason(reply.refusal_reason),
        text,
        suggestions,
        bundle,
    )
