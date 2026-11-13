"""Tools that report numbers. The database does the arithmetic; the model reads it.

review_stats      count, average, share per star, date range (also answers
                  "how many reviews mention X?" through the words filter)
compare_periods   the same numbers before and after a date
reviews_over_time reviews and average rating per month
top_terms         the words used in the most reviews
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.services.qa.tools.review_filter import MAX_PHRASE_LENGTH, ReviewFilter
from app.services.qa.tools.scope import ToolScope
from app.services.qa.tools.types import ToolResult


class CompareArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    split: datetime = Field(description="Compare reviews before and after this date.")
    words: str | None = Field(default=None, min_length=1, max_length=MAX_PHRASE_LENGTH)
    min_rating: int | None = Field(default=None, ge=1, le=5)
    max_rating: int | None = Field(default=None, ge=1, le=5)


class TopTermsArgs(ReviewFilter):
    limit: Annotated[int, Field(ge=1, le=20)] = 10


def _day(value: datetime) -> str:
    return f"{value:%Y-%m-%d}"


def _describe(stats: dict) -> str:
    total = stats["total"]
    if total == 0:
        return "No reviews match."
    shares = ", ".join(
        f"{n} star: {stats['per_star'][n]} ({round(100 * stats['per_star'][n] / total)}%)"
        for n in range(5, 0, -1)
    )
    text = f"{total} reviews, average {stats['average']:.2f} out of 5. {shares}."
    if stats["first_reviewed_at"] and stats["last_reviewed_at"]:
        text += (
            f" Reviewed between {_day(stats['first_reviewed_at'])}"
            f" and {_day(stats['last_reviewed_at'])}."
        )
    return text


def review_stats(scope: ToolScope, args: ReviewFilter) -> ToolResult:
    stats = scope.reviews.stats(scope.target_id, scope.owner_id, args)
    return ToolResult(text=_describe(stats))


def compare_periods(scope: ToolScope, args: CompareArgs) -> ToolResult:
    shared = {
        "words": args.words,
        "min_rating": args.min_rating,
        "max_rating": args.max_rating,
    }
    before = scope.reviews.stats(
        scope.target_id, scope.owner_id, ReviewFilter(until=args.split, **shared)
    )
    after = scope.reviews.stats(
        scope.target_id, scope.owner_id, ReviewFilter(since=args.split, **shared)
    )
    day = _day(args.split)
    return ToolResult(
        text=f"Before {day}: {_describe(before)}\nFrom {day}: {_describe(after)}"
    )


def reviews_over_time(scope: ToolScope, args: ReviewFilter) -> ToolResult:
    rows = scope.reviews.by_month(scope.target_id, scope.owner_id, args)
    if not rows:
        return ToolResult(text="No dated reviews match.")
    lines = [
        f"{row['month']:%Y-%m}: {row['total']} reviews, average {row['average']:.2f}"
        for row in rows
    ]
    return ToolResult(text="\n".join(lines))


def top_terms(scope: ToolScope, args: TopTermsArgs) -> ToolResult:
    flt = ReviewFilter(**args.model_dump(exclude={"limit"}))
    rows = scope.reviews.top_terms(
        scope.target_id, scope.owner_id, flt, limit=args.limit
    )
    if not rows:
        return ToolResult(text="No reviews match.")
    lines = [f"{row['term']}: {row['reviews']} reviews" for row in rows]
    return ToolResult(
        text="Words used in the most reviews (stems):\n" + "\n".join(lines)
    )
