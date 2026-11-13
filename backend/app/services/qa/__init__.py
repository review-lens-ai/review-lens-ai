"""S2-003: Review-grounded Q&A orchestration.

    answer_question(owner_id=..., target_id=..., question=...,
                    targets=..., reviews=..., model=...)  -> AnswerResponse

context.py       which reviews go to the model, checked against the active target
prompt.py        the question check, the system prompt and the review block
answer.py        the model's reply checked and turned into an AnswerResponse
orchestrator.py  the steps above, in order; it holds no rules of its own
"""

from app.services.qa.orchestrator import QAUnavailableError, answer_question
from app.services.qa.prompt import InvalidQuestionError

__all__ = ["InvalidQuestionError", "QAUnavailableError", "answer_question"]
