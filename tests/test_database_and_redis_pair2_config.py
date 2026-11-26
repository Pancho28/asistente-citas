import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.core.database import build_engine_options


def test_settings_pair2_defaults_without_env_file():
    settings = Settings(_env_file=None)

    assert "reservas_user:reservas_super_secret_password_2026@127.0.0.1:5433/citas_medicas" in settings.DATABASE_URL
    assert settings.REDIS_URL == "redis://:redis_secure_password_2026@127.0.0.1:6380/1"
    assert settings.DB_POOL_SIZE == 5
    assert settings.DB_MAX_OVERFLOW == 5
    assert settings.DB_POOL_TIMEOUT == 15


def test_settings_rejects_invalid_db_pool_values():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DB_POOL_SIZE=0)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, DB_MAX_OVERFLOW=-1)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, DB_POOL_TIMEOUT=0)


def test_build_engine_options_applies_pool_limits_for_postgres_and_omits_for_sqlite():
    pg_opts = build_engine_options(
        "postgresql+asyncpg://reservas_user:pass@127.0.0.1:5433/citas_medicas",
        pool_size=5,
        max_overflow=5,
        pool_timeout=15,
    )
    assert pg_opts["echo"] is False
    assert pg_opts["pool_pre_ping"] is True
    assert pg_opts["pool_size"] == 5
    assert pg_opts["max_overflow"] == 5
    assert pg_opts["pool_timeout"] == 15

    sqlite_opts = build_engine_options(
        "sqlite+aiosqlite:///:memory:",
        pool_size=5,
        max_overflow=5,
        pool_timeout=15,
    )
    assert sqlite_opts == {"echo": False, "pool_pre_ping": True}
