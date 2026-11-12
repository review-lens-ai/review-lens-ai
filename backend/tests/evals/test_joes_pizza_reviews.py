"""S2-004: the fake reviews behind the evaluation set say what the cases need.

In-scope cases need evidence in the reviews. Insufficient-evidence and
other-entity/platform cases need the reviews to stay silent on their topic:
one review mentioning parking would turn "Is the parking lot safe at night?"
into a question the model may rightly answer.
"""

import pytest

from app.schemas.reviews import ReviewCreate
from tests.evals.scope_guard import load_cases, load_reviews

# Words that must never appear, and the case each one protects.
MUST_NOT_MENTION = {
    "parking": "no-evidence-parking-safety",
    "gluten": "no-evidence-gluten-free",
    "kid": "no-evidence-kids",
    "child": "no-evidence-kids",
    "famil": "no-evidence-kids",
    "price": "no-evidence-price",
    "cost": "no-evidence-price",
    "cheap": "no-evidence-price",
    "expensive": "no-evidence-price",
    "$": "no-evidence-price",
    "starbucks": "other-entity-starbucks",
    "domino": "other-entity-dominos",
    "mia's": "other-entity-mias-tacos",
    "taco": "other-entity-mias-tacos",
    "hoboken": "other-entity-other-location",
    "street": "other-entity-unnamed-competitor",
    "competit": "other-entity-unnamed-competitor",
    "yelp": "other-platform-yelp",
    "uber": "other-platform-uber-eats",
    "tripadvisor": "other-platform-tripadvisor-rating",
}

# Topics in-scope cases ask about: some review must mention one of the words.
MUST_MENTION = {
    "in-scope-service": ("service", "staff", "server"),
    "in-scope-pizza-likes": ("pizza", "slice", "crust"),
    "in-scope-wait-times": ("wait",),
    # The comparison case needs both sides in the reviews: service and food.
    "in-scope-service-vs-food (service side)": ("service", "staff", "server"),
    "in-scope-service-vs-food (food side)": ("pizza", "slice", "crust"),
}


@pytest.fixture(scope="module")
def reviews() -> list[ReviewCreate]:
    return load_reviews()


def test_reviews_are_valid_review_rows(reviews: list[ReviewCreate]) -> None:
    # load_reviews validates each one as the pipeline's ReviewCreate.
    assert len(reviews) >= 10


def test_every_case_these_checks_protect_exists_in_the_question_set() -> None:
    # A typo or a deleted case would otherwise leave a check protecting nothing.
    ids = {case.id for case in load_cases()}
    protected = set(MUST_NOT_MENTION.values()) | {
        key.split(" (")[0] for key in MUST_MENTION
    }

    assert protected <= ids, protected - ids


@pytest.mark.parametrize(("word", "case_id"), MUST_NOT_MENTION.items())
def test_reviews_never_mention_topics_cases_rely_on_being_absent(
    reviews: list[ReviewCreate], word: str, case_id: str
) -> None:
    mentions = [r.review_text for r in reviews if word in r.review_text.lower()]

    assert mentions == [], f"breaks {case_id}"


@pytest.mark.parametrize(("case_id", "words"), MUST_MENTION.items())
def test_reviews_hold_evidence_for_in_scope_topics(
    reviews: list[ReviewCreate], case_id: str, words: tuple[str, ...]
) -> None:
    texts = [r.review_text.lower() for r in reviews]

    assert any(word in text for text in texts for word in words), case_id


def test_reviews_include_one_star_reviews(reviews: list[ReviewCreate]) -> None:
    # in-scope-one-star-reasons and in-scope-top-complaints need real complaints.
    assert sum(r.rating == 1 for r in reviews) >= 2


def test_reviews_are_mixed_but_mostly_positive(reviews: list[ReviewCreate]) -> None:
    # in-scope-overall-sentiment needs a clear answer, with both sides present.
    positive = sum(r.rating >= 4 for r in reviews)
    negative = sum(r.rating <= 2 for r in reviews)

    assert negative > 0
    assert positive > negative
