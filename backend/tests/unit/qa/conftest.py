"""S2-010: the deterministic Q&A harness, without a database or a live model.

FakeChatModel stands in for the provider behind ChatModel (app/llm/client.py). It
records every call (the messages, the tools offered, tool_choice), so tests check
exactly what ReviewLens would send, and returns scripted AIMessage turns (or raises
a scripted error), so tests check what ReviewLens does with each reply. No test here
calls a real model.

The tool set is built from the real ToolRegistry and LabelBook (app/services/qa/
tools/) with two small in-memory tools, find_reviews and count_reviews, over the
reviews below. The targets, reviews and search fakes keep the real ownership rule:
another user's target is NotFoundError. The same checks with the real tools and SQL
are in tests/db/test_qa_context_isolation.py.

Data: user A owns TARGET_A (one review holds LEAK_PHRASE), TARGET_A2 and
TARGET_EMPTY; user B owns TARGET_B.
"""

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict

from app.core.errors import NotFoundError
from app.services.qa import QAConfig, answer_question
from app.services.qa.tools.guard import quote, require_in_scope
from app.services.qa.tools.labels import LabelBook
from app.services.qa.tools.registry import Tool, ToolRegistry
from app.services.qa.tools.types import ToolResult

OWNER_A = "00000000-0000-0000-0000-00000000000a"
OWNER_B = "00000000-0000-0000-0000-00000000000b"

TARGET_A = UUID("10000000-0000-0000-0000-00000000000a")
TARGET_A2 = UUID("20000000-0000-0000-0000-00000000000a")
TARGET_EMPTY = UUID("30000000-0000-0000-0000-00000000000a")
TARGET_B = UUID("10000000-0000-0000-0000-00000000000b")

LEAK_PHRASE = "ZEBRA-PASTA-7"

TARGETS = {
    TARGET_A: {"owner_id": OWNER_A, "name": "Luigi's Trattoria"},
    TARGET_A2: {"owner_id": OWNER_A, "name": "Harbor Coffee"},
    TARGET_EMPTY: {"owner_id": OWNER_A, "name": "Not Ingested Yet"},
    TARGET_B: {"owner_id": OWNER_B, "name": "Bella's Diner"},
}

# (review id, target, rating, text, day of October 2026)
REVIEWS = [
    ("a0000000-0000-0000-0000-000000000001", TARGET_A, 5,
     f"The {LEAK_PHRASE} special was the best dish I've had.", 20),
    ("a0000000-0000-0000-0000-000000000002", TARGET_A, 2,
     "Service was slow and the waiter forgot our drinks.", 18),
    ("a0000000-0000-0000-0000-000000000003", TARGET_A, 4,
     "Great food, a bit loud on weekends.", 15),
    ("a2000000-0000-0000-0000-000000000001", TARGET_A2, 5,
     "Best espresso on the waterfront.", 19),
    ("a2000000-0000-0000-0000-000000000002", TARGET_A2, 3,
     "Coffee is fine but the pastries were stale.", 11),
    ("b0000000-0000-0000-0000-000000000001", TARGET_B, 4,
     "Huge portions and friendly staff.", 17),
    ("b0000000-0000-0000-0000-000000000002", TARGET_B, 1,
     "Waited forty minutes for pancakes.", 12),
]  # fmt: skip

ROWS = [
    {
        "id": UUID(review_id),
        "target_id": target_id,
        "rating": rating,
        "review_text": text,
        "reviewed_at": datetime(2026, 10, day, tzinfo=UTC),
    }
    for review_id, target_id, rating, text, day in REVIEWS
]


def rows_of(target_id: UUID) -> list[dict]:
    """The target's reviews, newest first."""
    rows = [r for r in ROWS if r["target_id"] == target_id]
    return sorted(rows, key=lambda r: r["reviewed_at"], reverse=True)


# --- Repositories --------------------------------------------------------------------


class FakeTargets:
    def get_owned(self, target_id, owner_id):
        row = TARGETS.get(UUID(str(target_id)))
        if row is None or row["owner_id"] != str(owner_id):
            raise NotFoundError("Target not found.")
        return {
            "id": UUID(str(target_id)),
            "name": row["name"],
            "platform": "google_maps",
        }


class FakeReviews:
    """The ReviewRepository methods Q&A uses."""

    def __init__(self, targets: FakeTargets):
        self.targets = targets

    def list_for_target(self, target_id, owner_id):
        self.targets.get_owned(target_id, owner_id)
        return rows_of(UUID(str(target_id)))

    def summary_for_target(self, target_id, owner_id):
        rows = self.list_for_target(target_id, owner_id)
        counts = {n: sum(r["rating"] == n for r in rows) for n in range(1, 6)}
        dates = [r["reviewed_at"] for r in rows]
        average = round(sum(r["rating"] for r in rows) / len(rows), 2) if rows else None
        return {
            "review_count": len(rows),
            "average_rating": average,
            "rating_counts": [{"rating": n, "count": counts[n]} for n in range(1, 6)],
            "first_reviewed_at": min(dates) if dates else None,
            "last_reviewed_at": max(dates) if dates else None,
        }


class FakeSearch:
    """The ReviewSearchRepository method the answer builder uses."""

    def __init__(self, targets: FakeTargets):
        self.targets = targets

    def get_many(self, target_id, owner_id, review_ids):
        self.targets.get_owned(target_id, owner_id)
        wanted = set(review_ids)
        return [r for r in rows_of(UUID(str(target_id))) if r["id"] in wanted]


# --- Tools ---------------------------------------------------------------------------


class WordsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    words: str | None = None


@dataclass
class FakeToolSet:
    """Real ToolRegistry and LabelBook, with two in-memory tools."""

    registry: ToolRegistry
    labels: LabelBook
    runs: list


def fake_toolset(search, owner_id, target_id, *, leak_from: UUID | None = None):
    """A tool set for one question. leak_from adds another target's reviews to
    find_reviews, like a query that forgot its target filter."""
    labels, runs = LabelBook(), []

    def matching(words):
        rows = rows_of(target_id) + (rows_of(leak_from) if leak_from else [])
        if words:
            rows = [r for r in rows if words.lower() in r["review_text"].lower()]
        return rows

    def find_reviews(args: WordsArgs) -> ToolResult:
        runs.append(("find_reviews", args.words))
        rows = matching(args.words)
        require_in_scope(rows, target_id)
        if not rows:
            return ToolResult(text="No reviews match.")
        lines, sources = [], []
        for row in rows:
            source = labels.source_for(row["id"], f"{row['rating']} stars")
            lines.append(f"{source.label} | {quote(row['review_text'], 300)}")
            sources.append(source)
        return ToolResult(text="\n".join(lines), sources=sources)

    def count_reviews(args: WordsArgs) -> ToolResult:
        runs.append(("count_reviews", args.words))
        found, total = len(matching(args.words)), len(rows_of(target_id))
        return ToolResult(text=f"{found} of {total} reviews")

    registry = ToolRegistry(
        [
            Tool("find_reviews", "Find reviews.", WordsArgs, find_reviews),
            Tool("count_reviews", "Count reviews.", WordsArgs, count_reviews),
        ]
    )
    return FakeToolSet(registry=registry, labels=labels, runs=runs)


# --- Model ---------------------------------------------------------------------------

_ids = itertools.count(1)


def call(name: str, **args) -> AIMessage:
    """A model turn that calls one tool."""
    return AIMessage(
        "", tool_calls=[{"name": name, "args": args, "id": f"c{next(_ids)}"}]
    )


def submit(status: str, answer: str = "Diners praise the food.", cited=(), **extra):
    """A model turn that finishes the question."""
    return call(
        "submit_answer", status=status, answer=answer, cited=list(cited), **extra
    )


class FakeChatModel:
    """Records each call; returns the next scripted turn, or raises it."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls: list[dict] = []
        self._bound: dict = {}

    def bind_tools(self, tools, *, tool_choice=None):
        self._bound = {"tools": [t["name"] for t in tools], "tool_choice": tool_choice}
        return self

    def invoke(self, messages):
        self.calls.append({**self._bound, "messages": list(messages)})
        if not self.turns:
            raise AssertionError("FakeChatModel ran out of scripted turns")
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        return turn

    @property
    def last(self) -> dict:
        return self.calls[-1]

    def sent_text(self) -> str:
        """Everything sent to the model, in every call, as one string."""
        return "\n".join(
            str(m.content) for c in self.calls for m in c["messages"] if m.content
        )


# --- Running a question --------------------------------------------------------------


@pytest.fixture
def repos():
    targets = FakeTargets()
    return targets, FakeReviews(targets), FakeSearch(targets)


@pytest.fixture
def ask(repos):
    """ask(model, owner, target, question) runs the real orchestrator on the fakes."""
    targets, reviews, search = repos

    def _ask(
        model,
        owner_id=OWNER_A,
        target_id=TARGET_A,
        question="What do people say about the food?",
        *,
        config=None,
        make_toolset=fake_toolset,
    ):
        return answer_question(
            owner_id=owner_id,
            target_id=target_id,
            question=question,
            targets=targets,
            reviews=reviews,
            search=search,
            make_model=lambda: model,
            config=config or QAConfig(),
            make_toolset=make_toolset,
        )

    return _ask
