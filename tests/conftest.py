import os
os.environ["ENVIRONMENT"] = "test"

import pytest
import pytest_asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from src.models.base import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            try:
                await session.rollback()
                await session.close()
            except Exception:
                pass
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def override_db_dependency(async_session: AsyncSession):
    """Sobrescribe automáticamente get_db y get_redis_client en FastAPI para pruebas unitarias."""
    from src.main import app
    from src.core.database import get_db
    from src.core.redis import get_redis_client

    fake_redis_instance = FakeRedis()

    async def _test_get_db():
        try:
            yield async_session
        finally:
            try:
                await async_session.rollback()
            except Exception:
                pass

    async def _test_get_redis():
        return fake_redis_instance

    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_redis_client] = _test_get_redis
    try:
        yield
    finally:
        app.dependency_overrides.clear()


class FakeRedis:
    """Mock/Fake asíncrono para redis.asyncio.Redis compartido para tests."""
    def __init__(self):
        self.store = {}
        self.ttls = {}
        self.lists = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        if ex:
            self.ttls[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        removed = 0
        if key in self.store:
            del self.store[key]
            removed = 1
        if key in self.ttls:
            del self.ttls[key]
        if key in self.lists:
            del self.lists[key]
            removed = 1
        return removed

    async def rpush(self, key: str, value: str) -> int:
        if key not in self.lists:
            self.lists[key] = []
        self.lists[key].append(value)
        return len(self.lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start:end + 1]

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        if key in self.lists:
            items = self.lists[key]
            if end == -1:
                self.lists[key] = items[start:]
            else:
                self.lists[key] = items[start:end + 1]
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        if key in self.store or key in self.lists:
            self.ttls[key] = seconds
            return True
        return False

