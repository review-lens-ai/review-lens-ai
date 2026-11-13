"""The six tools for one question, scoped to one owner and one target (S2-010).

    toolset = ToolSet(repository, owner_id, target_id)
    toolset.registry.specs()           the menu for the model
    toolset.registry.run(name, args)   run one tool

Make a new ToolSet for every question: it holds that question's labels, so nothing
carries over to the next one (S2-019).
"""

from functools import partial
from uuid import UUID

from app.repositories.review_search import ReviewSearchRepository
from app.services.qa.tools import search, stats
from app.services.qa.tools.labels import LabelBook
from app.services.qa.tools.registry import Tool, ToolRegistry
from app.services.qa.tools.review_filter import ReviewFilter
from app.services.qa.tools.scope import ToolScope


class ToolSet:
    def __init__(
        self,
        reviews: ReviewSearchRepository,
        owner_id: UUID | str,
        target_id: UUID | str,
    ) -> None:
        scope = ToolScope(reviews, owner_id, target_id, LabelBook())
        self.labels = scope.labels
        self.registry = ToolRegistry(
            [
                Tool(
                    "review_stats",
                    "Count, average rating, share per star and date range of the reviews"
                    " that match the filter. Use words to count reviews that mention"
                    " something.",
                    ReviewFilter,
                    partial(stats.review_stats, scope),
                ),
                Tool(
                    "compare_periods",
                    "The same numbers for reviews before and after a date.",
                    stats.CompareArgs,
                    partial(stats.compare_periods, scope),
                ),
                Tool(
                    "reviews_over_time",
                    "Number of reviews and average rating for each month.",
                    ReviewFilter,
                    partial(stats.reviews_over_time, scope),
                ),
                Tool(
                    "top_terms",
                    "The words used in the most reviews.",
                    stats.TopTermsArgs,
                    partial(stats.top_terms, scope),
                ),
                Tool(
                    "search_reviews",
                    "Find reviews. order picks relevance, newest, oldest, lowest,"
                    " highest or longest. Each result has a label such as R1 to cite.",
                    search.SearchArgs,
                    partial(search.search_reviews, scope),
                ),
                Tool(
                    "get_reviews",
                    "The full text of reviews you were already shown, by label.",
                    search.GetReviewsArgs,
                    partial(search.get_reviews, scope),
                ),
            ]
        )
