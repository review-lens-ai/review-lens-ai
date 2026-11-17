from uuid import uuid4

from pydantic import BaseModel, Field

from app.services.qa.tools.labels import LabelBook
from app.services.qa.tools.registry import Tool, ToolRegistry
from app.services.qa.tools.types import ToolResult


class _CountArgs(BaseModel):
    min_rating: int = Field(ge=1, le=5)


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                name="count_reviews",
                description="Count reviews at or above a rating.",
                arguments=_CountArgs,
                handler=lambda args: ToolResult(text=f"{args.min_rating} ok"),
            )
        ]
    )


def test_labels_start_at_r1_and_repeat_for_the_same_review():
    book = LabelBook()
    first, second = uuid4(), uuid4()
    assert book.source_for(first, "a").label == "R1"
    assert book.source_for(second, "b").label == "R2"
    assert book.source_for(first, "a").label == "R1"


def test_a_new_book_restarts_numbering():
    LabelBook().source_for(uuid4(), "a")
    assert LabelBook().source_for(uuid4(), "b").label == "R1"


def test_run_returns_the_tool_result():
    result = _registry().run("count_reviews", {"min_rating": 4})
    assert result.text == "4 ok"
    assert not result.is_error


def test_unknown_tool_is_an_error_result_that_lists_the_menu():
    result = _registry().run("drop_table", {})
    assert result.is_error
    assert "count_reviews" in result.text


def test_bad_arguments_are_an_error_result_not_an_exception():
    result = _registry().run("count_reviews", {"min_rating": 9})
    assert result.is_error
    assert "min_rating" in result.text


def test_specs_describe_every_tool_for_the_model():
    [spec] = _registry().specs()
    assert spec["name"] == "count_reviews"
    assert "min_rating" in spec["parameters"]["properties"]
