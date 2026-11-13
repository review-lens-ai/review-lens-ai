"""S2-003: what ReviewLens sends to a model and what it expects back.

    request = ModelRequest(system_prompt, reviews_block, question)
    reply = model.complete(request)       # ModelReply, or a ModelError
    #       reply.status                  ANSWERED / INSUFFICIENT_EVIDENCE / OUT_OF_SCOPE
    #       reply.answer                  text for the user
    #       reply.cited_review_ids        ids from the reviews_block

Rules for every implementation of ModelClient:
- REVIEWS ARE DATA: reviews_block goes to the provider as data, separate from
  the system prompt (docs/standards/data-security-guardrails.md section 7).
- STRUCTURED REPLY: the adapter makes the provider return the ModelReply fields
  (structured output or tool calling) and raises ModelReplyError when it does not.
  The app still checks the reply against the reviews it sent (services/qa/answer.py).
- FAILURES: a provider error or timeout is ModelUnavailableError. Provider text
  stays in the exception for server logs; users never see it.
- HISTORY: always empty until conversations are stored. Persisted chat history
  is planned: a conversation belongs to one owner and one target, and its turns
  will travel here as ChatMessage values.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol


class ReplyStatus(StrEnum):
    """What kind of answer the model gave."""

    ANSWERED = "ANSWERED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True)
class ChatMessage:
    """One earlier turn of a conversation. Same role/content shape LangChain and
    assistant-ui use, so stored history can be passed straight through."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    reviews_block: str
    question: str
    history: tuple[ChatMessage, ...] = ()


@dataclass(frozen=True)
class ModelReply:
    status: ReplyStatus
    answer: str
    cited_review_ids: tuple[str, ...] = ()


class ModelError(Exception):
    """The model call did not produce a usable reply."""


class ModelUnavailableError(ModelError):
    """The provider failed, refused the request or timed out."""


class ModelReplyError(ModelError):
    """The provider answered, but not in the ModelReply shape."""


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelReply: ...
