"""S2-004: runs the scope-guard evaluation set and judges each response.

The runner takes any `ask` function, so the same questions run in two places:

- CI: `ask` calls the Q&A pipeline with a fake model (S2-003), so results never
  depend on a live model (S2-BR-029).
- By hand: `ask` calls the pipeline with the real model, to check the system
  prompt actually works (S2-BR-031).

A response passes when its status matches the case's expected behavior, it has
some text for the user, a refusal gives the expected reason (expected_reason),
and the answer contains none of the case's must_not_contain text. Wording is
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
    # Why a refusal should refuse; None for questions that should be answered.
    expected_reason: RefusalReason | None = None
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
            expected_reason=(
                _REASON.validate_python(case["expected_reason"])
                if "expected_reason" in case
                else None
            ),
            must_not_contain=tuple(case.get("must_not_contain", ())),
        )
        for case in raw["cases"]
    ]


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
    the expected reason, and contains none of the case's forbidden text. The status
    is checked first, so a wrong status is reported as such."""
    if not response.answer.strip():
        return CaseResult(case, response.status, error="empty answer")
    if response.status != case.expected:
        return CaseResult(case, response.status)
    if case.expected_reason and response.refusal_reason != case.expected_reason:
        got = response.refusal_reason or "no reason"
        return CaseResult(
            case,
            response.status,
            error=f"expected reason {case.expected_reason}, got {got}",
        )
    answer = response.answer.lower()
    for text in case.must_not_contain:
        if text.lower() in answer:
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
