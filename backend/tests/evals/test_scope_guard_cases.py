"""S2-004: the scope-guard evaluation set is complete and well formed.

These tests check the question file itself, not the model. The Q&A tests (S2-011
onwards) and the live evaluation script read the same file, so a broken or
lopsided set would weaken every check built on it.
"""

import json
from collections import Counter

import pytest

from app.schemas.qa import RefusalReason
from tests.evals.scope_guard import CASES_FILE, load_cases

# The five categories S2-004 requires, plus OTHER_LOCATION (split out of
# OTHER_ENTITY, as in the architecture doc's refusal table), and the behavior
# each one expects.
EXPECTED_BY_CATEGORY = {
    "IN_SCOPE": "ANSWERED",
    "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
    "GENERAL_KNOWLEDGE": "OUT_OF_SCOPE",
    "OTHER_ENTITY": "OUT_OF_SCOPE",
    "OTHER_LOCATION": "OUT_OF_SCOPE",
    "OTHER_PLATFORM": "OUT_OF_SCOPE",
}

# Why a case in each category should be refused (app.schemas.qa.RefusalReason).
# IN_SCOPE cases are answered, so they have no reason.
REASON_BY_CATEGORY = {
    "INSUFFICIENT_EVIDENCE": "NO_EVIDENCE",
    "GENERAL_KNOWLEDGE": "GENERAL_KNOWLEDGE",
    "OTHER_ENTITY": "OTHER_ENTITY",
    "OTHER_LOCATION": "OTHER_LOCATION",
    "OTHER_PLATFORM": "OTHER_PLATFORM",
}

# "Multiple examples" per category, without growing into a redundant list.
MIN_PER_CATEGORY = 3
MAX_TOTAL = 30

CASE_FIELDS = {"id", "category", "question", "expected", "why"}
OPTIONAL_FIELDS = {"expected_reason", "must_not_contain"}
# must_not_contain stays a short check on obvious made-up answers, never a
# blocklist of wording (S2-BR-020).
MAX_FORBIDDEN_PER_CASE = 3


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
        assert CASE_FIELDS <= set(case) <= CASE_FIELDS | OPTIONAL_FIELDS, case.get("id")
        for field in CASE_FIELDS:
            assert isinstance(case[field], str), (case["id"], field)
            assert case[field].strip(), (case["id"], field)


def test_every_refusal_case_expects_its_categorys_reason(cases: list[dict]) -> None:
    for case in cases:
        assert case.get("expected_reason") == REASON_BY_CATEGORY.get(
            case["category"]
        ), case["id"]


def test_the_reasons_are_the_ones_the_app_returns() -> None:
    # A renamed or removed RefusalReason must not leave the set checking a name
    # the app can never send.
    assert set(REASON_BY_CATEGORY.values()) == {r.value for r in RefusalReason}


def test_forbidden_text_is_a_short_list_of_real_strings(cases: list[dict]) -> None:
    for case in cases:
        forbidden = case.get("must_not_contain")
        if forbidden is None:
            continue
        assert isinstance(forbidden, list), case["id"]
        assert 1 <= len(forbidden) <= MAX_FORBIDDEN_PER_CASE, case["id"]
        for text in forbidden:
            assert isinstance(text, str) and text.strip(), case["id"]


def test_in_scope_cases_have_no_forbidden_text(cases: list[dict]) -> None:
    # An answer may legitimately say almost anything the reviews support.
    for case in cases:
        if case["category"] == "IN_SCOPE":
            assert "must_not_contain" not in case, case["id"]


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


# Edge cases that catch real failures: a model that refuses a valid comparison, one that
# drops its rules when told to, and one that answers about a business it was never given.
EDGE_CASES = {
    "in-scope-service-vs-food",
    "general-ignore-rules-weather",
    "other-entity-unnamed-competitor",
}


def test_the_set_keeps_its_edge_cases(cases: list[dict]) -> None:
    assert EDGE_CASES <= {case["id"] for case in cases}


def test_runner_loads_every_case(cases: list[dict]) -> None:
    loaded = load_cases()

    assert [case.id for case in loaded] == [case["id"] for case in cases]
    assert [case.expected_reason for case in loaded] == [
        case.get("expected_reason") for case in cases
    ]
    assert [list(case.must_not_contain) for case in loaded] == [
        case.get("must_not_contain", []) for case in cases
    ]
