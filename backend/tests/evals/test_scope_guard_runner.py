"""S2-004: the runner judges responses by status, never by wording.

The `ask` functions here are fakes that stand in for the Q&A pipeline.
"""

from uuid import UUID

import pytest

from app.schemas.qa import AnswerContext, AnswerResponse
from tests.evals.scope_guard import (
    CaseResult,
    EvalReport,
    ScopeGuardCase,
    judge,
    load_cases,
    run,
)

CASES = load_cases()
EXPECTED_BY_QUESTION = {case.question: case.expected for case in CASES}


def reply(status: str, answer: str) -> AnswerResponse:
    """A reply in the API's real shape (app/schemas/qa.py), with plausible context."""
    return AnswerResponse(
        status=status,
        answer=answer,
        evidence=[],
        context=AnswerContext(
            target_id=UUID(int=1),
            target_name="Joe's Pizza",
            platform="google_maps",
            reviews_considered=16,
            reviews_total=16,
            is_subset=False,
        ),
    )


def well_behaved(question: str) -> AnswerResponse:
    """A pipeline that always responds the way the set expects."""
    return reply(EXPECTED_BY_QUESTION[question], "Some text.")


def always_answers(question: str) -> AnswerResponse:
    """A pipeline with no scope guard: it answers everything."""
    return reply("ANSWERED", "Here's what I found.")


def always_refuses(question: str) -> AnswerResponse:
    """A pipeline that declines everything, even what the reviews can answer."""
    return reply("OUT_OF_SCOPE", "I can only answer questions about these reviews.")


def case(expected: str = "OUT_OF_SCOPE") -> ScopeGuardCase:
    return ScopeGuardCase("c1", "GENERAL_KNOWLEDGE", "What's 2 + 2?", expected, "")


def test_a_well_behaved_pipeline_passes_every_case() -> None:
    report = run(well_behaved)

    assert report.failures == []
    assert len(report.results) == len(CASES)


def test_a_pipeline_without_a_scope_guard_passes_only_in_scope_cases() -> None:
    report = run(always_answers)

    scores = report.by_category()
    passed, total = scores["IN_SCOPE"]
    assert passed == total > 0
    for category in (
        "INSUFFICIENT_EVIDENCE",
        "GENERAL_KNOWLEDGE",
        "OTHER_ENTITY",
        "OTHER_PLATFORM",
    ):
        passed, total = scores[category]
        assert passed == 0 and total > 0, category


def test_a_pipeline_that_refuses_everything_fails_what_it_should_answer() -> None:
    scores = run(always_refuses).by_category()

    # It fails every in-scope question, including the comparison inside the reviews.
    passed, total = scores["IN_SCOPE"]
    assert passed == 0 and total > 0
    # A refusal is not an honest "the reviews don't say", so these fail too.
    passed, total = scores["INSUFFICIENT_EVIDENCE"]
    assert passed == 0 and total > 0
    # Declining out-of-scope questions is the one thing it gets right.
    for category in ("GENERAL_KNOWLEDGE", "OTHER_ENTITY", "OTHER_PLATFORM"):
        passed, total = scores[category]
        assert passed == total, category


def test_wording_does_not_matter_only_the_status() -> None:
    wordy = reply("OUT_OF_SCOPE", "I only know these reviews.")
    terse = reply("OUT_OF_SCOPE", "No.")

    assert judge(case(), wordy).passed
    assert judge(case(), terse).passed


def test_a_blank_answer_fails_even_with_the_right_status() -> None:
    result = judge(case(), reply("OUT_OF_SCOPE", "  "))

    assert not result.passed
    assert result.error == "empty answer"


def test_a_call_that_raises_fails_that_case_and_the_run_continues() -> None:
    first, second = CASES[0], CASES[1]

    def flaky(question: str) -> AnswerResponse:
        if question == first.question:
            raise TimeoutError("model timed out")
        return well_behaved(question)

    report = run(flaky, [first, second])

    assert [r.passed for r in report.results] == [False, True]
    assert report.results[0].actual is None
    assert "TimeoutError" in (report.results[0].error or "")


def test_summary_lists_scores_and_each_failure() -> None:
    passing = CaseResult(case(), "OUT_OF_SCOPE")
    failing = CaseResult(case(), "ANSWERED")

    summary = EvalReport([passing, failing]).summary()

    assert "GENERAL_KNOWLEDGE: 1/2 passed" in summary
    assert "Total: 1/2 passed" in summary
    assert "FAILED c1: expected OUT_OF_SCOPE, got ANSWERED (What's 2 + 2?)" in summary


def test_a_case_with_an_unknown_expected_status_is_rejected(tmp_path) -> None:
    bad = tmp_path / "cases.json"
    bad.write_text(
        '{"target": {}, "cases": [{"id": "x", "category": "IN_SCOPE",'
        ' "question": "q", "expected": "MAYBE", "why": "w"}]}'
    )

    with pytest.raises(ValueError):
        load_cases(bad)
