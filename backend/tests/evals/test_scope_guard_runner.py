"""S2-004: the runner judges responses by status and refusal reason, never by wording.

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
CASE_BY_QUESTION = {case.question: case for case in CASES}
OUT_OF_SCOPE_CATEGORIES = (
    "GENERAL_KNOWLEDGE",
    "OTHER_ENTITY",
    "OTHER_LOCATION",
    "OTHER_PLATFORM",
)


def reply(status: str, answer: str, reason: str | None = None) -> AnswerResponse:
    """A reply in the API's real shape (app/schemas/qa.py), with plausible context."""
    return AnswerResponse(
        status=status,
        answer=answer,
        evidence=[],
        refusal_reason=reason,
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
    expected = CASE_BY_QUESTION[question]
    return reply(expected.expected, "Some text.", expected.expected_reason)


def always_answers(question: str) -> AnswerResponse:
    """A pipeline with no scope guard: it answers everything."""
    return reply("ANSWERED", "Here's what I found.")


def always_refuses(question: str) -> AnswerResponse:
    """A pipeline that declines everything with the same reason, even what the
    reviews can answer."""
    return reply(
        "OUT_OF_SCOPE",
        "I can only answer questions about these reviews.",
        "GENERAL_KNOWLEDGE",
    )


def case(
    expected: str = "OUT_OF_SCOPE",
    reason: str | None = None,
    must_not_contain: tuple[str, ...] = (),
) -> ScopeGuardCase:
    return ScopeGuardCase(
        "c1", "GENERAL_KNOWLEDGE", "What's 2 + 2?", expected, "", reason,
        must_not_contain,
    )  # fmt: skip


def test_a_well_behaved_pipeline_passes_every_case() -> None:
    report = run(well_behaved)

    assert report.failures == []
    assert len(report.results) == len(CASES)


def test_a_pipeline_without_a_scope_guard_passes_only_in_scope_cases() -> None:
    report = run(always_answers)

    scores = report.by_category()
    passed, total = scores["IN_SCOPE"]
    assert passed == total > 0
    for category in ("INSUFFICIENT_EVIDENCE", *OUT_OF_SCOPE_CATEGORIES):
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
    # Its one reason fits general-knowledge questions only.
    passed, total = scores["GENERAL_KNOWLEDGE"]
    assert passed == total > 0
    # Other businesses, locations and platforms get the right status but the
    # wrong reason, so the user would be told the wrong thing.
    for category in ("OTHER_ENTITY", "OTHER_LOCATION", "OTHER_PLATFORM"):
        passed, total = scores[category]
        assert passed == 0 and total > 0, category


def test_the_right_refusal_reason_passes() -> None:
    result = judge(
        case(reason="OTHER_PLATFORM"),
        reply("OUT_OF_SCOPE", "Only Google Maps reviews.", "OTHER_PLATFORM"),
    )

    assert result.passed


@pytest.mark.parametrize(
    ("given", "shown"),
    [("GENERAL_KNOWLEDGE", "GENERAL_KNOWLEDGE"), (None, "no reason")],
    ids=["wrong-reason", "no-reason"],
)
def test_a_refusal_with_the_wrong_or_no_reason_fails(given, shown) -> None:
    result = judge(case(reason="OTHER_PLATFORM"), reply("OUT_OF_SCOPE", "No.", given))

    assert not result.passed
    assert result.error == f"expected reason OTHER_PLATFORM, got {shown}"


def test_a_wrong_status_is_reported_before_the_reason() -> None:
    result = judge(case(reason="OTHER_PLATFORM"), reply("ANSWERED", "Yelp says 4.5."))

    assert not result.passed
    assert result.actual == "ANSWERED"
    assert result.error is None  # reported as "expected OUT_OF_SCOPE, got ANSWERED"


def test_an_in_scope_case_does_not_check_a_reason() -> None:
    result = judge(case("ANSWERED"), reply("ANSWERED", "People love the crust."))

    assert result.passed


def test_forbidden_text_fails_the_case_ignoring_case() -> None:
    forbidden = case(reason="GENERAL_KNOWLEDGE", must_not_contain=("Paris",))
    leaked = reply("OUT_OF_SCOPE", "I can't help, but it's PARIS.", "GENERAL_KNOWLEDGE")
    clean = reply("OUT_OF_SCOPE", "I only know these reviews.", "GENERAL_KNOWLEDGE")

    assert judge(forbidden, leaked).error == "answer contains 'Paris'"
    assert judge(forbidden, clean).passed


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


def test_summary_says_when_the_reason_was_wrong() -> None:
    result = judge(
        case(reason="OTHER_PLATFORM"),
        reply("OUT_OF_SCOPE", "No.", "GENERAL_KNOWLEDGE"),
    )

    summary = EvalReport([result]).summary()

    assert (
        "FAILED c1: expected reason OTHER_PLATFORM, got GENERAL_KNOWLEDGE"
        " (What's 2 + 2?)"
    ) in summary


def test_a_case_with_an_unknown_expected_status_is_rejected(tmp_path) -> None:
    bad = tmp_path / "cases.json"
    bad.write_text(
        '{"target": {}, "cases": [{"id": "x", "category": "IN_SCOPE",'
        ' "question": "q", "expected": "MAYBE", "why": "w"}]}'
    )

    with pytest.raises(ValueError):
        load_cases(bad)


def test_a_case_with_an_unknown_expected_reason_is_rejected(tmp_path) -> None:
    bad = tmp_path / "cases.json"
    bad.write_text(
        '{"target": {}, "cases": [{"id": "x", "category": "OTHER_PLATFORM",'
        ' "question": "q", "expected": "OUT_OF_SCOPE",'
        ' "expected_reason": "OTHER_PLATFROM", "why": "w"}]}'
    )

    with pytest.raises(ValueError):
        load_cases(bad)
