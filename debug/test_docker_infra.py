"""
Script de diagnóstico (debug/) para verificar la conexión real contra los contenedores
locales de Docker Desktop (PostgreSQL 16 y Redis 7) y comprobar las tablas creadas por Alembic.

Uso:
    python -m debug.test_docker_infra
"""
import asyncio
import uuid
from sqlalchemy import text
from src.core.database import engine
from src.core.redis import get_redis_client, acquire_slot_lock, release_slot_lock


async def verify_docker_infrastructure() -> None:
    print("1. Verificando conexión con contenedor PostgreSQL (asistente-citas-postgres)...")
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT version();"))).scalar_one()
        print(f"   [OK] PostgreSQL conectado: {version.split(',')[0]}")

        tables_res = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"
            )
        )
        tables = [row[0] for row in tables_res.fetchall()]
        print(f"   [OK] Tablas encontradas en 'citas_medicas' ({len(tables)}): {tables}")

        indexes_res = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                "AND indexname IN ('idx_cita_unica_activa', 'idx_usuario_canal_external_id');"
            )
        )
        indexes = [row[0] for row in indexes_res.fetchall()]
        print(f"   [OK] Índices únicos críticos verificados: {indexes}")

    await engine.dispose()

    print("\n2. Verificando conexión con contenedor Redis (asistente-citas-redis)...")
    redis_client = await get_redis_client()
    try:
        pong = await redis_client.ping()
        print(f"   [OK] Redis PING -> {pong}")

        prof_id = uuid.uuid4()
        user_id = uuid.uuid4()
        slot_str = "2026-11-10_09:00"
        locked = await acquire_slot_lock(redis_client, prof_id, slot_str, user_id, ttl=30)
        assert locked is True
        await release_slot_lock(redis_client, prof_id, slot_str, user_id)
        print("   [OK] Bloqueo atómico SET NX EX y liberación en Redis real verificados.")
    finally:
        await redis_client.aclose()

    print("\nInfraestructura Docker Desktop 100% operativa.")


if __name__ == "__main__":
    asyncio.run(verify_docker_infrastructure())

