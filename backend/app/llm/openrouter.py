"""S2-010: the ChatModel behind Q&A, through OpenRouter.

    model = make_chat_model(get_settings())   # ModelUnavailableError if not configured

OpenRouter speaks the OpenAI API, so LangChain's ChatOpenAI talks to it with a
different base_url. This is the only module that imports langchain_openai or
openai; the rest of the app sees the ChatModel protocol (app/llm/client.py).

- temperature=0, so the same question gives answers as close as the model allows.
- Every openai error (timeout, connection, rate limit, 4xx/5xx) inherits from
  openai.APIError and becomes ModelUnavailableError. The provider's text is kept on
  the exception for the server log, never shown to users.
"""

from collections.abc import Sequence

import openai
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.llm.client import ModelUnavailableError

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class _BoundOpenRouter:
    def __init__(self, runnable: Runnable) -> None:
        self._runnable = runnable

    def invoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        try:
            return self._runnable.invoke(list(messages))
        except openai.APIError as exc:
            raise ModelUnavailableError(type(exc).__name__) from exc


class OpenRouterChatModel:
    def __init__(self, chat: ChatOpenAI) -> None:
        self._chat = chat

    def bind_tools(
        self, tools: Sequence[dict], *, tool_choice: str | None = None
    ) -> _BoundOpenRouter:
        return _BoundOpenRouter(self._chat.bind_tools(tools, tool_choice=tool_choice))


def make_chat_model(settings) -> OpenRouterChatModel:
    if settings.openrouter_api_key is None or not settings.qa_model:
        raise ModelUnavailableError("OPENROUTER_API_KEY and QA_MODEL must be set")
    return OpenRouterChatModel(
        ChatOpenAI(
            model=settings.qa_model,
            base_url=OPENROUTER_BASE_URL,
            api_key=settings.openrouter_api_key,
            timeout=settings.qa_timeout_seconds,
            temperature=0,
            max_retries=1,
        )
    )
