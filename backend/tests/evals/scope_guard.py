"""S2-004: runs the scope-guard evaluation set and judges each response.

The runner takes any `ask` function, so the same questions run in two places:

- CI: `ask` calls the Q&A pipeline with a fake model (S2-003), so results never
  depend on a live model (S2-BR-029).
- By hand: `ask` calls the pipeline with the real model, to check the system
  prompt actually works (S2-BR-031).

A response passes when its status matches the case's expected behavior, it has
some text for the user, a refusal gives one of the expected reasons
(expected_reason), and the answer contains none of the case's must_not_contain text. Wording is
never compared otherwise.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from app.schemas.qa import AnswerResponse, AnswerStatus, RefusalReason
from app.schemas.reviews import ReviewCreate

EVALS_DIR = Path(__file__).parent
CASES_FILE = EVALS_DIR / "scope_guard_cases.json"
# The reviews every case is asked about (the set's "target").
REVIEWS_FILE = EVALS_DIR / "joes_pizza_reviews.json"

_STATUS = TypeAdapter(AnswerStatus)
_REASON = TypeAdapter(RefusalReason)
_REVIEWS = TypeAdapter(list[ReviewCreate])


@dataclass(frozen=True)
class ScopeGuardCase:
    id: str
    category: str
    question: str
    expected: AnswerStatus
    why: str
    # The reasons a refusal may give: one, or several when the question breaks more
    # than one rule ("Domino's on Amazon" is another business and another platform).
    # Empty for questions that should be answered.
    expected_reasons: tuple[RefusalReason, ...] = ()
    # Text only a made-up answer would contain, matched ignoring case.
    must_not_contain: tuple[str, ...] = ()


def load_cases(path: Path = CASES_FILE) -> list[ScopeGuardCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        ScopeGuardCase(
            id=case["id"],
            category=case["category"],
            question=case["question"],
            expected=_STATUS.validate_python(case["expected"]),
            why=case["why"],
            expected_reasons=_reasons(case.get("expected_reason")),
            must_not_contain=tuple(case.get("must_not_contain", ())),
        )
        for case in raw["cases"]
    ]


def _reasons(raw: str | list[str] | None) -> tuple[RefusalReason, ...]:
    """expected_reason is one reason, or a list when several are correct."""
    if raw is None:
        return ()
    names = [raw] if isinstance(raw, str) else raw
    return tuple(_REASON.validate_python(name) for name in names)


def load_target(path: Path = CASES_FILE) -> dict[str, str]:
    """The target every case is asked about: its name and platform."""
    return json.loads(path.read_text(encoding="utf-8"))["target"]


def load_reviews(path: Path = REVIEWS_FILE) -> list[ReviewCreate]:
    """The target's reviews, in the shape the ingestion pipeline saves."""
    return _REVIEWS.validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class CaseResult:
    case: ScopeGuardCase
    # None when no usable response came back (see error).
    actual: AnswerStatus | None
    # Why the case failed apart from the status: the call raised, the answer was
    # blank, the refusal gave the wrong reason, or it contained forbidden text.
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.actual == self.case.expected


def judge(case: ScopeGuardCase, response: AnswerResponse) -> CaseResult:
    """Passes when the response is the expected kind, says something, refuses for
    an expected reason, carries no review evidence when it refuses, and contains
    none of the case's forbidden text. The status is checked first, so a wrong status
    is reported as such."""
    if not response.answer.strip():
        return CaseResult(case, response.status, error="empty answer")
    if response.status != case.expected:
        return CaseResult(case, response.status)
    if case.expected_reasons and response.refusal_reason not in case.expected_reasons:
        wanted = " or ".join(case.expected_reasons)
        got = response.refusal_reason or "no reason"
        return CaseResult(
            case, response.status, error=f"expected reason {wanted}, got {got}"
        )
    # A refusal never claims review evidence (S2-BR-010, S2-BR-021).
    if response.status != AnswerStatus.ANSWERED and response.evidence:
        return CaseResult(
            case, response.status, error="refusal carries review evidence"
        )
    answer, question = response.answer.lower(), case.question.lower()
    for text in case.must_not_contain:
        # A refusal may repeat the user's own words ("I can't confirm $30"), so text
        # the question itself contains is not evidence of a made-up answer.
        if text.lower() in answer and text.lower() not in question:
            return CaseResult(case, response.status, error=f"answer contains {text!r}")
    return CaseResult(case, response.status)


@dataclass(frozen=True)
class EvalReport:
    results: list[CaseResult]

    @property
    def failures(self) -> list[CaseResult]:
        return [result for result in self.results if not result.passed]

    def by_category(self) -> dict[str, tuple[int, int]]:
        """Passed and total cases per category, in the set's order."""
        counts: dict[str, tuple[int, int]] = {}
        for result in self.results:
            passed, total = counts.get(result.case.category, (0, 0))
            counts[result.case.category] = (passed + result.passed, total + 1)
        return counts

    def summary(self) -> str:
        """A scorecard for a person: totals per category, then each failure."""
        lines = [
            f"{category}: {passed}/{total} passed"
            for category, (passed, total) in self.by_category().items()
        ]
        passed_total = len(self.results) - len(self.failures)
        lines.append(f"Total: {passed_total}/{len(self.results)} passed")
        for result in self.failures:
            if result.error:
                problem = result.error
            else:
                problem = f"expected {result.case.expected}, got {result.actual}"
            lines.append(f"FAILED {result.case.id}: {problem} ({result.case.question})")
        return "\n".join(lines)


def run(
    ask: Callable[[str], AnswerResponse],
    cases: list[ScopeGuardCase] | None = None,
) -> EvalReport:
    """Asks every case and judges it. One failing call never stops the rest."""
    results = []
    for case in load_cases() if cases is None else cases:
        try:
            response = ask(case.question)
        except Exception as error:  # report it as this case's failure, then go on
            results.append(CaseResult(case, None, error=f"raised {error!r}"))
            continue
        results.append(judge(case, response))
    return EvalReport(results)
