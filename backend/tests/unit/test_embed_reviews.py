"""The real embedder and the backfill entry point, without the model or a database.

A fake fastembed module stands in for the library, so nothing is downloaded; the
database side of embed_missing is in tests/db/test_review_embeddings.py.
"""

import logging
import sys
import types
from contextlib import contextmanager
from uuid import uuid4

from app.services import embed_reviews
from app.services.embed_reviews import EMBEDDING_MODEL, MODEL_CACHE, FastEmbedder
from tests.conftest import FakeEmbedder


class _Vector(list):
    """Like the numpy array fastembed returns: it has tolist()."""

    def tolist(self):
        return list(self)


def _fake_fastembed(monkeypatch) -> list:
    """Install a fake fastembed; returns the list of models it was asked to load."""
    loads = []

    class TextEmbedding:
        def __init__(self, model_name, cache_dir):
            loads.append((model_name, cache_dir))

        def embed(self, texts):
            return (_Vector([3.0, 4.0]) for _ in texts)

    module = types.SimpleNamespace(TextEmbedding=TextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return loads


def test_the_model_loads_once_on_first_use_and_vectors_are_normalized(monkeypatch):
    loads = _fake_fastembed(monkeypatch)
    embedder = FastEmbedder()
    assert loads == []  # nothing loads until it is used

    first = embedder.embed(["great fries", "slow"])
    embedder.embed(["again"])

    assert loads == [(EMBEDDING_MODEL, str(MODEL_CACHE))]
    assert first == [[0.6, 0.8], [0.6, 0.8]]  # [3, 4] scaled to length 1


class _Store:
    """One batch of two reviews without an embedding, then none."""

    def __init__(self):
        self.rows = [
            {"id": uuid4(), "review_text": "Great fries"},
            {"id": uuid4(), "review_text": "Slow service"},
        ]
        self.saved = []

    def without_embedding(self, *, limit, target_id=None, after=None):
        return [] if after else self.rows

    def save_embeddings(self, embeddings):
        self.saved.extend(embeddings)


class _Conn:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        raise AssertionError("nothing should fail")


def test_the_backfill_embeds_every_review_and_commits(monkeypatch, caplog):
    store, conn = _Store(), _Conn()

    @contextmanager
    def connect():
        yield conn

    monkeypatch.setattr(embed_reviews, "connect", connect)
    monkeypatch.setattr(embed_reviews, "ReviewRepository", lambda _conn: store)
    monkeypatch.setattr(embed_reviews, "FastEmbedder", FakeEmbedder)
    caplog.set_level(logging.INFO, logger=embed_reviews.__name__)

    embed_reviews.main()

    assert [review_id for review_id, _ in store.saved] == [r["id"] for r in store.rows]
    assert conn.commits == 1
    assert "Embedded 2 reviews." in caplog.text
