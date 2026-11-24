"""Analysis target endpoints, mounted under /api/v1/targets.

The router is mounted on api_router, so every endpoint here already requires a
verified user. Ownership always comes from get_current_user_id (the token),
never from the request.
"""

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError

from app.auth import OwnerId
from app.core.errors import error_responses
from app.db import get_connection
from app.platforms import SUPPORTED_PLATFORM
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewPage, ReviewSummary
from app.schemas.targets import TargetCreate, TargetList, TargetResponse
from app.services.qa.tools.review_filter import ReviewFilter

router = APIRouter(prefix="/targets", tags=["targets"])

# A target can hold thousands of reviews; the browser gets them a page at a time.
REVIEW_PAGE_SIZE = 50
MAX_REVIEW_PAGE_SIZE = 100
# Postgres rejects an OFFSET past bigint (a 500); no UI pages this deep anyway.
MAX_REVIEW_OFFSET = 100_000


def get_target_repository(
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> TargetRepository:
    return TargetRepository(conn)


def get_review_repository(
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> ReviewRepository:
    return ReviewRepository(conn)


Repo = Annotated[TargetRepository, Depends(get_target_repository)]
ReviewRepo = Annotated[ReviewRepository, Depends(get_review_repository)]
Rating = Annotated[int | None, Query(ge=1, le=5)]


def _invalid(field: str, message: str) -> RequestValidationError:
    """A 422 in the same shape as FastAPI's own validation errors."""
    return RequestValidationError(
        [{"loc": ("query", field), "msg": message, "type": "value_error"}]
    )


def review_filter(
    min_rating: Rating = None,
    max_rating: Rating = None,
    start_date: date | None = None,
    end_date: date | None = None,
    timezone: Annotated[str | None, Query(max_length=64)] = None,
) -> ReviewFilter:
    """S3-016: the reviews-list filter from the query string. Every part is optional
    and they combine with AND.

    Ratings are an inclusive range: min 4 is 4 and 5 stars, max 2 is 1 and 2.
    Dates are an inclusive range of whole days in the user's timezone (an IANA
    name such as "America/New_York", sent by the browser; UTC when absent), the
    same days the review cards show. end_date 2026-11-08 keeps reviews from any
    time that day there, so it becomes "before midnight starting 2026-11-09".
    A review with no date never matches a date filter. A range that is the wrong
    way round, or an unknown timezone, is a 422, never an empty list that looks
    like "no reviews"."""
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise _invalid("max_rating", "Maximum rating can't be lower than minimum.")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise _invalid("end_date", "End date can't be before start date.")
    zone = _zone(timezone)
    return ReviewFilter(
        min_rating=min_rating,
        max_rating=max_rating,
        since=_start_of(start_date, zone) if start_date else None,
        until=_start_of(end_date + timedelta(days=1), zone) if end_date else None,
    )


def _zone(name: str | None) -> tzinfo:
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise _invalid("timezone", "Unknown timezone.") from None


def _start_of(day: date, zone: tzinfo) -> datetime:
    """Midnight at the start of `day` in `zone` (daylight saving included)."""
    return datetime.combine(day, time.min, tzinfo=zone)


Filter = Annotated[ReviewFilter, Depends(review_filter)]


@router.get("", response_model=TargetList)
def list_targets(owner_id: OwnerId, repo: Repo) -> dict:
    """S1-017: only the signed-in user's targets. The owner filter runs in SQL
    (TargetRepository.list_for_owner), not in the browser."""
    return {"items": repo.list_for_owner(owner_id)}


@router.get(
    "/{target_id}", response_model=TargetResponse, responses=error_responses(404)
)
def get_target(target_id: UUID, owner_id: OwnerId, repo: Repo) -> dict:
    """S1-018: one target for its workspace. Another user's target is a 404,
    indistinguishable from one that does not exist."""
    return repo.get_owned(target_id, owner_id)


@router.get(
    "/{target_id}/reviews", response_model=ReviewPage, responses=error_responses(404)
)
def list_target_reviews(
    target_id: UUID,
    owner_id: OwnerId,
    reviews: ReviewRepo,
    matching: Filter,
    limit: Annotated[int, Query(ge=1, le=MAX_REVIEW_PAGE_SIZE)] = REVIEW_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_REVIEW_OFFSET)] = 0,
    around: UUID | None = None,
) -> dict:
    """S1-022: one page of a target's saved reviews, through its owner only.
    Another user's target is a 404, indistinguishable from one that does not exist.

    S3-016: min_rating, max_rating, start_date and end_date (see review_filter)
    keep only the matching reviews. total then counts the matches, so pages and
    "12 of 739" follow the filter. No filter returns every review, as before.

    around: the id of one review. The page that holds it is returned (offset is
    ignored), so a link to a review can open on the right page. A review that is
    not in this target, or does not match the filter, is a 404 too.

    Known limits, accepted for now:
    - items and total are two queries. A run that commits between them makes
      total count reviews the page did not see.
    - A run that finishes while someone pages shifts the rows, so the next page
      can repeat a few reviews. The UI goes back to page 1 when a run finishes.
    - Future: one repository method that checks the owner once and returns
      (rows, total) would save a round trip and could read both in one snapshot.
    """
    if around is not None:
        offset = reviews.page_offset_of(
            target_id, owner_id, around, limit=limit, review_filter=matching
        )
    return {
        "items": reviews.list_for_target(
            target_id, owner_id, limit=limit, offset=offset, review_filter=matching
        ),
        "total": reviews.count_for_target(target_id, owner_id, matching),
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{target_id}/summary", response_model=ReviewSummary, responses=error_responses(404)
)
def get_target_summary(target_id: UUID, owner_id: OwnerId, reviews: ReviewRepo) -> dict:
    """S2-008: review count, average rating and rating counts for one of the
    user's targets, calculated from its saved reviews (the same dataset Q&A uses).
    Another user's target is a 404, indistinguishable from one that does not exist.
    """
    return reviews.summary_for_target(target_id, owner_id)


@router.delete("/{target_id}", status_code=204, responses=error_responses(404))
def delete_target(target_id: UUID, owner_id: OwnerId, repo: Repo) -> None:
    """S1-012: delete one of your own targets. Another user's target is a 404."""
    repo.delete_owned(target_id, owner_id)


@router.post("", status_code=201, response_model=TargetResponse)
def create_target(payload: TargetCreate, owner_id: OwnerId, repo: Repo) -> dict:
    """S1-015: create a target owned by the signed-in user."""
    return repo.create(owner_id, payload.name, SUPPORTED_PLATFORM, payload.source_url)
