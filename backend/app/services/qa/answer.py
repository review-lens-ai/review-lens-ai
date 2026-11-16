"""S2-010: the model's final call (submit_answer) and the answer the API returns.

    spec = submit_answer_spec()                       offered with the review tools
    response = build_answer(submitted, bundle=...)    SubmitAnswer | None -> AnswerResponse

Rules:
- EVIDENCE FROM THE DATABASE: cited labels (R1, R2, ...) are mapped back through the
  question's LabelBook and the reviews are read again from the active target. A
  label no tool handed out is dropped, duplicates are removed, and the list is
  capped at MAX_CITATIONS. Review text always comes from the database, never from
  the model.
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
- NO REPLY: when the loop ends without a valid submit_answer, the answer is
  INSUFFICIENT_EVIDENCE.
- PLAIN TEXT: the prompt asks for plain text without review labels. tidy_answer
  backs that up on the model's text: it removes bracketed label lists like
  "(R4, R16)" and Markdown bold markers, since the UI shows the text as-is and
  shows the cited reviews under it. It reads the model's output only.
"""

import logging
import re
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.llm import ReplyStatus
from app.repositories.review_search import ReviewSearchRepository
from app.schemas.qa import (
    AnswerContext,
    AnswerResponse,
    AnswerStatus,
    EvidenceReview,
    RefusalReason,
    ToolStep,
)
from app.services.qa.context import ContextBundle
from app.services.qa.tools.guard import require_in_scope
from app.services.qa.tools.labels import LabelBook

logger = logging.getLogger(__name__)

SUBMIT_ANSWER = "submit_answer"
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

    The model's final tool call (submit_answer). submit_answer_spec() sends the
    first line above as the tool's description, and each field description: they
    are part of the prompt (S2-013). Nothing here is trusted as-is; the answer
    builder still checks citations against what was shown and checks refusals.

    The schema stays permissive on purpose. A status that doesn't match its
    refusal_reason is corrected by the answer builder, not rejected here, so the
    model is never stuck retrying a call it can't get past validation. A field it
    doesn't know is rejected, though: ignoring a misnamed field (cited_review_ids
    for cited) would quietly drop the citations and turn an answer into a refusal.
    """

    model_config = ConfigDict(extra="forbid")

    status: ReplyStatus = Field(
        description=(
            "ANSWERED if the reviews answer the question. INSUFFICIENT_EVIDENCE if "
            "the question is about this place but the reviews don't cover it. "
            "OUT_OF_SCOPE if the question is not about this place's reviews."
        )
    )
    answer: str = Field(
        description=(
            "What the user reads, in plain text: no Markdown, and no review labels "
            "(R1, R2, ...); put those in cited. When you decline, say briefly why "
            "and what you can help with instead. Never pretend you analyzed "
            "information you were not given."
        )
    )
    cited: list[str] = Field(
        default_factory=list,
        description=(
            "Labels (R1, R2, ...) of the reviews your answer relies on, only labels "
            f"you were shown, at most {MAX_CITATIONS}. At least one when ANSWERED, "
            "even for an answer about numbers: an answer citing none is shown as "
            "not enough evidence. Empty unless ANSWERED."
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


def submit_answer_spec() -> dict:
    """The tool spec the model sees, in the same shape as ToolRegistry.specs(): the
    first line of SubmitAnswer's docstring and its fields. The rest of the
    docstring is for developers, so it is left out."""
    parameters = SubmitAnswer.model_json_schema()
    parameters.pop("description", None)
    parameters.pop("title", None)
    return {
        "name": SUBMIT_ANSWER,
        "description": SubmitAnswer.__doc__.splitlines()[0],
        "parameters": parameters,
    }


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


# "(R4)", "(R4, R16, R19)", "(R1 and R2)", "(e.g. R3)": a bracketed list of review
# labels, with the space before it.
_LABEL_LIST = re.compile(
    r"\s*\((?:e\.g\.,?\s*)?R\d+(?:\s*(?:,|and|&)\s*R\d+)*\)", re.IGNORECASE
)
_BOLD = re.compile(r"\*\*|__")


def tidy_answer(text: str) -> str:
    """The model's text as the user should read it: bracketed review labels and
    Markdown bold markers removed, surrounding whitespace trimmed. Labels belong in
    cited, where the app turns them into the evidence shown under the answer."""
    return _BOLD.sub("", _LABEL_LIST.sub("", text)).strip()


@dataclass(frozen=True)
class LoopRecord:
    """What the tool loop did for one question: the tools it ran (for the UI) and
    the reviews whose text the model was shown (for context.reviews_considered)."""

    steps: tuple[ToolStep, ...] = ()
    shown_review_ids: frozenset[UUID] = frozenset()


NOTHING_RAN = LoopRecord()


def context_summary(bundle: ContextBundle, record: LoopRecord) -> AnswerContext:
    considered = len(record.shown_review_ids)
    return AnswerContext(
        target_id=bundle.target_id,
        target_name=bundle.target_name,
        platform=bundle.platform,
        reviews_considered=considered,
        reviews_total=bundle.reviews_total,
        is_subset=considered < bundle.reviews_total,
    )


def _respond(
    status: AnswerStatus,
    answer: str,
    bundle: ContextBundle,
    record: LoopRecord,
    evidence: list[EvidenceReview] | None = None,
    refusal_reason: RefusalReason | None = None,
    suggestions: list[str] | None = None,
) -> AnswerResponse:
    return AnswerResponse(
        status=status,
        answer=answer,
        evidence=evidence or [],
        steps=list(record.steps),
        context=context_summary(bundle, record),
        refusal_reason=refusal_reason,
        suggestions=suggestions or [],
    )


def clean_suggestions(suggestions: list[str]) -> list[str]:
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
    record: LoopRecord,
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
        record,
        refusal_reason=reason,
        suggestions=suggestions or fallback_suggestions(reason, bundle),
    )


def no_reviews_answer(bundle: ContextBundle) -> AnswerResponse:
    """The target has no saved reviews: decided before any model call (S2-BR-025)."""
    return _respond(AnswerStatus.NO_REVIEWS, NO_REVIEWS_MESSAGE, bundle, NOTHING_RAN)


def insufficient_answer(bundle: ContextBundle, record: LoopRecord) -> AnswerResponse:
    return _refusal(
        AnswerStatus.INSUFFICIENT_EVIDENCE,
        RefusalReason.NO_EVIDENCE,
        "",
        [],
        bundle,
        record,
    )


def cited_evidence(
    cited: list[str],
    bundle: ContextBundle,
    labels: LabelBook,
    search: ReviewSearchRepository,
) -> list[EvidenceReview]:
    """The cited reviews, read again from the active target, in the model's order."""
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
            bundle.target_id,
        )
    review_ids = review_ids[:MAX_CITATIONS]
    if not review_ids:
        return []
    rows = search.get_many(review_ids)
    require_in_scope(rows, bundle.target_id)
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


def build_answer(
    submitted: SubmitAnswer | None,
    *,
    bundle: ContextBundle,
    labels: LabelBook,
    search: ReviewSearchRepository,
    record: LoopRecord,
) -> AnswerResponse:
    if submitted is None:
        return insufficient_answer(bundle, record)
    text = tidy_answer(submitted.answer)

    if submitted.status is ReplyStatus.ANSWERED:
        evidence = cited_evidence(submitted.cited, bundle, labels, search)
        if not evidence or not text:
            logger.info(
                "Answer for target %s had no valid citation or text; "
                "returned as insufficient evidence.",
                bundle.target_id,
            )
            return insufficient_answer(bundle, record)
        return _respond(AnswerStatus.ANSWERED, text, bundle, record, evidence)

    suggestions = clean_suggestions(submitted.suggestions)
    if submitted.status is ReplyStatus.INSUFFICIENT_EVIDENCE:
        return _refusal(
            AnswerStatus.INSUFFICIENT_EVIDENCE,
            RefusalReason.NO_EVIDENCE,
            text,
            suggestions,
            bundle,
            record,
        )
    return _refusal(
        AnswerStatus.OUT_OF_SCOPE,
        out_of_scope_reason(submitted.refusal_reason),
        text,
        suggestions,
        bundle,
        record,
    )
