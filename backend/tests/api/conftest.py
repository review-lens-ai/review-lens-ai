"""Fixtures for endpoint tests. No database: the repository is replaced by an in-memory fake."""

import uuid
from datetime import UTC, datetime

import pytest

from app.api.targets import get_target_repository
from app.core.errors import NotFoundError
from app.main import app


class FakeTargetRepository:
    """Same ownership behavior as TargetRepository; the SQL itself is covered in tests/db."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.owners: dict[str, str] = {}
        self.create_calls = 0

    def create(self, owner_id, name, platform, source_url):
        self.create_calls += 1
        now = datetime.now(UTC)
        row = {
            "id": uuid.uuid4(),
            "name": name,
            "platform": platform,
            "source_url": source_url,
            "ingestion_status": None,
            "created_at": now,
            "updated_at": now,
        }
        self.rows[str(row["id"])] = row
        self.owners[str(row["id"])] = owner_id
        return row

    def list_for_owner(self, owner_id):
        return [row for key, row in self.rows.items() if self.owners[key] == owner_id]

    def get_owned(self, target_id, owner_id):
        key = str(target_id)
        if key not in self.rows or self.owners[key] != owner_id:
            raise NotFoundError("Target not found.")
        return self.rows[key]


@pytest.fixture
def repo(client):
    fake = FakeTargetRepository()
    app.dependency_overrides[get_target_repository] = lambda: fake
    return fake
