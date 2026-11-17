"""S2-014, S2-015: run the scope-guard cases through the real pipeline and the real
model, by hand. Never part of CI or pytest: it calls a live model and its answers
vary between runs (S2-BR-029, S2-BR-031).

    cd backend
    $env:TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/postgres"
    uv run python -m tests.evals.live                      # one case per category
    uv run python -m tests.evals.live --category OTHER_PLATFORM
    uv run python -m tests.evals.live --case general-weather --case no-evidence-kids
    uv run python -m tests.evals.live --all                # every case

What it does:
1. Saves the Joe's Pizza reviews (joes_pizza_reviews.json) and the target's address
   (scope_guard_cases.json) for a throwaway user in the TEST database, the same
   throwaway Postgres the database tests use. It never touches Supabase, and
   everything it saved is rolled back at the end.
2. Asks each chosen case through answer_question, the code behind the Q&A panel:
   the system prompt, the tool loop and the real model from backend/.env
   (OPENROUTER_API_KEY, QA_MODEL, QA_MODE).
3. Judges each answer with scope_guard.judge and prints a scorecard.

Requests: every case costs one model request per tool call plus the final answer,
up to QA_MAX_TOOL_CALLS + 2. They come from the key of whoever runs it. On a free
model that is no money, but free keys have a daily request limit, so the default
runs one case per category, not the whole set. When the model is unavailable (rate
limited, busy, failing), the case is reported as SKIPPED, not FAILED, since that
says nothing about the prompt. After STOP_AFTER_UNAVAILABLE in a row the run stops.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from app.schemas.qa import AnswerResponse
from tests.evals.scope_guard import (
    CaseResult,
    EvalReport,
    ScopeGuardCase,
    judge,
    load_cases,
    load_reviews,
    load_target,
)

STOP_AFTER_UNAVAILABLE = 2
PAUSE_SECONDS = 3.0  # between cases, to stay under a free model's per-minute limit
ANSWER_PREVIEW_CHARACTERS = 200
STUB_SQL = Path(__file__).parents[1] / "db" / "supabase_stub.sql"
# A Google Maps link the targets table accepts; the place itself is never fetched.
PLACE_URL = "https://www.google.com/maps/place/Joes+Pizza/data=!4m2!3m1!1s0x1:0x14"


class ModelBusyError(Exception):
    """The model could not be reached for one case (QAUnavailableError)."""


# --- Choosing cases ------------------------------------------------------------------


def select_cases(
    cases: Sequence[ScopeGuardCase],
    *,
    case_ids: Sequence[str] = (),
    categories: Sequence[str] = (),
    run_all: bool = False,
) -> list[ScopeGuardCase]:
    """The cases to ask, in the set's order. --all: every case. --case/--category:
    those. Neither: the first case of each category, so a run stays cheap."""
    known_ids = {case.id for case in cases}
    known_categories = {case.category for case in cases}
    unknown = [i for i in case_ids if i not in known_ids] + [
        c for c in categories if c not in known_categories
    ]
    if unknown:
        raise ValueError(f"Unknown case or category: {', '.join(unknown)}")
    if run_all:
        return list(cases)
    if case_ids or categories:
        return [c for c in cases if c.id in case_ids or c.category in categories]
    first_of_each: dict[str, ScopeGuardCase] = {}
    for case in cases:
        first_of_each.setdefault(case.category, case)
    return list(first_of_each.values())


# --- Running cases -------------------------------------------------------------------


@dataclass
class LiveRun:
    """What one run did: the judged cases, the cases skipped because the model was
    unavailable, and the cases never asked because the run stopped early."""

    results: list[CaseResult] = field(default_factory=list)
    skipped: list[ScopeGuardCase] = field(default_factory=list)
    not_run: list[ScopeGuardCase] = field(default_factory=list)

    @property
    def report(self) -> EvalReport:
        return EvalReport(self.results)


def evaluate(
    ask: Callable[[str], AnswerResponse],
    cases: Sequence[ScopeGuardCase],
    *,
    on_result: Callable[
        [ScopeGuardCase, AnswerResponse | None, CaseResult | None], None
    ] = lambda *_: None,
    pause: Callable[[], None] = lambda: None,
    stop_after_unavailable: int = STOP_AFTER_UNAVAILABLE,
) -> LiveRun:
    """Asks each case and judges it. ModelBusyError skips the case; that many in
    a row stops the run. Any other exception is the case's failure, as in
    scope_guard.run."""
    run = LiveRun()
    unavailable_in_a_row = 0
    for index, case in enumerate(cases):
        if index:
            pause()
        try:
            response = ask(case.question)
        except ModelBusyError:
            run.skipped.append(case)
            on_result(case, None, None)
            unavailable_in_a_row += 1
            if unavailable_in_a_row >= stop_after_unavailable:
                run.not_run = list(cases[index + 1 :])
                break
            continue
        except Exception as error:  # this case fails; the rest still run
            response = None
            result = CaseResult(case, None, error=f"raised {error!r}")
        else:
            result = judge(case, response)
        unavailable_in_a_row = 0
        run.results.append(result)
        on_result(case, response, result)
    return run


def scorecard(run: LiveRun) -> str:
    lines = [run.report.summary()]
    for case in run.skipped:
        lines.append(f"SKIPPED {case.id}: model unavailable ({case.question})")
    if run.not_run:
        lines.append(
            f"Stopped after {STOP_AFTER_UNAVAILABLE} unavailable replies in a row; "
            f"{len(run.not_run)} case(s) not asked. Try again later or another model."
        )
    return "\n".join(lines)


# --- The real pipeline ---------------------------------------------------------------


class CountingModel:
    """Wraps the real ChatModel and counts requests, so a run shows what it used."""

    def __init__(self, model):
        self._model = model
        self.requests = 0

    def bind_tools(self, tools, *, tool_choice=None):
        bound = self._model.bind_tools(tools, tool_choice=tool_choice)
        counter = self

        class _Counted:
            def invoke(self, messages):
                counter.requests += 1
                return bound.invoke(messages)

        return _Counted()


def seed_joes_pizza(conn) -> tuple[str, str]:
    """A throwaway user who owns "Joe's Pizza", with its address and every fixture
    review saved by one finished run. Returns (owner_id, target_id). The caller
    rolls it back."""
    from app.repositories.ingestion_runs import IngestionRunRepository
    from app.repositories.reviews import ReviewRepository
    from app.repositories.targets import TargetRepository

    owner_id = conn.execute(
        "insert into auth.users (email) values (%s) returning id",
        (f"live-eval-{os.getpid()}@example.com",),
    ).fetchone()["id"]
    target = load_target()
    targets = TargetRepository(conn)
    target_id = targets.create(owner_id, target["name"], target["platform"], PLACE_URL)[
        "id"
    ]
    # Like the worker after a real ingestion: the place's address, which the prompt
    # uses to tell this location from other branches (OTHER_LOCATION).
    targets.save_place_details(target_id, owner_id, {"address": target["address"]})
    reviews = load_reviews()
    runs = IngestionRunRepository(conn)
    run = runs.mark_running(runs.create_run(target_id, owner_id)["id"])
    ReviewRepository(conn).bulk_insert(run["id"], target_id, owner_id, reviews)
    runs.mark_succeeded(run["id"], reviews_ingested=len(reviews))
    return owner_id, target_id


def _migrate(conn) -> None:
    """The test database's schema: the Supabase stub (auth.users) once, then any
    migrations not applied yet. The same steps as tests/db/conftest.py."""
    from app.db.migrate import apply_migrations

    if conn.execute("select to_regclass('auth.users') as t").fetchone()["t"] is None:
        conn.execute(STUB_SQL.read_text(encoding="utf-8"))
        conn.commit()
    apply_migrations(conn)


def _print_case(case, response, result) -> None:
    if result is None:
        print(f"[SKIP] {case.id}: model unavailable", flush=True)
        return
    mark = "PASS" if result.passed else "FAIL"
    got = result.actual or "no response"
    reason = (
        f" / {response.refusal_reason}" if response and response.refusal_reason else ""
    )
    print(f"[{mark}] {case.id}: {got}{reason}", flush=True)
    print(f"       Q: {case.question}")
    if response is not None:
        answer = " ".join(response.answer.split())
        if len(answer) > ANSWER_PREVIEW_CHARACTERS:
            answer = answer[: ANSWER_PREVIEW_CHARACTERS - 3] + "..."
        print(f"       A: {answer}")
        if response.steps:
            print(f"       tools: {', '.join(step.tool for step in response.steps)}")
        if response.suggestions:
            print(f"       suggestions: {' | '.join(response.suggestions)}")
    if result.error:
        print(f"       problem: {result.error}")
    elif not result.passed:
        print(f"       problem: expected {case.expected}")


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tests.evals.live",
        description="Run scope-guard cases through the real pipeline and model.",
    )
    parser.add_argument("--case", action="append", default=[], help="a case id")
    parser.add_argument(
        "--category", action="append", default=[], help="e.g. OTHER_PLATFORM"
    )
    parser.add_argument("--all", action="store_true", help="every case in the set")
    parser.add_argument(
        "--list", action="store_true", help="list the cases and exit, asking nothing"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    cases = load_cases()
    if args.list:
        for case in cases:
            print(f"{case.category:<22} {case.id:<34} {case.question}")
        return 0
    try:
        chosen = select_cases(
            cases, case_ids=args.case, categories=args.category, run_all=args.all
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        print(
            "Set TEST_DATABASE_URL to the throwaway test Postgres (see backend/tests/"
            "evals/README.md). The live eval never uses Supabase.",
            file=sys.stderr,
        )
        return 2
    if "supabase.co" in database_url:
        print(
            "TEST_DATABASE_URL points at Supabase. Use the test Postgres.",
            file=sys.stderr,
        )
        return 2

    from app.core.config import get_settings
    from app.db import connect
    from app.llm.openrouter import make_chat_model
    from app.repositories.review_search import ReviewSearchRepository
    from app.repositories.reviews import ReviewRepository
    from app.repositories.targets import TargetRepository
    from app.services.qa import QAConfig, QAUnavailableError, answer_question

    settings = get_settings()
    if not settings.qa_model or not settings.openrouter_api_key:
        print("Set OPENROUTER_API_KEY and QA_MODEL in backend/.env.", file=sys.stderr)
        return 2
    model = CountingModel(make_chat_model(settings))
    config = QAConfig.from_settings(settings)
    print(
        f"Model {settings.qa_model}, mode {config.mode}, up to "
        f"{config.max_tool_calls} tool calls per question. Asking {len(chosen)} of "
        f"{len(cases)} cases (about {len(chosen) * 2}-"
        f"{len(chosen) * (config.max_tool_calls + 2)} requests).\n",
        flush=True,
    )

    with connect(database_url) as conn:
        _migrate(conn)
        try:
            owner_id, target_id = seed_joes_pizza(conn)

            def ask(question: str) -> AnswerResponse:
                try:
                    return answer_question(
                        owner_id=owner_id,
                        target_id=target_id,
                        question=question,
                        targets=TargetRepository(conn),
                        reviews=ReviewRepository(conn),
                        open_search=partial(ReviewSearchRepository.for_owner, conn),
                        make_model=lambda: model,
                        config=config,
                    )
                except QAUnavailableError as error:
                    raise ModelBusyError() from error

            run = evaluate(
                ask,
                chosen,
                on_result=_print_case,
                pause=lambda: time.sleep(PAUSE_SECONDS),
            )
        finally:
            conn.rollback()  # the throwaway user, target and reviews are not kept

    print("\n" + scorecard(run))
    print(f"Model requests used: {model.requests}")
    return 0 if not run.report.failures and not run.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
