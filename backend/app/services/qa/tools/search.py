"""Tools that return review text, each review under a label (R1, R2, ...).

search_reviews  matching reviews, shortened, in a chosen order (relevance, newest,
                oldest, lowest, highest, longest) - so "the worst reviews" and
                "the most recent ones" need no sorting by the model. The order
                used is stated in the result.
get_reviews     the full text of reviews already shown, by label
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.repositories.review_search import ORDERS, RELEVANCE
from app.services.qa.tools.guard import quote, require_in_scope
from app.services.qa.tools.review_filter import ReviewFilter
from app.services.qa.tools.scope import ToolScope
from app.services.qa.tools.types import ToolResult

EXCERPT_CHARACTERS = 300
FULL_TEXT_CHARACTERS = 2000

Order = Literal["relevance", "newest", "oldest", "lowest", "highest", "longest"]
assert set(ORDERS) | {RELEVANCE} == set(Order.__args__)


class SearchArgs(ReviewFilter):
    # Left out: relevance when there are keywords, newest otherwise. Asking for
    # relevance without keywords is refused, never quietly changed.
    order: Order | None = None
    limit: Annotated[int, Field(ge=1, le=10)] = 5

    @model_validator(mode="after")
    def _relevance_needs_keywords(self) -> "SearchArgs":
        if self.order == RELEVANCE and self.keywords is None:
            raise ValueError(
                "order 'relevance' needs keywords; use newest, oldest, lowest,"
                " highest or longest instead"
            )
        return self


class GetReviewsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    labels: list[str] = Field(min_length=1, max_length=10)


def _line(scope: ToolScope, row: dict, characters: int) -> tuple[str, object]:
    date = f"{row['reviewed_at']:%Y-%m-%d}" if row["reviewed_at"] else "no date"
    detail = f"{row['rating']} stars, {date}"
    source = scope.labels.source_for(row["id"], detail)
    return (
        f"{source.label} | {detail} | {quote(row['review_text'], characters)}",
        source,
    )


def search_reviews(scope: ToolScope, args: SearchArgs) -> ToolResult:
    review_filter = ReviewFilter(**args.model_dump(exclude={"order", "limit"}))
    order = args.order or (RELEVANCE if args.keywords is not None else "newest")
    rows = scope.reviews.search(review_filter, order=order, limit=args.limit)
    require_in_scope(rows, scope.target_id)
    if not rows:
        return ToolResult(text="No reviews match.")
    total = scope.reviews.stats(review_filter)["review_count"]
    lines, sources = [], []
    for row in rows:
        line, source = _line(scope, row, EXCERPT_CHARACTERS)
        lines.append(line)
        sources.append(source)
    header = f"{len(rows)} of {total} matching reviews, {order} first:"
    return ToolResult(text=header + "\n" + "\n".join(lines), sources=sources)


def get_reviews(scope: ToolScope, args: GetReviewsArgs) -> ToolResult:
    ids = {label: scope.labels.review_id_for(label) for label in args.labels}
    unknown = [label for label, review_id in ids.items() if review_id is None]
    if unknown:
        return ToolResult(
            text=f"Unknown labels: {', '.join(unknown)}. Use labels from earlier results.",
            is_error=True,
        )
    rows = scope.reviews.get_many([i for i in ids.values() if i is not None])
    require_in_scope(rows, scope.target_id)
    by_id = {row["id"]: row for row in rows}
    lines, sources = [], []
    for review_id in ids.values():
        row = by_id.get(review_id)
        if row is None:
            continue
        line, source = _line(scope, row, FULL_TEXT_CHARACTERS)
        lines.append(line)
        sources.append(source)
    return ToolResult(text="\n".join(lines), sources=sources)
