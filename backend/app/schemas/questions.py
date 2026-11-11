"""S2-004: what kind of response a Q&A answer is.

The scope-guard evaluation judges answers by this status, never by their wording
(S2-BR-029), so the Q&A pipeline (S2-011) must return it with every answer.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ANSWERED: answered from the active target's reviews.
# INSUFFICIENT_EVIDENCE: in scope, but the reviews don't say (S2-BR-011).
# OUT_OF_SCOPE: declined: general knowledge, another entity or another
# platform (S2-BR-016 to S2-BR-018).
AnswerStatus = Literal["ANSWERED", "INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE"]


class AnswerResponse(BaseModel):
    """One ReviewLens answer. The text is for the user; the status is for code."""

    status: AnswerStatus
    answer: str = Field(min_length=1)
