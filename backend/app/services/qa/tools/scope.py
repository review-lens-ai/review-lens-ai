"""What one question's tools are allowed to see (S2-010).

Made once per question, so labels and scope never carry over to the next one
(S2-019). The owner and the target are fixed here, not chosen by the model.
"""

from dataclasses import dataclass
from uuid import UUID

from app.repositories.review_search import ReviewSearchRepository
from app.services.qa.tools.labels import LabelBook


@dataclass(frozen=True)
class ToolScope:
    reviews: ReviewSearchRepository
    owner_id: UUID | str
    target_id: UUID | str
    labels: LabelBook
