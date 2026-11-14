"""Tools that report numbers. The database does the arithmetic; the model reads it.

Each answers one question in one tool call, which matters with a few calls per
question:

review_stats      count, average, share per star, date range of the matching reviews
                  ("how many 1-2 star reviews mention slow?")
compare_periods   those numbers before and after a date ("did the renovation help?")
compare_terms     count and average for 2 to 5 keywords ("service vs food")
reviews_over_time reviews and average rating per month ("is it getting worse?")
top_terms         the words used in the most reviews

The numbers come from reviews.aggregate, the same query as the summary card, so
"every review" here always matches the screen.
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
    keywords: str | None = Field(
        default=None, min_length=1, max_length=MAX_PHRASE_LENGTH
    )
    min_rating: int | None = Field(default=None, ge=1, le=5)
    max_rating: int | None = Field(default=None, ge=1, le=5)


class CompareTermsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terms: list[Annotated[str, Field(min_length=1, max_length=MAX_PHRASE_LENGTH)]] = (
        Field(
            min_length=2,
            max_length=5,
            description="Keywords to compare, e.g. service and food.",
        )
    )


class TopTermsArgs(ReviewFilter):
    limit: Annotated[int, Field(ge=1, le=20)] = 10


def _day(value: datetime) -> str:
    return f"{value:%Y-%m-%d}"


def _describe(stats: dict) -> str:
    total = stats["review_count"]
    if total == 0:
        return "No reviews match."
    counts = {item["rating"]: item["count"] for item in stats["rating_counts"]}
    shares = ", ".join(
        f"{n} star: {counts[n]} ({round(100 * counts[n] / total)}%)"
        for n in range(5, 0, -1)
    )
    text = f"{total} reviews, average {stats['average_rating']:.2f} out of 5. {shares}."
    if stats["first_reviewed_at"] and stats["last_reviewed_at"]:
        text += (
            f" Reviewed between {_day(stats['first_reviewed_at'])}"
            f" and {_day(stats['last_reviewed_at'])}."
        )
    return text


def review_stats(scope: ToolScope, args: ReviewFilter) -> ToolResult:
    return ToolResult(text=_describe(scope.reviews.stats(args)))


def compare_periods(scope: ToolScope, args: CompareArgs) -> ToolResult:
    shared = {
        "keywords": args.keywords,
        "min_rating": args.min_rating,
        "max_rating": args.max_rating,
    }
    before = scope.reviews.stats(ReviewFilter(until=args.split, **shared))
    after = scope.reviews.stats(ReviewFilter(since=args.split, **shared))
    day = _day(args.split)
    return ToolResult(
        text=f"Before {day}: {_describe(before)}\nFrom {day}: {_describe(after)}"
    )


def compare_terms(scope: ToolScope, args: CompareTermsArgs) -> ToolResult:
    lines = []
    for term in args.terms:
        stats = scope.reviews.stats(ReviewFilter(keywords=term))
        if stats["review_count"] == 0:
            lines.append(f"{term}: no reviews mention it.")
        else:
            lines.append(
                f"{term}: {stats['review_count']} reviews,"
                f" average {stats['average_rating']:.2f} out of 5."
            )
    note = "A review that mentions several of these keywords is counted under each."
    return ToolResult(text="\n".join(lines) + "\n" + note)


def reviews_over_time(scope: ToolScope, args: ReviewFilter) -> ToolResult:
    rows, cut = scope.reviews.by_month(args)
    if not rows:
        return ToolResult(text="No dated reviews match.")
    lines = [
        f"{row['month']:%Y-%m}: {row['total']} reviews, average {row['average']:.2f}"
        for row in rows
    ]
    if cut:
        lines.append(
            f"Showing the latest {len(rows)} months; older months were left out."
        )
    return ToolResult(text="\n".join(lines))


def top_terms(scope: ToolScope, args: TopTermsArgs) -> ToolResult:
    review_filter = ReviewFilter(**args.model_dump(exclude={"limit"}))
    rows = scope.reviews.top_terms(review_filter, limit=args.limit)
    if not rows:
        return ToolResult(text="No reviews match.")
    lines = [f"{row['term']}: {row['reviews']} reviews" for row in rows]
    return ToolResult(
        text="Words used in the most reviews (stems):\n" + "\n".join(lines)
    )
