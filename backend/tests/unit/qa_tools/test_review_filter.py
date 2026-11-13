from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.repositories.review_search import where_clause
from app.services.qa.tools.review_filter import MAX_PHRASE_LENGTH, ReviewFilter


def test_an_empty_filter_still_scopes_to_the_target():
    target_id = uuid4()

    sql, params = where_clause(target_id, ReviewFilter())

    assert sql == "target_id = %s"
    assert params == [target_id]


def test_every_condition_is_a_parameter_not_text_in_the_sql():
    since = datetime(2026, 3, 1, tzinfo=UTC)
    text = "slow'; drop table reviews"
    review_filter = ReviewFilter(keywords=text, min_rating=2, max_rating=4, since=since)

    sql, params = where_clause(uuid4(), review_filter)

    assert "drop table" not in sql
    assert sql.count("%s") == len(params) == 5
    assert text in params


def test_similar_to_is_refused_until_embedding_search_exists():
    with pytest.raises(ValidationError, match="keywords instead"):
        ReviewFilter(similar_to="rude staff")


@pytest.mark.parametrize(
    "bad",
    [
        {"min_rating": 0},
        {"max_rating": 6},
        {"min_rating": 4, "max_rating": 2},
        {"keywords": ""},
        {"keywords": "x" * (MAX_PHRASE_LENGTH + 1)},
        {"target_id": "someone-elses"},  # scope is never the model's to set
        {"owner_id": "someone-else"},
    ],
)
def test_bad_filters_are_rejected(bad):
    with pytest.raises(ValidationError):
        ReviewFilter(**bad)
