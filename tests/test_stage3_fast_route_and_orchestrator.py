import uuid
from typing import Any
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.schemas.context import UserContext, RoleEnum, ChannelOrigin
from src.core.config import get_settings, now_tz
from src.core.exceptions import PermissionDeniedError
from src.models.user import Usuario, PerfilProfesional
from src.models.schedule import HorarioBase
from src.models.appointment import Cita
from tests.conftest import FakeRedis

settings = get_settings()


@pytest.fixture
async def setup_test_doctor(async_session: AsyncSession):
    medico = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="doc_fast",
        nombre_completo="Dra. Ana López",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(medico)
    await async_session.commit()
    await async_session.refresh(medico)

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Pediatría",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("40.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=2,
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil)
    await async_session.commit()
    await async_session.refresh(perfil)

    # Lunes a Viernes
    for d in range(1, 6):
        async_session.add(
            HorarioBase(
                profesional_id=perfil.id,
                dia_semana=d,
                hora_inicio=time(8, 0),
                hora_fin=time(14, 0),
                activo=True,
            )
        )
    await async_session.commit()
    await async_session.refresh(perfil)
    return medico, perfil


# =========================================================================
# 1. Tests de Ruta Rápida (0 Tokens)
# =========================================================================

@pytest.mark.asyncio
async def test_fast_route_book_button(async_session: AsyncSession, setup_test_doctor):
    from src.agent.fast_route import process_fast_route

    medico, perfil = setup_test_doctor
    fake_redis = FakeRedis()

    paciente = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="pac_fast",
        nombre_completo="Carlos Prueba",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(paciente)
    await async_session.commit()
    await async_session.refresh(paciente)

    ctx_paciente = UserContext(
        user_id=paciente.id,
        external_chat_id=paciente.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["citas:agendar_propia"]),
    )

    # Próximo lunes
    today = now_tz().date()
    days_ahead = (0 - today.weekday() + 7) % 7 or 7
    target_date = today + timedelta(days=days_ahead)
    date_str = target_date.strftime("%Y%m%d")
    short_prof_id = str(perfil.id)[:8]
    callback_data = f"BOOK|{short_prof_id}|{date_str}_0900"

    response = await process_fast_route(ctx_paciente, callback_data, async_session, fake_redis)

    assert response is not None
    assert "agendada" in response.reply_text.lower() or "confirmada" in response.reply_text.lower()


@pytest.mark.asyncio
async def test_fast_route_reminder_confirmation_and_cancel(async_session: AsyncSession, setup_test_doctor):
    from src.agent.fast_route import process_fast_route

    medico, perfil = setup_test_doctor
    fake_redis = FakeRedis()

    paciente = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="pac_reminder",
        nombre_completo="María Recordatorio",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(paciente)
    await async_session.commit()
    await async_session.refresh(paciente)

    cita = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=now_tz() + timedelta(days=2),
        fecha_hora_fin=now_tz() + timedelta(days=2, minutes=30),
        estado="CONFIRMADA",
        monto_acordado=Decimal("40.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
    )
    async_session.add(cita)
    await async_session.commit()
    await async_session.refresh(cita)

    ctx_paciente = UserContext(
        user_id=paciente.id,
        external_chat_id=paciente.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["citas:modificar_propia"]),
    )

    # 1. R_OK (Confirmar)
    res_ok = await process_fast_route(ctx_paciente, f"R_OK|{cita.id}", async_session, fake_redis)
    assert "confirmada" in res_ok.reply_text.lower() or "gracias" in res_ok.reply_text.lower()
    await async_session.refresh(cita)
    assert cita.estado == "CONFIRMADA_PACIENTE"
    assert cita.respuesta_recordatorio == "CONFIRMO"

    # 2. R_CAN (Cancelar)
    res_can = await process_fast_route(ctx_paciente, f"R_CAN|{cita.id}", async_session, fake_redis)
    assert "cancelada" in res_can.reply_text.lower()
    await async_session.refresh(cita)
    assert cita.estado == "CANCELADA"


@pytest.mark.asyncio
async def test_fast_route_doctor_quick_actions(async_session: AsyncSession, setup_test_doctor):
    from src.agent.fast_route import process_fast_route

    medico, perfil = setup_test_doctor
    fake_redis = FakeRedis()

    paciente = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="pac_doc_act",
        nombre_completo="Luis Paciente",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(paciente)
    await async_session.commit()
    await async_session.refresh(paciente)

    cita = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=now_tz() - timedelta(hours=1),
        fecha_hora_fin=now_tz() - timedelta(minutes=30),
        estado="COMPLETADA",
        monto_acordado=Decimal("40.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
    )
    async_session.add(cita)
    await async_session.commit()
    await async_session.refresh(cita)

    ctx_medico = UserContext(
        user_id=medico.id,
        external_chat_id=medico.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.MEDICO,
        permissions=frozenset(["finanzas:registrar_pago", "citas:gestion_manual_medico"]),
    )

    # PAY_FULL
    res_pay = await process_fast_route(ctx_medico, f"PAY_FULL|{cita.id}", async_session, fake_redis)
    assert "pagada" in res_pay.reply_text.lower()
    await async_session.refresh(cita)
    assert cita.estado_financiero == "PAGADO"


# =========================================================================
# 2. Tests de Comandos Rápidos de Vista (/modo_admin, etc.)
# =========================================================================

@pytest.mark.asyncio
async def test_slash_view_commands(async_session: AsyncSession):
    from src.agent.fast_route import try_process_slash_view_command

    # 1. Admin real
    admin = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="admin_slash",
        nombre_completo="Super Admin",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
        activo=True,
    )
    async_session.add(admin)
    await async_session.commit()
    await async_session.refresh(admin)

    ctx_admin = UserContext(
        user_id=admin.id,
        external_chat_id=admin.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.ADMIN,
        permissions=frozenset(["admin:cambiar_vista"]),
    )

    res_medico = await try_process_slash_view_command(ctx_admin, "/modo_medico", async_session)
    assert res_medico is not None
    assert "medico" in res_medico.reply_text.lower()
    await async_session.refresh(admin)
    assert admin.modo_vista_actual == "MEDICO"

    # 2. Paciente no puede
    paciente = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="pac_slash",
        nombre_completo="No Admin",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(paciente)
    await async_session.commit()
    await async_session.refresh(paciente)

    ctx_paciente = UserContext(
        user_id=paciente.id,
        external_chat_id=paciente.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(),
    )

    with pytest.raises(PermissionDeniedError):
        await try_process_slash_view_command(ctx_paciente, "/modo_admin", async_session)


# =========================================================================
# 3. Tests de Prompts y Orquestador
# =========================================================================

def test_temporal_anchor_in_system_prompt():
    from src.agent.prompts import build_system_prompt

    ctx = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="123",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(),
    )
    dt = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)
    prompt = build_system_prompt(ctx, dt)

    assert "2026-11-09" in prompt
    assert "14:30" in prompt
    assert "Lunes" in prompt or "lunes" in prompt.lower()
    assert "motivo de consulta" in prompt.lower()


@pytest.mark.asyncio
async def test_orchestrator_process_message_with_mocked_genai(async_session: AsyncSession):
    from src.agent.orchestrator import process_message_with_ai

    fake_redis = FakeRedis()
    ctx = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="123",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar"]),
    )

    # Mock de Google GenAI Client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.function_calls = None
    mock_response.text = "¡Hola! Con gusto te ayudo a agendar tu cita."
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    res = await process_message_with_ai(
        ctx=ctx,
        text="Hola asistente",
        db=async_session,
        redis=fake_redis,
        genai_client=mock_client,
    )

    assert res.reply_text == "¡Hola! Con gusto te ayudo a agendar tu cita."
    assert mock_client.aio.models.generate_content.called

    # Verificar que el historial se guardó en Redis
    history = await fake_redis.lrange(f"chat:history:{ctx.user_id}", 0, -1)
    assert len(history) == 2


@pytest.mark.asyncio
async def test_orchestrator_function_calling_propagates_ui_hint(async_session: AsyncSession, setup_test_doctor):
    from src.agent.orchestrator import process_message_with_ai

    medico, perfil = setup_test_doctor
    fake_redis = FakeRedis()

    ctx = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="12345",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar"]),
    )

    # Próximo lunes
    today = now_tz().date()
    days_ahead = (0 - today.weekday() + 7) % 7 or 7
    target_date = today + timedelta(days=days_ahead)
    target_date_str = target_date.strftime("%Y-%m-%d")

    # Mock con llamada a función
    mock_client = MagicMock()

    mock_func_call = MagicMock()
    mock_func_call.name = "consultar_disponibilidad"
    mock_func_call.args = {"fecha": target_date_str}

    first_response = MagicMock()
    first_response.function_calls = [mock_func_call]
    first_response.text = None

    second_response = MagicMock()
    second_response.function_calls = None
    second_response.text = f"Tenemos estos horarios disponibles para el {target_date_str}."

    mock_client.aio.models.generate_content = AsyncMock(side_effect=[first_response, second_response])

    res = await process_message_with_ai(
        ctx=ctx,
        text=f"¿Qué horas tienes para el {target_date_str}?",
        db=async_session,
        redis=fake_redis,
        genai_client=mock_client,
    )

    assert res.reply_text == f"Tenemos estos horarios disponibles para el {target_date_str}."
    assert res.ui_hint == "SHOW_BOOKING_BUTTONS"
    assert res.ui_data is not None
    assert len(res.ui_data.get("horarios_libres", [])) > 0


@pytest.mark.asyncio
async def test_orchestrator_failover_on_503_unavailable(async_session: AsyncSession):
    from src.agent.orchestrator import process_message_with_ai
    from google.genai.errors import ServerError

    fake_redis = FakeRedis()
    ctx = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="123",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar"]),
    )

    mock_client = MagicMock()
    mock_resp_success = MagicMock()
    mock_resp_success.function_calls = None
    mock_resp_success.text = "Respuesta desde el modelo de respaldo."

    error_503 = ServerError(503, {"error": {"message": "High demand"}}, MagicMock())

    async def mock_generate_content(model: str, contents: Any, config: Any = None):
        # El modelo primario falla con 503
        if model == settings.gemini_models_list[0]:
            raise error_503
        # El modelo de respaldo responde con éxito
        return mock_resp_success

    mock_client.aio.models.generate_content = AsyncMock(side_effect=mock_generate_content)

    res = await process_message_with_ai(
        ctx=ctx,
        text="Hola",
        db=async_session,
        redis=fake_redis,
        genai_client=mock_client,
    )

    assert res.reply_text == "Respuesta desde el modelo de respaldo."
    # Verificar que se llamó con el modelo secundario
    called_models = [call.kwargs.get("model") for call in mock_client.aio.models.generate_content.call_args_list]
    assert settings.gemini_models_list[0] in called_models
    assert len(called_models) >= 2


@pytest.mark.asyncio
async def test_orchestrator_graceful_response_when_all_models_fail(async_session: AsyncSession):
    from src.agent.orchestrator import process_message_with_ai
    from google.genai.errors import ServerError

    fake_redis = FakeRedis()
    ctx = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="123",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar"]),
    )

    mock_client = MagicMock()
    error_503 = ServerError(503, {"error": {"message": "All models busy"}}, MagicMock())
    mock_client.aio.models.generate_content = AsyncMock(side_effect=error_503)

    res = await process_message_with_ai(
        ctx=ctx,
        text="Hola",
        db=async_session,
        redis=fake_redis,
        genai_client=mock_client,
    )

    # Debe devolver un AgentResponse controlado y amigable en vez de romper
    assert res is not None
    assert "alta demanda" in res.reply_text.lower() or "intenta" in res.reply_text.lower()


# =========================================================================
# 5. CATÁLOGO DINÁMICO DE MÉDICOS Y ESPECIALIDADES EN SALUDO (MEJORA 9)
# =========================================================================

@pytest.mark.asyncio
async def test_listar_catalogo_medicos_activos_excludes_inactive_doctors(async_session: AsyncSession):
    from src.services.availability_service import listar_catalogo_medicos_activos

    # 1. Médico Activo
    doc_activo = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_activo_cat",
        nombre_completo="Dra. Carmen Soto",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(doc_activo)
    await async_session.commit()
    perfil_activo = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=doc_activo.id,
        especialidad="Dermatología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("45.00"),
        moneda="USD",
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil_activo)

    # 2. Médico Inactivo (acepta_nuevas_citas = False)
    doc_inactivo = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_inactivo_cat",
        nombre_completo="Dr. Inactivo",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=False,
    )
    async_session.add(doc_inactivo)
    await async_session.commit()
    perfil_inactivo = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=doc_inactivo.id,
        especialidad="Neurología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("80.00"),
        moneda="USD",
        acepta_nuevas_citas=False,
    )
    async_session.add(perfil_inactivo)
    await async_session.commit()

    catalogo = await listar_catalogo_medicos_activos(async_session)

    assert len(catalogo) >= 1
    nombres = [m["nombre_profesional"] for m in catalogo]
    especialidades = [m["especialidad"] for m in catalogo]

    assert "Dra. Carmen Soto" in nombres
    assert "Dermatología" in especialidades
    assert "Dr. Inactivo" not in nombres
    assert "Neurología" not in especialidades


@pytest.mark.asyncio
async def test_patient_system_prompt_injects_active_doctors_and_greeting_rule(async_session: AsyncSession):
    from src.agent.orchestrator import process_message_with_ai

    # Crear médico activo
    doc = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_perez_prompt",
        nombre_completo="Dr. Juan Pérez",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(doc)
    await async_session.commit()
    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=doc.id,
        especialidad="Cardiología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("50.00"),
        moneda="USD",
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil)
    await async_session.commit()

    fake_redis = FakeRedis()
    ctx_paciente = UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="pac_greeting",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar"]),
    )

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.function_calls = None
    mock_response.text = "¡Hola! Bienvenido. Tenemos disponibles al Dr. Juan Pérez en Cardiología."
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    res = await process_message_with_ai(
        ctx=ctx_paciente,
        text="Hola",
        db=async_session,
        redis=fake_redis,
        genai_client=mock_client,
    )

    assert res.reply_text == "¡Hola! Bienvenido. Tenemos disponibles al Dr. Juan Pérez en Cardiología."
    assert mock_client.aio.models.generate_content.called

    # Verificar que el system_instruction inyectado en el config incluye al médico y la directriz
    called_config = mock_client.aio.models.generate_content.call_args_list[0].kwargs.get("config")
    system_instruction = getattr(called_config, "system_instruction", "") or ""

    assert "Dr. Juan Pérez" in system_instruction
    assert "Cardiología" in system_instruction
    assert "CATÁLOGO DE MÉDICOS Y ESPECIALIDADES" in system_instruction
    assert "salude" in system_instruction.lower() or "bienvenida" in system_instruction.lower()



