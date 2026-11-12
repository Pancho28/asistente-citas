import uuid
from datetime import datetime, timezone, time, timedelta
from decimal import Decimal
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from src.models.user import Usuario, PerfilProfesional
from src.models.appointment import Cita, Pago
from src.core.redis import acquire_slot_lock, release_slot_lock, append_chat_history, get_chat_history


class FakeRedis:
    """Mock/Fake asíncrono para redis.asyncio.Redis."""
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


@pytest.mark.asyncio
async def test_idx_cita_unica_activa_blocks_duplicate_active_slot(async_session):
    # 1. Crear usuario médico y perfil profesional
    medico_user = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="111222",
        nombre_completo="Dr. Gregory House",
        telefono="+584121112233",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    paciente_1 = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="333444",
        nombre_completo="Paciente Uno",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    paciente_2 = Usuario(
        id=uuid.uuid4(),
        canal_origen="WHATSAPP",
        external_chat_id="+584149998877",
        nombre_completo="Paciente Dos",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add_all([medico_user, paciente_1, paciente_2])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico_user.id,
        especialidad="Diagnóstico",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("50.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=2,
    )
    async_session.add(perfil)
    await async_session.commit()

    slot_time = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 11, 10, 10, 30, tzinfo=timezone.utc)

    # 2. Primera cita confirmada en el slot
    cita1 = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente_1.id,
        fecha_hora_inicio=slot_time,
        fecha_hora_fin=slot_end,
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita1)
    await async_session.commit()

    # 3. Intentar agendar segunda cita activa en el mismo slot para el mismo profesional
    cita2 = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente_2.id,
        fecha_hora_inicio=slot_time,
        fecha_hora_fin=slot_end,
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
    )
    async_session.add(cita2)
    with pytest.raises(IntegrityError):
        await async_session.commit()
    await async_session.rollback()


@pytest.mark.asyncio
async def test_idx_cita_unica_activa_allows_rebooking_after_cancelled(async_session):
    medico_user = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="555666",
        nombre_completo="Dra. Wilson",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    paciente_1 = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="777888",
        nombre_completo="Paciente Tres",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    paciente_2 = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="999000",
        nombre_completo="Paciente Cuatro",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add_all([medico_user, paciente_1, paciente_2])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico_user.id,
        especialidad="Oncología",
        duracion_cita_minutos=45,
        tarifa_base_consulta=Decimal("60.00"),
        moneda="USD",
    )
    async_session.add(perfil)
    await async_session.commit()

    slot_time = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 11, 12, 14, 45, tzinfo=timezone.utc)

    # 1. Cita inicial se cancela
    cita1 = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente_1.id,
        fecha_hora_inicio=slot_time,
        fecha_hora_fin=slot_end,
        estado="CANCELADA",
        monto_acordado=Decimal("60.00"),
        estado_financiero="EXONERADO",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita1)
    await async_session.commit()

    # 2. Nueva cita en el mismo slot DEBE ser permitida gracias a que estado es CANCELADA
    cita2 = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente_2.id,
        fecha_hora_inicio=slot_time,
        fecha_hora_fin=slot_end,
        estado="CONFIRMADA",
        monto_acordado=Decimal("60.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita2)
    await async_session.commit()

    res = await async_session.execute(select(Cita).where(Cita.profesional_id == perfil.id))
    citas = res.scalars().all()
    assert len(citas) == 2


@pytest.mark.asyncio
async def test_idx_usuario_canal_external_id_unique(async_session):
    u1 = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="123456",
        nombre_completo="Usuario Unico",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(u1)
    await async_session.commit()

    u2 = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="123456",  # Mismo canal y mismo ID externo
        nombre_completo="Usuario Duplicado",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(u2)
    with pytest.raises(IntegrityError):
        await async_session.commit()
    await async_session.rollback()


@pytest.mark.asyncio
async def test_redis_slot_lock_nx_and_reentrant():
    fake_redis = FakeRedis()
    prof_id = uuid.uuid4()
    slot_str = "2026-11-15_09:00"
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    # 1. user_a adquiere lock
    acquired = await acquire_slot_lock(fake_redis, prof_id, slot_str, user_a, ttl=300)
    assert acquired is True

    # 2. user_b intenta adquirir el mismo lock y falla
    acquired_b = await acquire_slot_lock(fake_redis, prof_id, slot_str, user_b, ttl=300)
    assert acquired_b is False

    # 3. user_a reintenta adquirir su propio lock (reentrante) y tiene éxito
    re_acquired = await acquire_slot_lock(fake_redis, prof_id, slot_str, user_a, ttl=300)
    assert re_acquired is True

    # 4. user_b intenta liberar el lock de user_a y no surte efecto
    await release_slot_lock(fake_redis, prof_id, slot_str, user_b)
    current_holder = await fake_redis.get(f"lock:slot:{prof_id}:{slot_str}")
    assert current_holder == str(user_a)

    # 5. user_a libera su propio lock
    await release_slot_lock(fake_redis, prof_id, slot_str, user_a)
    assert await fake_redis.get(f"lock:slot:{prof_id}:{slot_str}") is None

    # 6. Ahora user_b puede adquirirlo
    acquired_b_now = await acquire_slot_lock(fake_redis, prof_id, slot_str, user_b, ttl=300)
    assert acquired_b_now is True


@pytest.mark.asyncio
async def test_redis_chat_history_trim_and_ttl():
    fake_redis = FakeRedis()
    user_id = uuid.uuid4()

    # Añadir 15 mensajes
    for i in range(15):
        await append_chat_history(
            fake_redis,
            user_id=user_id,
            role="user" if i % 2 == 0 else "model",
            content=f"Mensaje {i}",
            max_len=10,
            ttl=7200,
        )

    history = await get_chat_history(fake_redis, user_id)
    # Debe conservar exactamente los últimos 10
    assert len(history) == 10
    assert history[0]["content"] == "Mensaje 5"
    assert history[-1]["content"] == "Mensaje 14"
    assert fake_redis.ttls.get(f"chat:history:{user_id}") == 7200


def test_railway_database_url_normalization():
    from src.core.config import Settings

    s1 = Settings(DATABASE_URL="postgres://user:pass@postgres.railway.internal:5432/railway")
    assert s1.DATABASE_URL == "postgresql+asyncpg://user:pass@postgres.railway.internal:5432/railway"

    s2 = Settings(DATABASE_URL="postgresql://user:pass@postgres.railway.internal:5432/railway")
    assert s2.DATABASE_URL == "postgresql+asyncpg://user:pass@postgres.railway.internal:5432/railway"

    s3 = Settings(DATABASE_URL="postgresql+asyncpg://postgres:password@localhost:5432/citas_medicas")
    assert s3.DATABASE_URL == "postgresql+asyncpg://postgres:password@localhost:5432/citas_medicas"


