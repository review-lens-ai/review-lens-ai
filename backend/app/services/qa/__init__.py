"""S2-010: Review-grounded Q&A.

    answer_question(owner_id=..., target_id=..., question=..., targets=...,
                    reviews=..., search=..., make_model=...)  -> AnswerResponse

orchestrator.py  the steps in order: checks, messages, loop, answer
agent.py         the tool loop: the model asks, our code runs the tools
prompt.py        the question check, the system prompt, the full-mode listing
answer.py        submit_answer and the answer built from the cited reviews
tools/           what the model can call, fixed to one owner and one target

The names below load on first use, not on import. tools/ is imported by
app.repositories.review_search, which the orchestrator itself imports; loading
the orchestrator here would make that a circular import.
"""

from importlib import import_module

_EXPORTS = {
    "InvalidQuestionError": "app.services.qa.prompt",
    "NoReviewsError": "app.services.qa.orchestrator",
    "QAConfig": "app.services.qa.orchestrator",
    "QAContextError": "app.services.qa.orchestrator",
    "QAUnavailableError": "app.services.qa.orchestrator",
    "answer_question": "app.services.qa.orchestrator",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(import_module(_EXPORTS[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
