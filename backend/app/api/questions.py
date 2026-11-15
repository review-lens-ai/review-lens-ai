"""Review Q&A endpoint (S2-009, S2-010), mounted under /api/v1/targets.

    POST /api/v1/targets/{target_id}/questions   {"question": "..."}  -> AnswerResponse

The router is mounted on api_router, so every request comes from a verified user.
Ownership comes from the token: another user's target is a 404 before anything is
read or sent to the model (S2-BR-022, S2-BR-026). Only the question is sent; there
is no chat history yet.

    200  an answer, a refusal, or NO_REVIEWS for a target without reviews
    404  the target is not the caller's
    422  blank or too long question
    500  a review from another target reached a tool (QAContextError)
    503  the model failed or is not configured (QAUnavailableError)
"""

from collections.abc import Callable
from functools import partial
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends

from app.auth import OwnerId
from app.core.config import Settings, get_settings
from app.core.errors import error_responses
from app.db import get_connection
from app.llm import ChatModel
from app.repositories.review_search import ReviewSearchRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.qa import AnswerResponse, QuestionCreate
from app.services.qa import QAConfig, answer_question

router = APIRouter(prefix="/targets", tags=["questions"])


def get_model_factory(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Callable[[], ChatModel]:
    """Makes the model only once the checks before it pass, so a question that is
    blank, about another user's target or about a target without reviews never
    needs the provider. Tests replace this with a fake model."""
    from app.llm.openrouter import make_chat_model  # loads the provider SDK

    return partial(make_chat_model, settings)


Conn = Annotated[psycopg.Connection, Depends(get_connection)]
ModelFactory = Annotated[Callable[[], ChatModel], Depends(get_model_factory)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{target_id}/questions",
    response_model=AnswerResponse,
    responses=error_responses(404, 500, 503),
)
def ask_question(
    target_id: UUID,
    body: QuestionCreate,
    owner_id: OwnerId,
    conn: Conn,
    make_model: ModelFactory,
    settings: AppSettings,
) -> AnswerResponse:
    """S2-009: ask one question about one of the user's targets. The answer is
    built from that target's saved reviews only."""
    return answer_question(
        owner_id=owner_id,
        target_id=target_id,
        question=body.question,
        targets=TargetRepository(conn),
        reviews=ReviewRepository(conn),
        open_search=partial(ReviewSearchRepository.for_owner, conn),
        make_model=make_model,
        config=QAConfig.from_settings(settings),
    )
