"""S2-004: runs the scope-guard evaluation set and judges each response.

The runner takes any `ask` function, so the same questions run in two places:

- CI: `ask` calls the Q&A pipeline with a fake model (S2-003), so results never
  depend on a live model (S2-BR-029).
- By hand: `ask` calls the pipeline with the real model, to check the system
  prompt actually works (S2-BR-031).

A response passes when its status matches the case's expected behavior and it
has some text for the user. Wording is never compared.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from app.schemas.qa import AnswerResponse, AnswerStatus
from app.schemas.reviews import ReviewCreate

EVALS_DIR = Path(__file__).parent
CASES_FILE = EVALS_DIR / "scope_guard_cases.json"
# The reviews every case is asked about (the set's "target").
REVIEWS_FILE = EVALS_DIR / "joes_pizza_reviews.json"

_STATUS = TypeAdapter(AnswerStatus)
_REVIEWS = TypeAdapter(list[ReviewCreate])


@dataclass(frozen=True)
class ScopeGuardCase:
    id: str
    category: str
    question: str
    expected: AnswerStatus
    why: str


def load_cases(path: Path = CASES_FILE) -> list[ScopeGuardCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        ScopeGuardCase(
            id=case["id"],
            category=case["category"],
            question=case["question"],
            expected=_STATUS.validate_python(case["expected"]),
            why=case["why"],
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
    # Why the case failed without a status to compare, e.g. the call raised.
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.actual == self.case.expected


def judge(case: ScopeGuardCase, response: AnswerResponse) -> CaseResult:
    """Passes when the response is the expected kind and says something."""
    if not response.answer.strip():
        return CaseResult(case, response.status, error="empty answer")
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
            got = result.error or f"got {result.actual}"
            lines.append(
                f"FAILED {result.case.id}: expected {result.case.expected}, {got}"
                f" ({result.case.question})"
            )
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
