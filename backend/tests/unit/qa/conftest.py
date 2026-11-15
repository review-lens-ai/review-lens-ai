"""S2-003: the deterministic Q&A harness, without a database or a live model.

FakeModel stands in for the provider behind ModelClient. It records every
ModelRequest, so tests check exactly what ReviewLens would send, and returns a
scripted ModelReply (or raises a scripted error), so tests check what ReviewLens
does with each kind of reply. No test here calls a real model.

The targets and reviews are small in-memory fakes with the same ownership rule
as the real repositories: another user's target is NotFoundError. The same
checks against a real Postgres are in tests/db/test_qa_context_isolation.py.

Data: user A owns TARGET_A (one review holds the distinctive phrase
LEAK_PHRASE), TARGET_A2 and TARGET_EMPTY; user B owns TARGET_B.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.core.errors import NotFoundError
from app.llm import ModelReply, ReplyStatus
from app.services.qa import answer_question

OWNER_A = "00000000-0000-0000-0000-00000000000a"
OWNER_B = "00000000-0000-0000-0000-00000000000b"

TARGET_A = "10000000-0000-0000-0000-00000000000a"
TARGET_A2 = "20000000-0000-0000-0000-00000000000a"
TARGET_EMPTY = "30000000-0000-0000-0000-00000000000a"
TARGET_B = "10000000-0000-0000-0000-00000000000b"

LEAK_PHRASE = "ZEBRA-PASTA-7"

TARGETS = {
    TARGET_A: {
        "owner_id": OWNER_A,
        "name": "Luigi's Trattoria",
        "address": "12 Mulberry St, New York, NY 10013",
    },
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


def review_ids(target_id: str) -> list[str]:
    """The target's review ids, newest first (the order the selector returns)."""
    rows = sorted(
        (r for r in REVIEWS if r[1] == target_id), key=lambda r: r[4], reverse=True
    )
    return [r[0] for r in rows]


class FakeTargets:
    def __init__(self, rows):
        self.rows = rows

    def get_owned(self, target_id, owner_id):
        row = self.rows.get(str(target_id))
        if row is None or row["owner_id"] != str(owner_id):
            raise NotFoundError("Target not found.")
        return {
            "id": str(target_id),
            "name": row["name"],
            "platform": "google_maps",
        }

    def place_address(self, target_id, owner_id):
        self.get_owned(target_id, owner_id)
        return self.rows[str(target_id)].get("address")


class FakeReviews:
    def __init__(self, targets, rows):
        self.targets = targets
        self.rows = rows

    def _for(self, target_id, owner_id):
        self.targets.get_owned(target_id, owner_id)
        return [r for r in self.rows if r["target_id"] == str(target_id)]

    def list_for_target(self, target_id, owner_id, *, limit=None, offset=0):
        rows = sorted(
            self._for(target_id, owner_id),
            key=lambda r: r["reviewed_at"],
            reverse=True,
        )
        return rows[offset:] if limit is None else rows[offset : offset + limit]

    def count_for_target(self, target_id, owner_id):
        return len(self._for(target_id, owner_id))

    def summary_for_target(self, target_id, owner_id):
        """The same shape as ReviewRepository.summary_for_target."""
        rows = self._for(target_id, owner_id)
        ratings = [r["rating"] for r in rows]
        dates = [r["reviewed_at"] for r in rows if r["reviewed_at"]]
        return {
            "review_count": len(rows),
            "average_rating": round(sum(ratings) / len(ratings), 2) if rows else None,
            "rating_counts": [
                {"rating": n, "count": ratings.count(n)} for n in range(1, 6)
            ],
            "first_reviewed_at": min(dates) if dates else None,
            "last_reviewed_at": max(dates) if dates else None,
        }


class FakeModel:
    """Records each ModelRequest; returns `reply` or raises `error`."""

    def __init__(self, reply: ModelReply | None = None, error=None):
        self.reply = reply or ModelReply(ReplyStatus.OUT_OF_SCOPE, "Scripted reply.")
        self.error = error
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.reply

    @property
    def last(self):
        return self.requests[-1]


def answered(*ids: str, text: str = "Diners praise the food.") -> ModelReply:
    return ModelReply(ReplyStatus.ANSWERED, text, tuple(ids))


@dataclass
class Store:
    targets: FakeTargets
    reviews: FakeReviews


@pytest.fixture
def store():
    targets = FakeTargets(TARGETS)
    rows = [
        {
            "id": review_id,
            "target_id": target_id,
            "rating": rating,
            "review_text": text,
            "reviewed_at": datetime(2026, 10, day, tzinfo=UTC),
        }
        for review_id, target_id, rating, text, day in REVIEWS
    ]
    return Store(targets, FakeReviews(targets, rows))


@pytest.fixture
def ask(store):
    """ask(model, owner, target, question) runs the real orchestrator on the fakes."""

    def _ask(
        model,
        owner_id=OWNER_A,
        target_id=TARGET_A,
        question="What do people say about the food?",
        **kwargs,
    ):
        return answer_question(
            owner_id=owner_id,
            target_id=target_id,
            question=question,
            targets=store.targets,
            reviews=store.reviews,
            model=model,
            **kwargs,
        )

    return _ask
