"""Review embeddings against real Postgres with pgvector: embed_missing (the code
behind both the worker's EMBED step and ./scripts/embed-reviews.sh) and the two
ReviewRepository methods it uses. FakeEmbedder stands in for the model.

Skipped without TEST_DATABASE_URL.
"""

import pytest

from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services import embed_reviews
from app.services.embed_reviews import EMBEDDING_DIMENSIONS, _normalize, embed_missing
from tests.conftest import FakeEmbedder

URL = "https://www.google.com/maps/place/Joes+Pizza"


def _target_with_reviews(conn, owner_id, texts):
    target = TargetRepository(conn).create(owner_id, "Joe's", "google_maps", URL)
    run_id = conn.execute(
        """
        insert into public.ingestion_runs (target_id, status, started_at)
        values (%s, 'RUNNING', now())
        returning id
        """,
        (target["id"],),
    ).fetchone()["id"]
    ReviewRepository(conn).bulk_insert(
        run_id,
        target["id"],
        owner_id,
        [
            ReviewCreate(rating=4, review_text=t, mapping_version="serpapi@1")
            for t in texts
        ],
    )
    return target["id"]


def _missing(conn, target_id):
    return conn.execute(
        "select count(*) as n from public.reviews where target_id = %s and embedding is null",
        (target_id,),
    ).fetchone()["n"]


def test_every_review_gets_one_embedding_and_a_second_run_does_nothing(conn, make_user):
    target_id = _target_with_reviews(conn, make_user(), ["Slow service", "Great pizza"])
    repo = ReviewRepository(conn)

    first = embed_missing(repo, FakeEmbedder(), commit=lambda: None)
    again = embed_missing(repo, FakeEmbedder(), commit=lambda: None)

    assert (first, again) == (2, 0)
    assert _missing(conn, target_id) == 0


def test_a_target_id_embeds_only_that_target(conn, make_user):
    mine = _target_with_reviews(conn, make_user(), ["Slow service"])
    other = _target_with_reviews(conn, make_user(), ["Cold fries"])

    embed_missing(ReviewRepository(conn), FakeEmbedder(), lambda: None, target_id=mine)

    assert (_missing(conn, mine), _missing(conn, other)) == (0, 1)


def test_it_commits_after_each_batch(conn, make_user, monkeypatch):
    monkeypatch.setattr(embed_reviews, "BATCH_SIZE", 2)
    target_id = _target_with_reviews(
        conn, make_user(), ["a", "bb", "ccc", "dddd", "e5"]
    )
    commits = []

    count = embed_missing(
        ReviewRepository(conn), FakeEmbedder(), lambda: commits.append(1), target_id
    )

    assert count == 5
    assert len(commits) == 3  # batches of 2, 2 and 1: a crash keeps what was done


def test_saving_never_overwrites_an_existing_embedding(conn, make_user):
    target_id = _target_with_reviews(conn, make_user(), ["Slow service"])
    repo = ReviewRepository(conn)
    embed_missing(repo, FakeEmbedder(), lambda: None)
    review_id = conn.execute(
        "select id from public.reviews where target_id = %s", (target_id,)
    ).fetchone()["id"]
    before = conn.execute(
        "select embedding::text as e from public.reviews where id = %s", (review_id,)
    ).fetchone()["e"]

    repo.save_embeddings([(review_id, [0.0] * (EMBEDDING_DIMENSIONS - 1) + [1.0])])

    after = conn.execute(
        "select embedding::text as e from public.reviews where id = %s", (review_id,)
    ).fetchone()["e"]
    assert after == before


def test_stored_embeddings_have_384_numbers_and_length_one(conn, make_user):
    target_id = _target_with_reviews(conn, make_user(), ["Slow service"])
    embed_missing(ReviewRepository(conn), FakeEmbedder(), lambda: None)

    row = conn.execute(
        "select vector_dims(embedding) as dims, vector_norm(embedding) as norm "
        "from public.reviews where target_id = %s",
        (target_id,),
    ).fetchone()

    assert row["dims"] == EMBEDDING_DIMENSIONS
    assert row["norm"] == pytest.approx(1.0)


def test_normalize_scales_a_vector_to_length_one():
    assert _normalize([3.0, 4.0]) == pytest.approx([0.6, 0.8])
