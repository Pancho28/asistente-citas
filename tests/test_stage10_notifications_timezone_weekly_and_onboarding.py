import uuid
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import pytest

from src.core.config import format_wall_time, normalize_timezone_name, now_tz, now_wall_utc
from src.models.appointment import Cita
from src.models.schedule import HorarioBase
from src.models.user import PerfilProfesional, Usuario
from src.agent.mcp_tools.doctor import (
    configurar_horario_base_y_perfil as tool_configurar_perfil,
    ver_agenda_profesional as tool_ver_agenda,
)
from src.agent.mcp_tools.patient import (
    canjear_codigo_invitacion_medico as tool_canjear_codigo,
)
from src.agent.mcp_tools.registry import current_db_session, current_user_ctx
from src.agent.prompts import build_system_prompt
from src.schemas.context import ChannelOrigin, RoleEnum, UserContext
from src.services.onboarding_service import (
    configurar_horario_base_y_perfil,
    generar_codigo_invitacion,
    obtener_estado_onboarding_medico,
)
from src.workers.tasks import _do_heartbeat


@pytest.mark.asyncio
async def test_heartbeat_notification_omits_zero_total_when_no_appointments_and_shows_total_when_present(
    async_session,
):
    medico_sin_citas = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_empty_summary",
        nombre_completo="Dra. Camila Sin Citas",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        zona_horaria="America/Caracas",
    )
    medico_con_citas = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_busy_summary",
        nombre_completo="Dr. Mateo Con Citas",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        zona_horaria="America/Caracas",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_summary_1",
        nombre_completo="Sofía Navarro",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico_sin_citas, medico_con_citas, paciente])
    await async_session.commit()

    perfil_sin = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico_sin_citas.id,
        especialidad="Dermatología",
        hora_resumen_diario=time(8, 0),
        alcance_resumen_diario="AGENDA_HOY",
    )
    perfil_con = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico_con_citas.id,
        especialidad="Cardiología",
        hora_resumen_diario=time(8, 0),
        alcance_resumen_diario="AGENDA_HOY",
    )
    async_session.add_all([perfil_sin, perfil_con])
    await async_session.commit()

    fixed_local_now = datetime(2026, 11, 26, 8, 5, tzinfo=now_tz("America/Caracas").tzinfo)
    cita = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil_con.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime(2026, 11, 26, 10, 30, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 26, 11, 0, tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("60.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita)
    await async_session.commit()

    sent_messages: list[tuple[str, str]] = []

    async def fake_send(self, recipient: str | None = None, text: str = "", **kwargs):
        sent_messages.append((recipient or "", text))
        return True

    with patch("src.workers.tasks.now_tz", return_value=fixed_local_now), patch(
        "src.workers.tasks.now_wall_utc", return_value=fixed_local_now.replace(tzinfo=timezone.utc)
    ), patch("src.adapters.telegram.TelegramAdapter.send_message", new=fake_send):
        result = await _do_heartbeat(async_session)

    assert result["resumenes_enviados"] == 2
    by_recipient = dict(sent_messages)

    msg_sin = by_recipient["doc_empty_summary"]
    assert "No tienes citas programadas para esta jornada." in msg_sin
    assert "Total de pacientes programados" not in msg_sin

    msg_con = by_recipient["doc_busy_summary"]
    assert "Total de pacientes programados: 1" in msg_con
    assert "10:30" in msg_con
    assert "Sofía Navarro" in msg_con


@pytest.mark.asyncio
async def test_doctor_custom_timezone_triggers_heartbeat_at_doctor_local_hour_and_preserves_wall_time(
    async_session,
):
    assert normalize_timezone_name("madrid") == "Europe/Madrid"
    assert format_wall_time(datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)) == "09:00"

    medico_madrid = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_madrid",
        nombre_completo="Dr. Álvaro Madrid",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        zona_horaria="Europe/Madrid",
    )
    medico_caracas = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_caracas",
        nombre_completo="Dr. Luis Caracas",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        zona_horaria="America/Caracas",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_tz_1",
        nombre_completo="Elena Torres",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico_madrid, medico_caracas, paciente])
    await async_session.commit()

    perfil_madrid = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico_madrid.id,
        especialidad="Neurología",
        hora_resumen_diario=time(13, 0),  # En UTC+1 cuando en Caracas son las 08:00
        alcance_resumen_diario="AGENDA_HOY",
    )
    perfil_caracas = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico_caracas.id,
        especialidad="Medicina General",
        hora_resumen_diario=time(13, 0),  # En Caracas aún son las 08:00, NO debe disparar
        alcance_resumen_diario="AGENDA_HOY",
    )
    async_session.add_all([perfil_madrid, perfil_caracas])
    await async_session.commit()

    # Cita nominal a las 15:30 del 2026-11-26 en hora de Madrid
    cita = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil_madrid.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime(2026, 11, 26, 15, 30, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("70.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita)
    await async_session.commit()

    # Simulamos instante exacto 2026-11-26 12:05 UTC -> 13:05 Europe/Madrid, 08:05 America/Caracas
    def fake_now_tz(tz_name: str | None = None) -> datetime:
        from src.core.config import resolve_zoneinfo

        base_utc = datetime(2026, 11, 26, 12, 5, tzinfo=timezone.utc)
        return base_utc.astimezone(resolve_zoneinfo(tz_name))

    sent_recipients: list[tuple[str, str]] = []

    async def fake_send(self, recipient: str | None = None, text: str = "", **kwargs):
        sent_recipients.append((recipient or "", text))
        return True

    with patch("src.workers.tasks.now_tz", side_effect=fake_now_tz), patch(
        "src.workers.tasks.now_wall_utc",
        side_effect=lambda tz=None: fake_now_tz(tz).replace(tzinfo=timezone.utc),
    ), patch("src.adapters.telegram.TelegramAdapter.send_message", new=fake_send):
        result = await _do_heartbeat(async_session)

    assert result["resumenes_enviados"] == 1
    assert len(sent_recipients) == 1
    assert sent_recipients[0][0] == "doc_madrid"
    assert "15:30" in sent_recipients[0][1]
    assert "Elena Torres" in sent_recipients[0][1]


@pytest.mark.asyncio
async def test_weekly_agenda_filters_out_non_working_weekend_days(async_session):
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_weekly",
        nombre_completo="Dr. Semana Laboral",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    async_session.add(medico)
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Pediatría",
    )
    async_session.add(perfil)
    await async_session.commit()

    # Lunes(1) a Viernes(5) únicamente
    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=medico.id,
        dias_semana=[1, 2, 3, 4, 5],
        hora_apertura=time(9, 0),
        hora_cierre=time(17, 0),
    )

    ctx = UserContext(
        user_id=medico.id,
        external_chat_id="doc_weekly",
        role=RoleEnum.MEDICO,
        channel=ChannelOrigin.TELEGRAM,
        permissions=frozenset(["agenda:ver_pacientes_dia"]),
    )
    t_user = current_user_ctx.set(ctx)
    t_db = current_db_session.set(async_session)
    try:
        # Lunes 2026-11-30 a Domingo 2026-12-06
        res = await tool_ver_agenda(fecha_inicio="2026-11-30", fecha_fin="2026-12-06")
    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)

    assert res["success"] is True
    dias_nombres = [d["dia_semana"] for d in res["dias_habilitados_en_rango"]]
    assert dias_nombres == ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    assert "Sábado" not in dias_nombres
    assert "Domingo" not in dias_nombres


@pytest.mark.asyncio
async def test_doctor_guided_onboarding_tracks_each_pending_setting_and_injects_friendly_prompt(
    async_session,
):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_onb",
        nombre_completo="Admin Onb",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    nuevo_medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="new_doc_onb",
        nombre_completo="Dr. Novato",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([admin, nuevo_medico])
    await async_session.commit()

    await generar_codigo_invitacion(
        db=async_session,
        admin_id=admin.id,
        especialidad_asignada="Ginecología",
        frase_personalizada="MED-GINE-2026",
    )

    ctx_paciente = UserContext(
        user_id=nuevo_medico.id,
        external_chat_id="new_doc_onb",
        role=RoleEnum.PACIENTE,
        channel=ChannelOrigin.TELEGRAM,
        permissions=frozenset(["onboarding:canjear_codigo"]),
    )
    t_user = current_user_ctx.set(ctx_paciente)
    t_db = current_db_session.set(async_session)
    try:
        redeem_res = await tool_canjear_codigo(
            frase_codigo="MED-GINE-2026", nombre_profesional="Dra. Lucía Méndez"
        )
    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)

    assert redeem_res["success"] is True
    estado_inicial = redeem_res["onboarding_estado"]
    assert estado_inicial["onboarding_completo"] is False
    assert [p["campo"] for p in estado_inicial["pendientes"]] == [
        "horario_consulta",
        "duracion_cita",
        "tarifa_consulta",
        "notificacion_diaria",
    ]

    # Verificar que el prompt de rol MEDICO incluya recordatorios amables de las 4 configuraciones pendientes
    ctx_medico = UserContext(
        user_id=nuevo_medico.id,
        external_chat_id="new_doc_onb",
        role=RoleEnum.MEDICO,
        channel=ChannelOrigin.TELEGRAM,
        permissions=frozenset(["agenda:configurar_perfil"]),
    )
    prompt = build_system_prompt(ctx_medico, now_tz(), onboarding_medico=estado_inicial)
    assert "ONBOARDING" in prompt
    assert "Días y horario de consulta" in prompt
    assert "Duración de cada cita" in prompt
    assert "Tarifa base de consulta" in prompt
    assert "Notificación diaria de pacientes" in prompt

    # Configurar paso 1: horario + duración + zona horaria via tool MCP
    t_user = current_user_ctx.set(ctx_medico)
    t_db = current_db_session.set(async_session)
    try:
        step1_res = await tool_configurar_perfil(
            dias_semana=[1, 3, 5],
            hora_apertura="08:00",
            hora_cierre="13:00",
            duracion_cita_minutos=45,
            zona_horaria="America/Bogota",
        )
        assert step1_res["success"] is True
        assert step1_res["zona_horaria"] == "America/Bogota"
        assert [p["campo"] for p in step1_res["onboarding_estado"]["pendientes"]] == [
            "tarifa_consulta",
            "notificacion_diaria",
        ]

        # Completar paso 2: tarifa + notificación diaria
        step2_res = await tool_configurar_perfil(
            tarifa_base_consulta=80.0,
            hora_resumen_diario="19:00",
            alcance_resumen_diario="AGENDA_MANANA",
        )
    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)

    assert step2_res["success"] is True
    assert step2_res["onboarding_estado"]["onboarding_completo"] is True
    assert step2_res["onboarding_estado"]["pendientes"] == []
