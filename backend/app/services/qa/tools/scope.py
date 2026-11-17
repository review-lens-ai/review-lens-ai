"""What one question's tools are allowed to see (S2-010).

Made once per question, so labels and scope never carry over to the next one
(S2-019). The target is the one ReviewSearchRepository.for_owner checked in SQL;
the model never chooses it. target_id is kept here for the row re-check in guard.py.
"""

from dataclasses import dataclass
from uuid import UUID

from app.repositories.review_search import ReviewSearchRepository
from app.services.qa.tools.labels import LabelBook


@dataclass(frozen=True)
class ToolScope:
    reviews: ReviewSearchRepository
    target_id: UUID | str
    labels: LabelBook
