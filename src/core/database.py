from typing import Any, AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.core.config import get_settings

settings = get_settings()


def build_engine_options(
    db_url: str,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_timeout: int = 15,
) -> dict[str, Any]:
    """Construye las opciones del engine acotando el pool solo para motores relacionales con pool."""
    options: dict[str, Any] = {
        "echo": False,
        "pool_pre_ping": True,
    }
    if not db_url.startswith("sqlite"):
        options["pool_size"] = max(1, int(pool_size))
        options["max_overflow"] = max(0, int(max_overflow))
        options["pool_timeout"] = max(1, int(pool_timeout))
    return options


engine = create_async_engine(
    settings.DATABASE_URL,
    **build_engine_options(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
    ),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)
async_session_factory = AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
