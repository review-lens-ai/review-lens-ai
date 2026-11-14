"""S2-010: the boundary between ReviewLens and an LLM provider.

The app talks to a model only through ChatModel (client.py). The real one is
built by app.llm.openrouter.make_chat_model; tests use a fake that records every
call. openrouter is not imported here, so importing app.llm never loads a
provider SDK.
"""

from app.llm.client import BoundChatModel, ChatModel, ModelError, ModelUnavailableError

__all__ = ["BoundChatModel", "ChatModel", "ModelError", "ModelUnavailableError"]
