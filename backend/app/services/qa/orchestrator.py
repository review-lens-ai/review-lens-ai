"""S2-003: answer one question about the active AnalysisTarget.

    1. clean the question               blank or too long -> InvalidQuestionError
    2. load the target the caller owns  another user's    -> NotFoundError
    3. no reviews at all                -> NO_REVIEWS
    4. select and check the context     wrong target      -> ContextIsolationError
       nothing selected                 -> INSUFFICIENT_EVIDENCE
    5. build the prompt, call the model provider failure  -> QAUnavailableError
    6. check the reply                  -> AnswerResponse

Steps 1 to 4 run before the model is called; when any of them stops the request,
nothing is sent to the model. Each call is independent and reads the reviews
again, so changing the active target changes the context (S2-BR-023, S2-BR-024).

History is always empty for now. Persisted chat history is planned: a
conversation will belong to one owner and one target, and its earlier turns
will go into ModelRequest.history under the same isolation rules as reviews.
"""

import logging
from typing import Protocol
from uuid import UUID

from app.core.errors import AppError
from app.llm import ModelClient, ModelError, ModelRequest
from app.schemas.qa import AnswerResponse
from app.services.qa.answer import build_answer, insufficient_answer, no_reviews_answer
from app.services.qa.context import (
    ReviewSelector,
    ReviewSource,
    build_bundle,
    select_all_reviews,
)
from app.services.qa.prompt import build_system_prompt, clean_question, format_reviews

logger = logging.getLogger(__name__)


class QAUnavailableError(AppError):
    """The model failed or replied in the wrong shape. Provider text is logged
    by type only and never reaches the user."""

    status_code = 503
    code = "QA_UNAVAILABLE"
    message = "ReviewLens couldn't answer right now. Please try again in a moment."


class TargetSource(Protocol):
    def get_owned(self, target_id: UUID | str, owner_id: UUID | str) -> dict: ...


def answer_question(
    *,
    owner_id: UUID | str,
    target_id: UUID | str,
    question: str,
    targets: TargetSource,
    reviews: ReviewSource,
    model: ModelClient,
    select_reviews: ReviewSelector = select_all_reviews,
) -> AnswerResponse:
    question = clean_question(question)

    target = targets.get_owned(target_id, owner_id)
    reviews_total = reviews.count_for_target(target["id"], owner_id)
    if reviews_total == 0:
        return no_reviews_answer(build_bundle(target, [], 0))

    rows = select_reviews(reviews, target, owner_id, question)
    bundle = build_bundle(target, rows, reviews_total)
    if not bundle.reviews:
        return insufficient_answer(bundle)

    request = ModelRequest(
        system_prompt=build_system_prompt(bundle),
        reviews_block=format_reviews(bundle),
        question=question,
    )
    try:
        return build_answer(model.complete(request), bundle)
    except ModelError as exc:
        logger.warning(
            "Q&A model call failed for target %s (%s).",
            bundle.target_id,
            type(exc).__name__,
        )
        raise QAUnavailableError() from exc
