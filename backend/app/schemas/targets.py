from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.platforms import validate_source_url
from app.schemas.ingestion_runs import IngestionStatus

MAX_NAME_LENGTH = 200


def clean_target_name(value: str) -> str:
    """The name without surrounding whitespace; ValueError if it can't be used.
    The one set of name rules, shared by TargetCreate and TargetRename."""
    value = value.strip()
    if not value:
        raise ValueError("Enter a name for what you want to analyze.")
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"Keep the name under {MAX_NAME_LENGTH} characters.")
    if not value.isprintable():
        raise ValueError("The name can't contain line breaks or hidden characters.")
    if not any(ch.isalnum() for ch in value):
        raise ValueError("The name needs at least one letter or number.")
    return value


class TargetCreate(BaseModel):
    """What a client may send when creating a target.

    There is deliberately NO owner_id / user_id field: ownership comes only from
    the verified token (S1-BR-010). Any such key a client adds is ignored.
    The platform is fixed by app.platforms, so it is not sent either.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    source_url: str

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return clean_target_name(value)

    @field_validator("source_url")
    @classmethod
    def _clean_source_url(cls, value: str) -> str:
        return validate_source_url(value)


class TargetRename(BaseModel):
    """S3-002: the only thing a rename may change is the name (S3-BR-002). Any
    other key, such as source_url or owner_id, is ignored."""

    model_config = ConfigDict(extra="ignore")

    name: str

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return clean_target_name(value)


class TargetResponse(BaseModel):
    id: UUID
    name: str
    platform: str
    source_url: str
    # Status of the newest ingestion run; None means "no ingestion yet".
    ingestion_status: IngestionStatus | None
    created_at: datetime
    updated_at: datetime


class TargetList(BaseModel):
    items: list[TargetResponse]
