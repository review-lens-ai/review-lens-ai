"""S2-010: the OpenRouter ChatModel, without calling OpenRouter.

It is built from settings, refuses to start without a key and a model, passes the
tools and tool_choice to LangChain, and turns every provider error into
ModelUnavailableError while letting real bugs through.
"""

from types import SimpleNamespace

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr

from app.llm import ModelUnavailableError
from app.llm.openrouter import OPENROUTER_BASE_URL, OpenRouterChatModel, make_chat_model

SPEC = {
    "name": "count_reviews",
    "description": "Count.",
    "parameters": {"type": "object"},
}


def settings(**overrides):
    values = {
        "openrouter_api_key": SecretStr("sk-test-0123456789"),
        "qa_model": "provider/model-name",
        "qa_timeout_seconds": 30,
    }
    return SimpleNamespace(**{**values, **overrides})


@pytest.mark.parametrize(
    "missing", [{"openrouter_api_key": None}, {"qa_model": None}, {"qa_model": ""}]
)
def test_without_a_key_or_a_model_it_is_unavailable(missing):
    with pytest.raises(ModelUnavailableError):
        make_chat_model(settings(**missing))


def test_it_talks_to_openrouter_with_the_configured_model():
    chat = make_chat_model(settings())._chat

    assert chat.openai_api_base == OPENROUTER_BASE_URL
    assert chat.model_name == "provider/model-name"
    assert chat.temperature == 0
    assert chat.request_timeout == 30
    assert chat.openai_api_key.get_secret_value() == "sk-test-0123456789"


def test_bind_tools_sends_the_specs_and_tool_choice_in_openai_form():
    bound = make_chat_model(settings()).bind_tools([SPEC], tool_choice="count_reviews")
    kwargs = bound._runnable.kwargs

    assert kwargs["tools"][0]["function"]["name"] == "count_reviews"
    assert kwargs["tool_choice"] == {
        "type": "function",
        "function": {"name": "count_reviews"},
    }


class StubChat:
    """Stands in for ChatOpenAI: records bind_tools, then replies or raises."""

    def __init__(self, outcome):
        self.outcome, self.bound = outcome, None

    def bind_tools(self, tools, tool_choice=None):
        self.bound = (tools, tool_choice)
        return self

    def invoke(self, messages):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def test_a_reply_is_returned_as_the_models_message():
    reply = AIMessage("hello")
    stub = StubChat(reply)
    model = OpenRouterChatModel(stub)

    assert model.bind_tools([SPEC]).invoke([HumanMessage("hi")]) is reply
    assert stub.bound == ([SPEC], None)


REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


@pytest.mark.parametrize(
    "error",
    [
        openai.APITimeoutError(request=REQUEST),
        openai.APIConnectionError(request=REQUEST),
        openai.RateLimitError(
            "rate limited, key sk-test-0123456789",
            response=httpx.Response(429, request=REQUEST),
            body=None,
        ),
    ],
    ids=["timeout", "connection", "rate-limit"],
)
def test_provider_errors_become_model_unavailable_without_their_text(error):
    model = OpenRouterChatModel(StubChat(error))

    with pytest.raises(ModelUnavailableError) as exc:
        model.bind_tools([SPEC]).invoke([HumanMessage("hi")])

    assert str(exc.value) == type(error).__name__
    assert "sk-test" not in str(exc.value)
    assert exc.value.__cause__ is error  # kept for the server log


def test_a_bug_is_not_mistaken_for_a_provider_failure():
    model = OpenRouterChatModel(StubChat(RuntimeError("bug")))

    with pytest.raises(RuntimeError):
        model.bind_tools([SPEC]).invoke([HumanMessage("hi")])
