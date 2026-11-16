"""S2-004, S2-014, S2-015: every scope-guard case through the real Q&A pipeline, in CI.

The real orchestrator, tool loop, system prompt and answer builder run on the eval's
Joe's Pizza reviews. Only the model is fake: for each case it replies the way a
well-behaved model would, so the result is the same on every run (S2-BR-029).

What this proves is the application's half of the scope guard: each kind of reply
reaches the API response with the right status and reason, a refusal never carries
review evidence and always says something, an answer with no review behind it is not
passed off as an answer, and every question reaches the model with the scope prompt.

What it cannot prove is that a live model chooses the right reply. That is
`python -m tests.evals.live`, by hand and never in CI (S2-BR-031).
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.errors import NotFoundError
from app.schemas.qa import AnswerResponse, AnswerStatus, RefusalReason
from app.services.qa import QAConfig, answer_question
from app.services.qa.tools.guard import quote
from app.services.qa.tools.labels import LabelBook
from app.services.qa.tools.registry import Tool, ToolRegistry
from app.services.qa.tools.types import ToolResult
from tests.evals.scope_guard import (
    ScopeGuardCase,
    judge,
    load_cases,
    load_reviews,
    load_target,
    run,
)
from tests.unit.qa.conftest import (
    FakeChatModel,
    FakeToolSet,
    WordsArgs,
    call,
    submit,
)

CASES = load_cases()
CASE_BY_QUESTION = {case.question: case for case in CASES}
REFUSALS = [case for case in CASES if case.expected is not AnswerStatus.ANSWERED]
OUT_OF_SCOPE_OR_ANSWERED = [
    case for case in CASES if case.expected is not AnswerStatus.INSUFFICIENT_EVIDENCE
]

OWNER = "00000000-0000-0000-0000-0000000000e1"
TARGET = UUID("10000000-0000-0000-0000-0000000000e1")
TARGET_DATA = load_target()
DISTANT_PAST = datetime(1970, 1, 1, tzinfo=UTC)


def _id(case: ScopeGuardCase) -> str:
    return case.id


# --- The eval's reviews, in memory --------------------------------------------------


def _rows() -> list[dict]:
    """Joe's Pizza reviews as the repositories return them, newest first."""
    rows = [
        {
            "id": UUID(int=position),
            "target_id": TARGET,
            "rating": review.rating,
            "review_text": review.review_text,
            "reviewed_at": review.reviewed_at,
        }
        for position, review in enumerate(load_reviews(), start=1)
    ]
    return sorted(rows, key=lambda r: r["reviewed_at"] or DISTANT_PAST, reverse=True)


class EvalTargets:
    def get_owned(self, target_id, owner_id):
        if UUID(str(target_id)) != TARGET or str(owner_id) != OWNER:
            raise NotFoundError("Target not found.")
        return {
            "id": TARGET,
            "name": TARGET_DATA["name"],
            "platform": TARGET_DATA["platform"],
        }

    def place_address(self, target_id, owner_id):
        self.get_owned(target_id, owner_id)
        return TARGET_DATA.get("address")


class EvalReviews:
    def __init__(self, targets: EvalTargets):
        self.targets = targets

    def list_for_target(self, target_id, owner_id):
        self.targets.get_owned(target_id, owner_id)
        return _rows()

    def summary_for_target(self, target_id, owner_id):
        rows = self.list_for_target(target_id, owner_id)
        counts = {n: sum(r["rating"] == n for r in rows) for n in range(1, 6)}
        dates = [r["reviewed_at"] for r in rows if r["reviewed_at"]]
        return {
            "review_count": len(rows),
            "average_rating": round(sum(r["rating"] for r in rows) / len(rows), 2),
            "rating_counts": [{"rating": n, "count": counts[n]} for n in range(1, 6)],
            "first_reviewed_at": min(dates) if dates else None,
            "last_reviewed_at": max(dates) if dates else None,
        }


class EvalSearch:
    """What the tool set and the answer builder use of a ReviewSearchRepository."""

    def __init__(self, target_id: UUID):
        self.target_id = target_id

    def get_many(self, review_ids):
        wanted = set(review_ids)
        return [row for row in _rows() if row["id"] in wanted]


def _open_search(targets: EvalTargets):
    def open_search(target_id, owner_id):
        return EvalSearch(targets.get_owned(target_id, owner_id)["id"])

    return open_search


def _toolset(search: EvalSearch) -> FakeToolSet:
    """The real registry and labels, with one in-memory tool over the eval reviews."""
    labels, runs = LabelBook(), []

    def find_reviews(args: WordsArgs) -> ToolResult:
        runs.append(args.words)
        rows = _rows()
        lines, sources = [], []
        for row in rows:
            source = labels.source_for(row["id"], f"{row['rating']} stars")
            lines.append(f"{source.label} | {quote(row['review_text'], 300)}")
            sources.append(source)
        return ToolResult(text="\n".join(lines), sources=sources)

    registry = ToolRegistry(
        [Tool("find_reviews", "Find reviews.", WordsArgs, find_reviews)]
    )
    return FakeToolSet(registry=registry, labels=labels, runs=runs)


def ask(question: str, model: FakeChatModel) -> AnswerResponse:
    """One question through the real pipeline, with the scripted model."""
    targets = EvalTargets()
    return answer_question(
        owner_id=OWNER,
        target_id=TARGET,
        question=question,
        targets=targets,
        reviews=EvalReviews(targets),
        open_search=_open_search(targets),
        make_model=lambda: model,
        config=QAConfig(),
        make_toolset=_toolset,
    )


# --- The scripted model -------------------------------------------------------------


def well_behaved_model(case: ScopeGuardCase) -> FakeChatModel:
    """Replies the way a good model would: reads the reviews and cites one to answer,
    declines for the case's reason with a suggestion otherwise."""
    if case.expected is AnswerStatus.ANSWERED:
        return FakeChatModel(
            call("find_reviews"),
            submit("ANSWERED", "Reviewers like the crust.", cited=["R1"]),
        )
    extra = {"suggestions": ["What do reviewers like most?"]}
    if case.expected_reasons and case.expected is AnswerStatus.OUT_OF_SCOPE:
        extra["refusal_reason"] = case.expected_reasons[0].value
    return FakeChatModel(
        submit(case.expected.value, "I can't answer that from these reviews.", **extra)
    )


# --- The tests ----------------------------------------------------------------------


def test_the_whole_set_scores_full_marks_through_the_real_pipeline():
    report = run(
        lambda question: ask(question, well_behaved_model(CASE_BY_QUESTION[question]))
    )

    assert len(report.results) == len(CASES)
    assert report.failures == [], report.summary()


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_a_well_behaved_reply_reaches_the_response_as_the_case_expects(case):
    response = ask(case.question, well_behaved_model(case))

    result = judge(case, response)

    assert result.passed, result.error or f"got {response.status}"
    if case.expected is AnswerStatus.ANSWERED:
        assert response.evidence, "an answer shows the reviews behind it"
    else:
        assert response.evidence == [], "a refusal claims no review evidence"


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_every_question_reaches_the_model_with_the_scope_prompt(case):
    model = well_behaved_model(case)

    ask(case.question, model)

    system, question = model.calls[0]["messages"][:2]
    assert isinstance(system, SystemMessage) and isinstance(question, HumanMessage)
    assert question.content == case.question
    assert TARGET_DATA["name"] in system.content
    assert "Google Maps" in system.content
    assert TARGET_DATA["address"] in system.content
    for reason in RefusalReason:
        assert reason.value in system.content, f"the prompt defines {reason}"


@pytest.mark.parametrize("case", REFUSALS, ids=_id)
def test_a_blank_refusal_still_tells_the_user_why(case):
    extra = {}
    if case.expected is AnswerStatus.OUT_OF_SCOPE:
        extra["refusal_reason"] = case.expected_reasons[0].value
    model = FakeChatModel(submit(case.expected.value, "   ", **extra))

    response = ask(case.question, model)

    assert response.answer.strip(), "the fixed message for the reason is shown"
    assert judge(case, response).passed


@pytest.mark.parametrize("case", OUT_OF_SCOPE_OR_ANSWERED, ids=_id)
def test_an_answer_with_no_review_behind_it_is_not_passed_off_as_an_answer(case):
    model = FakeChatModel(submit("ANSWERED", "Sure, here you go.", cited=[]))

    response = ask(case.question, model)

    assert response.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.evidence == []
    assert not judge(case, response).passed
