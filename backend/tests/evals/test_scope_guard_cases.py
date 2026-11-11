"""S2-004: the scope-guard evaluation set is complete and well formed.

These tests check the question file itself, not the model. The Q&A tests (S2-011
onwards) and the live evaluation script read the same file, so a broken or
lopsided set would weaken every check built on it.
"""

import json
from collections import Counter

import pytest

from tests.evals.scope_guard import CASES_FILE, load_cases

# The five categories S2-004 requires, and the behavior each one expects.
EXPECTED_BY_CATEGORY = {
    "IN_SCOPE": "ANSWERED",
    "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
    "GENERAL_KNOWLEDGE": "OUT_OF_SCOPE",
    "OTHER_ENTITY": "OUT_OF_SCOPE",
    "OTHER_PLATFORM": "OUT_OF_SCOPE",
}

# "Multiple examples" per category, without growing into a redundant list.
MIN_PER_CATEGORY = 3
MAX_TOTAL = 30

CASE_FIELDS = {"id", "category", "question", "expected", "why"}


@pytest.fixture(scope="module")
def eval_set() -> dict:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases(eval_set: dict) -> list[dict]:
    return eval_set["cases"]


def test_eval_set_names_one_google_maps_target(eval_set: dict) -> None:
    assert eval_set["target"]["name"].strip()
    assert eval_set["target"]["platform"] == "google_maps"


def test_every_case_has_exactly_the_expected_fields(cases: list[dict]) -> None:
    for case in cases:
        assert set(case) == CASE_FIELDS, case.get("id")
        for field in CASE_FIELDS:
            assert isinstance(case[field], str), (case["id"], field)
            assert case[field].strip(), (case["id"], field)


def test_every_category_is_covered_by_several_cases(cases: list[dict]) -> None:
    counts = Counter(case["category"] for case in cases)

    assert set(counts) == set(EXPECTED_BY_CATEGORY)
    for category, count in counts.items():
        assert count >= MIN_PER_CATEGORY, category


def test_eval_set_stays_small(cases: list[dict]) -> None:
    assert len(cases) <= MAX_TOTAL


def test_each_case_expects_its_categorys_behavior(cases: list[dict]) -> None:
    for case in cases:
        assert case["expected"] == EXPECTED_BY_CATEGORY[case["category"]], case["id"]


def test_case_ids_and_questions_are_unique(cases: list[dict]) -> None:
    ids = [case["id"] for case in cases]
    questions = [case["question"].strip().lower() for case in cases]

    assert len(ids) == len(set(ids))
    assert len(questions) == len(set(questions))


def test_runner_loads_every_case(cases: list[dict]) -> None:
    loaded = load_cases()

    assert [case.id for case in loaded] == [case["id"] for case in cases]
