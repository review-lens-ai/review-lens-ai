"""S3-006: persist a target's Q&A history (table: questions).

    questions.add(target_id, owner_id, question, response)   after an answer
    questions.list_for_target(target_id, owner_id, limit=, offset=)   oldest first
    questions.count_for_target(target_id, owner_id)         how many are saved
    questions.clear_for_target(target_id, owner_id)         Q&A only

Rules this module enforces:
- OWNERSHIP: FastAPI bypasses Row Level Security, so every method loads the target
  with TargetRepository.get_owned first. Another user's target is NotFoundError,
  exactly like a missing one, so IDs cannot be probed.
- EVIDENCE BY REFERENCE: a saved response keeps only the cited review ids. The
  reviews are read back from public.reviews, from this target only, when the
  history is restored, so review text is not copied into every answer. A cited
  review that no longer exists is left out.
- ORDER (S3-BR-010): oldest first, by created_at then position, so questions saved
  in the same instant still come back in the order they were saved.
- PAGING counts back from the newest question, like a chat: offset 0 is the latest
  `limit` questions, offset 100 the 100 before those. Each page is still oldest first.
- CLEARING touches only public.questions: never the target, its runs or reviews.
"""

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.repositories.targets import TargetRepository
from app.schemas.qa import AnswerResponse


class QuestionRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def _owned_target_id(self, target_id: UUID | str, owner_id: UUID | str):
        return TargetRepository(self._conn).get_owned(target_id, owner_id)["id"]

    def add(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        question: str,
        response: AnswerResponse,
    ) -> dict:
        """Save one question and the response the user saw. NotFoundError unless
        `owner_id` owns the target."""
        owned_id = self._owned_target_id(target_id, owner_id)
        stored = response.model_dump(mode="json")
        stored["evidence"] = [{"id": item["id"]} for item in stored["evidence"]]
        return self._conn.execute(
            """
            insert into public.questions (target_id, question, response)
            values (%s, %s, %s)
            returning id, question, created_at
            """,
            (owned_id, question, Jsonb(stored)),
        ).fetchone()

    def list_for_target(
        self,
        target_id: UUID | str,
        owner_id: UUID | str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """The target's questions, oldest first, each with its response and the
        cited reviews read back from this target. With `limit`, only the newest
        `limit` questions after skipping the newest `offset` (limit=None returns
        them all). NotFoundError unless `owner_id` owns the target."""
        owned_id = self._owned_target_id(target_id, owner_id)
        rows = self._conn.execute(
            """
            select id, question, created_at, response
              from (select id, question, created_at, response, position
                      from public.questions
                     where target_id = %s
                     order by created_at desc, position desc
                     limit %s offset %s) as page
             order by created_at, position
            """,
            (owned_id, limit, offset),
        ).fetchall()
        reviews = self._cited_reviews(owned_id, rows)
        for row in rows:
            row["response"]["evidence"] = [
                reviews[item["id"]]
                for item in row["response"]["evidence"]
                if item["id"] in reviews
            ]
        return rows

    def _cited_reviews(self, target_id: UUID, rows: list[dict]) -> dict[str, dict]:
        """The reviews the rows cite, by id, from this target only (one query)."""
        ids = {item["id"] for row in rows for item in row["response"]["evidence"]}
        if not ids:
            return {}
        found = self._conn.execute(
            """
            select id, rating, review_text, reviewed_at
              from public.reviews
             where target_id = %s and id = any(%s::uuid[])
            """,
            (target_id, list(ids)),
        ).fetchall()
        return {str(review["id"]): review for review in found}

    def count_for_target(self, target_id: UUID | str, owner_id: UUID | str) -> int:
        """How many questions the target has saved. NotFoundError unless `owner_id`
        owns the target."""
        owned_id = self._owned_target_id(target_id, owner_id)
        return self._conn.execute(
            "select count(*) as total from public.questions where target_id = %s",
            (owned_id,),
        ).fetchone()["total"]

    def clear_for_target(self, target_id: UUID | str, owner_id: UUID | str) -> int:
        """Delete the target's Q&A history only; returns how many were removed.
        NotFoundError unless `owner_id` owns the target."""
        owned_id = self._owned_target_id(target_id, owner_id)
        return self._conn.execute(
            "delete from public.questions where target_id = %s", (owned_id,)
        ).rowcount
