"""S2-014, S2-015: the live eval script's own logic, without a model or a database.

live.py asks the real model by hand; these tests check only the parts around it:
which cases it picks, that an unavailable model skips cases instead of failing
them, that it stops early, and that it counts requests. The `ask` functions are
fakes, so nothing here calls a model.
"""

import pytest

from tests.evals.live import (
    CountingModel,
    ModelBusyError,
    evaluate,
    main,
    scorecard,
    select_cases,
)
from tests.evals.scope_guard import load_cases
from tests.evals.test_scope_guard_runner import reply, well_behaved

CASES = load_cases()
CATEGORIES = list(dict.fromkeys(case.category for case in CASES))


# --- select_cases -----------------------------------------------------------------


def test_by_default_one_case_per_category_in_the_sets_order():
    first_of_each = [
        next(case for case in CASES if case.category == category)
        for category in CATEGORIES
    ]

    assert select_cases(CASES) == first_of_each


def test_all_picks_every_case():
    assert select_cases(CASES, run_all=True) == CASES


def test_cases_and_categories_pick_exactly_those():
    chosen = select_cases(
        CASES, case_ids=["general-weather"], categories=["OTHER_PLATFORM"]
    )

    assert {case.id for case in chosen} == {"general-weather"} | {
        case.id for case in CASES if case.category == "OTHER_PLATFORM"
    }


def test_an_unknown_case_or_category_is_an_error_not_an_empty_run():
    with pytest.raises(ValueError, match="no-such-case, NOT_A_CATEGORY"):
        select_cases(CASES, case_ids=["no-such-case"], categories=["NOT_A_CATEGORY"])


# --- evaluate ---------------------------------------------------------------------


def busy(question):
    raise ModelBusyError()


def test_well_behaved_answers_all_pass():
    run = evaluate(well_behaved, CASES[:3])

    assert run.report.failures == []
    assert run.skipped == [] and run.not_run == []


def test_an_unavailable_model_skips_the_case_instead_of_failing_it():
    answers = iter([ModelBusyError(), None])

    def flaky(question):
        outcome = next(answers)
        if outcome is not None:
            raise outcome
        return well_behaved(question)

    run = evaluate(flaky, CASES[:2])

    assert run.skipped == [CASES[0]]
    assert [r.case for r in run.results] == [CASES[1]]
    assert run.report.failures == []


def test_the_run_stops_after_two_unavailable_replies_in_a_row():
    run = evaluate(busy, CASES[:5])

    assert run.skipped == CASES[:2]
    assert run.not_run == CASES[2:5]
    assert run.results == []


def test_another_error_is_that_cases_failure_and_the_run_goes_on():
    def broken_then_fine(question):
        if question == CASES[0].question:
            raise RuntimeError("tool crashed")
        return well_behaved(question)

    run = evaluate(broken_then_fine, CASES[:2])

    assert [r.case for r in run.report.failures] == [CASES[0]]
    assert "tool crashed" in run.report.failures[0].error
    assert run.results[1].passed


def test_a_wrong_answer_is_judged_like_the_ci_runner():
    case = next(c for c in CASES if c.id == "other-platform-yelp")

    run = evaluate(lambda q: reply("OUT_OF_SCOPE", "No.", "GENERAL_KNOWLEDGE"), [case])

    assert run.report.failures[0].error == (
        "expected reason OTHER_PLATFORM, got GENERAL_KNOWLEDGE"
    )


def test_on_result_sees_each_case_with_its_response():
    seen = []
    answers = iter([ModelBusyError(), None])

    def flaky(question):
        outcome = next(answers)
        if outcome is not None:
            raise outcome
        return well_behaved(question)

    evaluate(
        flaky, CASES[:2], on_result=lambda c, resp, res: seen.append((c, resp, res))
    )

    assert seen[0] == (CASES[0], None, None)
    assert seen[1][0] is CASES[1] and seen[1][1] is not None and seen[1][2].passed


def test_pauses_between_cases_not_before_the_first():
    pauses = []

    evaluate(well_behaved, CASES[:3], pause=lambda: pauses.append(1))

    assert len(pauses) == 2


def test_scorecard_lists_skipped_cases_and_the_early_stop():
    text = scorecard(evaluate(busy, CASES[:3]))

    assert f"SKIPPED {CASES[0].id}: model unavailable" in text
    assert "1 case(s) not asked" in text


# --- CountingModel ----------------------------------------------------------------


class _Bound:
    def invoke(self, messages):
        return "reply"


class _Model:
    def __init__(self):
        self.bound_with = []

    def bind_tools(self, tools, *, tool_choice=None):
        self.bound_with.append((tools, tool_choice))
        return _Bound()


def test_counting_model_counts_every_request_and_passes_calls_through():
    inner = _Model()
    model = CountingModel(inner)

    first = model.bind_tools(["spec"], tool_choice="submit_answer")
    assert first.invoke([]) == "reply"
    model.bind_tools(["spec"]).invoke([])

    assert model.requests == 2
    assert inner.bound_with == [(["spec"], "submit_answer"), (["spec"], None)]


# --- main: the checks before anything is asked -------------------------------------


def test_list_prints_the_cases_and_asks_nothing(capsys):
    assert main(["--list"]) == 0
    assert "general-weather" in capsys.readouterr().out


def test_refuses_to_run_without_a_test_database(monkeypatch, capsys):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    assert main([]) == 2
    assert "TEST_DATABASE_URL" in capsys.readouterr().err


def test_refuses_to_run_against_supabase(monkeypatch, capsys):
    monkeypatch.setenv(
        "TEST_DATABASE_URL", "postgresql://u:p@db.x.supabase.co/postgres"
    )

    assert main([]) == 2
    assert "Supabase" in capsys.readouterr().err


def test_an_unknown_case_stops_before_anything_is_asked(capsys):
    assert main(["--case", "no-such-case"]) == 2
    assert "no-such-case" in capsys.readouterr().err
