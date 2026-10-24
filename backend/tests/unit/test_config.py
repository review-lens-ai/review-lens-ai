import pytest

from app.config import Settings

SUPABASE_URL = "https://test-project.supabase.co"
DATABASE_URL = "postgresql://user:fake-password@localhost:5432/test"


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)


def test_settings_load_with_required_vars(required_env):
    settings = Settings(_env_file=None)

    assert settings.supabase_url == SUPABASE_URL
    assert settings.database_url.get_secret_value() == DATABASE_URL
    assert settings.max_reviews_per_run == 1000


def test_missing_required_var_fails_with_clear_message(required_env, monkeypatch):
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(RuntimeError, match="Missing required setting: DATABASE_URL"):
        Settings(_env_file=None)


def test_secret_values_are_masked(required_env):
    settings = Settings(_env_file=None)

    assert DATABASE_URL not in str(settings.database_url)
    assert DATABASE_URL not in repr(settings.database_url)
    assert DATABASE_URL not in repr(settings)
