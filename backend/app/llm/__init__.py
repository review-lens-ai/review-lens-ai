"""S2-003: the boundary between ReviewLens and an LLM provider.

The rest of the app talks to a model only through ModelClient. A real provider
adapter (for example one built on LangChain) will implement it later; tests use
a fake that records every ModelRequest and returns scripted ModelReply values.
"""

from app.llm.client import (
    ChatMessage,
    ModelClient,
    ModelError,
    ModelReply,
    ModelReplyError,
    ModelRequest,
    ModelUnavailableError,
    ReplyStatus,
)

__all__ = [
    "ChatMessage",
    "ModelClient",
    "ModelError",
    "ModelReply",
    "ModelReplyError",
    "ModelRequest",
    "ModelUnavailableError",
    "ReplyStatus",
]
