from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.platforms import validate_source_url

MAX_NAME_LENGTH = 200


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
        value = value.strip()
        if not value:
            raise ValueError("Enter a name for what you want to analyze.")
        if len(value) > MAX_NAME_LENGTH:
            raise ValueError(f"Keep the name under {MAX_NAME_LENGTH} characters.")
        return value

    @field_validator("source_url")
    @classmethod
    def _clean_source_url(cls, value: str) -> str:
        return validate_source_url(value)


class TargetResponse(BaseModel):
    id: UUID
    name: str
    platform: str
    source_url: str
    # Status of the newest ingestion run; None means "no ingestion yet".
    ingestion_status: str | None
    created_at: datetime
    updated_at: datetime
