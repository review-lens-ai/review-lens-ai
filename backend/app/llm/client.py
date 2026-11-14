"""S2-010: the boundary between ReviewLens and an LLM provider.

    bound = model.bind_tools(tool_specs, tool_choice=None)   # or "submit_answer"
    reply = bound.invoke(messages)                           # AIMessage

The rest of the app talks to a model only through ChatModel. Messages are
LangChain's provider-neutral types (SystemMessage, HumanMessage, AIMessage,
ToolMessage); nothing outside app/llm/openrouter.py imports a provider SDK.

Rules for every implementation:
- TOOLS AS SPECS: tools are dicts {name, description, parameters (JSON schema)}.
  tool_choice names one tool the reply must call, or None for the model's choice.
- REPLIES: an AIMessage. Its tool_calls are [{name, args, id}]; with none, it is
  plain text.
- FAILURES: a provider error or timeout is ModelUnavailableError. Provider text
  stays in the exception for server logs; users never see it.
- NO MEMORY: a model keeps nothing between calls. Everything it should know is in
  the messages of that call (see services/qa/agent.py).

Tests use FakeChatModel (tests/unit/qa/conftest.py), which records every call.
"""

from collections.abc import Sequence
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage


class ModelError(Exception):
    """The model call did not produce a usable reply."""


class ModelUnavailableError(ModelError):
    """The provider failed, refused the request, timed out or is not configured."""


class BoundChatModel(Protocol):
    def invoke(self, messages: Sequence[BaseMessage]) -> AIMessage: ...


class ChatModel(Protocol):
    def bind_tools(
        self, tools: Sequence[dict], *, tool_choice: str | None = None
    ) -> BoundChatModel: ...
