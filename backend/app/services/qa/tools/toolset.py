"""The seven tools for one question, fixed to one target (S2-010).

    reviews = ReviewSearchRepository.for_owner(conn, target_id, owner_id)
    toolset = ToolSet(reviews)          ownership checked once, above, in SQL
    toolset.registry.specs()            the menu for the model
    toolset.registry.run(name, args)    run one tool

for_owner is the ownership check, done once per question instead of inside every
query. The tools then read that one target, and the model never sees or passes an
owner or a target.

Make a new ToolSet for every question: it holds that question's labels, so nothing
carries over to the next one (S2-019).
"""

from functools import partial

from app.repositories.review_search import ReviewSearchRepository
from app.services.qa.tools import search, stats
from app.services.qa.tools.labels import LabelBook
from app.services.qa.tools.registry import Tool, ToolRegistry
from app.services.qa.tools.review_filter import ReviewFilter
from app.services.qa.tools.scope import ToolScope


class ToolSet:
    def __init__(self, reviews: ReviewSearchRepository) -> None:
        scope = ToolScope(reviews, reviews.target_id, LabelBook())
        self.labels = scope.labels
        self.registry = ToolRegistry(
            [
                Tool(
                    "review_stats",
                    "Count, average rating, share per star and date range of the reviews"
                    " that match the filter. Use keywords to count reviews that mention"
                    " something. With no filter it repeats the briefing.",
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
                    "compare_terms",
                    "Count and average rating for 2 to 5 keywords side by side, such as"
                    " service and food.",
                    stats.CompareTermsArgs,
                    partial(stats.compare_terms, scope),
                ),
                Tool(
                    "reviews_over_time",
                    "Number of reviews and average rating for each month (the latest"
                    " 24 months).",
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
                    "Find reviews. order picks relevance (needs keywords), newest,"
                    " oldest, lowest, highest or longest; left out, it is relevance"
                    " with keywords and newest without. Each result has a label such"
                    " as R1 to cite.",
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
