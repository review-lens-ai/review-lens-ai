"""Give every saved review its embedding: its meaning as 384 numbers.

    embed_missing(reviews, embedder, commit, rollback, target_id=None) -> int

The one function that fills reviews.embedding. Two callers, same code:
- the ingestion worker, right after a run saves its reviews (one target);
- the backfill, for reviews saved before embeddings existed or whose step failed:

      ./scripts/embed-reviews.sh

It only reads reviews whose embedding is null and only writes to rows that are
still null, so running it again (or after a crash halfway) picks up where it
stopped and never redoes work. A batch that fails is rolled back, logged and
skipped; its reviews stay null, so the next run tries them again.

Vectors are normalized to length 1 here, in the one Embedder used for stored
reviews and, later, for search phrases, so the two always go through the same step.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.db import connect
from app.repositories.reviews import ReviewRepository

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384  # must match vector(384) in the migration
BATCH_SIZE = 64
# Where the model is downloaded to: backend/.cache/fastembed (git-ignored). A fixed
# folder, not the system temp folder, so a restart does not download it again.
MODEL_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "fastembed"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """One normalized vector of EMBEDDING_DIMENSIONS numbers per text."""
        ...


class EmbeddingStore(Protocol):
    """The ReviewRepository methods this module uses."""

    def without_embedding(
        self,
        *,
        limit: int,
        target_id: UUID | str | None = None,
        after: UUID | None = None,
    ) -> list[dict]: ...

    def save_embeddings(self, embeddings: list[tuple[UUID, list[float]]]) -> None: ...


class FastEmbedder:
    """BAAI/bge-small-en-v1.5 through fastembed: runs locally on the CPU, needs no
    API key and costs nothing. The model (about 50 MB) is downloaded to MODEL_CACHE
    on first use."""

    def __init__(self) -> None:
        self._model = None  # loaded on first use, so a failed load can't stop a caller

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from fastembed import TextEmbedding  # heavy import, only when really used

            self._model = TextEmbedding(EMBEDDING_MODEL, cache_dir=str(MODEL_CACHE))
        return [_normalize(vector.tolist()) for vector in self._model.embed(texts)]


def _normalize(vector: list[float]) -> list[float]:
    length = sum(value * value for value in vector) ** 0.5
    return [value / length for value in vector]


def embed_missing(
    reviews: EmbeddingStore,
    embedder: Embedder,
    commit: Callable[[], None],
    rollback: Callable[[], None],
    target_id: UUID | str | None = None,
) -> int:
    """Embed every review (of one target, or all) that has no embedding yet,
    committing after each batch. A failing batch is skipped, so one bad batch
    cannot stop the rest. Returns how many were embedded."""
    embedded, after = 0, None
    while batch := reviews.without_embedding(
        limit=BATCH_SIZE, target_id=target_id, after=after
    ):
        after = batch[-1]["id"]  # move past this batch even if it fails
        try:
            vectors = embedder.embed([row["review_text"] for row in batch])
            reviews.save_embeddings(
                [(row["id"], v) for row, v in zip(batch, vectors, strict=True)]
            )
            commit()
        except Exception as error:
            rollback()
            logger.warning(
                "Skipped %d reviews that could not be embedded: %s: %s",
                len(batch),
                type(error).__name__,
                error,
            )
            continue
        embedded += len(batch)
    return embedded


def main() -> None:
    """The backfill: embed every review in DATABASE_URL that has no embedding."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with connect() as conn:
        count = embed_missing(
            ReviewRepository(conn), FastEmbedder(), conn.commit, conn.rollback
        )
    logger.info("Embedded %d reviews.", count)


if __name__ == "__main__":
    main()
