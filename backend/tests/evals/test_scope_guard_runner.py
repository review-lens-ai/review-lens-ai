"""S2-004: the runner judges responses by status, never by wording.

The `ask` functions here are fakes that stand in for the Q&A pipeline.
"""

import pytest

from app.schemas.questions import AnswerResponse
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


def well_behaved(question: str) -> AnswerResponse:
    """A pipeline that always responds the way the set expects."""
    return AnswerResponse(status=EXPECTED_BY_QUESTION[question], answer="Some text.")


def always_answers(question: str) -> AnswerResponse:
    """A pipeline with no scope guard: it answers everything."""
    return AnswerResponse(status="ANSWERED", answer="Here's what I found.")


def case(expected: str = "OUT_OF_SCOPE") -> ScopeGuardCase:
    return ScopeGuardCase("c1", "GENERAL_KNOWLEDGE", "What's 2 + 2?", expected, "")


def test_a_well_behaved_pipeline_passes_every_case() -> None:
    report = run(well_behaved)

    assert report.failures == []
    assert len(report.results) == len(CASES)


def test_a_pipeline_without_a_scope_guard_passes_only_in_scope_cases() -> None:
    report = run(always_answers)

    scores = report.by_category()
    assert scores["IN_SCOPE"] == (6, 6)
    for category in (
        "INSUFFICIENT_EVIDENCE",
        "GENERAL_KNOWLEDGE",
        "OTHER_ENTITY",
        "OTHER_PLATFORM",
    ):
        passed, total = scores[category]
        assert passed == 0 and total > 0, category


def test_wording_does_not_matter_only_the_status() -> None:
    wordy = AnswerResponse(status="OUT_OF_SCOPE", answer="I only know these reviews.")
    terse = AnswerResponse(status="OUT_OF_SCOPE", answer="No.")

    assert judge(case(), wordy).passed
    assert judge(case(), terse).passed


def test_a_blank_answer_fails_even_with_the_right_status() -> None:
    result = judge(case(), AnswerResponse(status="OUT_OF_SCOPE", answer="  "))

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
