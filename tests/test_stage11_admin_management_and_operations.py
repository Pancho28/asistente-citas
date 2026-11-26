import uuid
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from httpx import ASGITransport

from src.agent.mcp_tools.admin import (
    consultar_metricas_plataforma,
    enviar_comunicado_medicos,
    gestionar_citas_soporte,
    gestionar_codigos_invitacion,
    gestionar_pacientes,
    listar_y_gestionar_profesionales,
)
from src.agent.mcp_tools.patient import (
    consultar_disponibilidad as tool_consultar_disp,
)
from src.agent.mcp_tools.registry import (
    current_db_session,
    current_redis_client,
    current_user_ctx,
    get_tools_for_user,
)
from src.core.config import get_settings, now_tz
from src.core.exceptions import InvalidInvitationCodeError, PermissionDeniedError
from src.main import app
from src.models.appointment import Cita, Pago
from src.models.schedule import BloqueoAgenda, HorarioBase
from src.models.user import CodigoInvitacion, PerfilProfesional, Usuario
from src.schemas.context import ChannelOrigin, RoleEnum, UserContext
from src.services.onboarding_service import (
    canjear_codigo_invitacion,
    configurar_horario_base_y_perfil,
    generar_codigo_invitacion,
)
from tests.conftest import FakeRedis

settings = get_settings()


def _make_admin_ctx(user_id: uuid.UUID) -> UserContext:
    from src.core.security import ROLE_PERMISSIONS_MAP

    return UserContext(
        user_id=user_id,
        external_chat_id="admin_stage11",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.ADMIN,
        permissions=ROLE_PERMISSIONS_MAP[RoleEnum.ADMIN],
    )


@pytest.mark.asyncio
async def test_admin_doctors_by_name_detail_edit_and_protected_deactivation(async_session):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_doc_1",
        nombre_completo="Super Admin",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    doc_user = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_perez_chat",
        nombre_completo="Dr. Juan Pérez",
        telefono="+584120001122",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    pac_user = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_doc_1",
        nombre_completo="Laura Ríos",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add_all([admin, doc_user, pac_user])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=doc_user.id,
        especialidad="Cardiología",
        tarifa_base_consulta=Decimal("50.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    future_day = now_tz().date() + timedelta(days=5)
    cita_futura = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=pac_user.id,
        fecha_hora_inicio=datetime.combine(future_day, time(10, 0), tzinfo=timezone.utc),
        fecha_hora_fin=datetime.combine(future_day, time(10, 30), tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita_futura)
    await async_session.commit()

    ctx = _make_admin_ctx(admin.id)
    t_u = current_user_ctx.set(ctx)
    t_d = current_db_session.set(async_session)
    try:
        # 1. Ver ficha 360° buscando por nombre (sin UUID)
        det = await listar_y_gestionar_profesionales(accion="DETALLE", profesional_id="Juan Pérez")
        assert det["success"] is True
        assert det["profesional"]["nombre"] == "Dr. Juan Pérez"
        assert det["profesional"]["citas_futuras_activas"] == 1
        assert det["profesional"]["onboarding"]["onboarding_completo"] is False

        # 2. Editar datos, tarifa, horario y zona horaria por nombre
        edited = await listar_y_gestionar_profesionales(
            accion="EDITAR",
            profesional_id="Dr. Juan Pérez",
            nueva_especialidad="Cardiología Intervencionista",
            zona_horaria="America/Bogota",
            tarifa_base_consulta=90.0,
            duracion_cita_minutos=45,
            dias_semana=[1, 2, 3, 4, 5],
            hora_apertura="08:00",
            hora_cierre="14:00",
        )
        assert edited["success"] is True
        assert edited["profesional"]["especialidad"] == "Cardiología Intervencionista"
        assert edited["profesional"]["zona_horaria"] == "America/Bogota"
        assert edited["profesional"]["tarifa_base_consulta"] == 90.0

        # 3. Intentar DAR_DE_BAJA con cita futura sin forzar -> advierte
        warn = await listar_y_gestionar_profesionales(
            accion="DAR_DE_BAJA",
            profesional_id="Juan Pérez",
            forzar=False,
        )
        assert warn["success"] is False
        assert warn["code"] == "HAS_ACTIVE_APPOINTMENTS"
        assert warn["citas_futuras"] == 1

        # 4. DAR_DE_BAJA con forzar=True -> desactiva usuario y perfil
        deactivated = await listar_y_gestionar_profesionales(
            accion="DAR_DE_BAJA",
            profesional_id="Juan Pérez",
            forzar=True,
        )
        assert deactivated["success"] is True
        assert deactivated["usuario_activo"] is False
        assert deactivated["acepta_nuevas_citas"] is False

        # 5. REACTIVAR_CUENTA -> vuelve a activar
        reactivated = await listar_y_gestionar_profesionales(
            accion="REACTIVAR_CUENTA",
            profesional_id="Juan Pérez",
        )
        assert reactivated["success"] is True
        assert reactivated["usuario_activo"] is True
        assert reactivated["acepta_nuevas_citas"] is True
    finally:
        current_user_ctx.reset(t_u)
        current_db_session.reset(t_d)


@pytest.mark.asyncio
async def test_admin_invitation_codes_list_and_revoke(async_session):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_cod_1",
        nombre_completo="Admin Codigos",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    cand_doc = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="cand_doc_1",
        nombre_completo="Dra. Aspirante",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([admin, cand_doc])
    await async_session.commit()

    await generar_codigo_invitacion(
        db=async_session,
        admin_id=admin.id,
        especialidad_asignada="Oftalmología",
        frase_personalizada="MED-OFTA-7777",
    )

    ctx = _make_admin_ctx(admin.id)
    t_u = current_user_ctx.set(ctx)
    t_d = current_db_session.set(async_session)
    try:
        listed = await gestionar_codigos_invitacion(accion="LISTAR", filtro_estado="VIGENTES")
        assert listed["success"] is True
        assert any(c["frase_codigo"] == "MED-OFTA-7777" for c in listed["codigos"])

        revoked = await gestionar_codigos_invitacion(
            accion="REVOCAR", frase_codigo="MED-OFTA-7777"
        )
        assert revoked["success"] is True
        assert revoked["code"] == "INVITATION_REVOKED"

        listed_after = await gestionar_codigos_invitacion(accion="LISTAR", filtro_estado="VIGENTES")
        assert all(c["frase_codigo"] != "MED-OFTA-7777" for c in listed_after["codigos"])
    finally:
        current_user_ctx.reset(t_u)
        current_db_session.reset(t_d)

    with pytest.raises(InvalidInvitationCodeError):
        await canjear_codigo_invitacion(
            db=async_session,
            usuario_id=cand_doc.id,
            frase_codigo="MED-OFTA-7777",
            nombre_profesional="Dra. Aspirante",
        )


@pytest.mark.asyncio
async def test_admin_patient_management_and_webhook_blocks_inactive_user(async_session):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_pac_1",
        nombre_completo="Admin Pacientes",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    doc = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_pac_test",
        nombre_completo="Dr. House",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    pac = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="99887766",
        nombre_completo="Carlos Spam",
        telefono="+584141112233",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add_all([admin, doc, pac])
    await async_session.commit()

    perfil = PerfilProfesional(id=uuid.uuid4(), usuario_id=doc.id, especialidad="Medicina")
    async_session.add(perfil)
    await async_session.commit()

    cita_no_show = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=pac.id,
        fecha_hora_inicio=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 20, 10, 30, tzinfo=timezone.utc),
        estado="NO_ASISTIO",
        monto_acordado=Decimal("40.00"),
        estado_financiero="EXONERADO",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita_no_show)
    await async_session.commit()

    ctx = _make_admin_ctx(admin.id)
    t_u = current_user_ctx.set(ctx)
    t_d = current_db_session.set(async_session)
    try:
        found = await gestionar_pacientes(accion="BUSCAR", identificador_paciente="Carlos")
        assert found["success"] is True
        assert found["pacientes"][0]["inasistencias"] == 1

        edited = await gestionar_pacientes(
            accion="EDITAR",
            identificador_paciente="Carlos Spam",
            nuevo_telefono="+584249990000",
        )
        assert edited["success"] is True
        assert edited["paciente"]["telefono"] == "+584249990000"

        blocked = await gestionar_pacientes(accion="BLOQUEAR", identificador_paciente="99887766")
        assert blocked["success"] is True
        assert blocked["paciente"]["activo"] is False
    finally:
        current_user_ctx.reset(t_u)
        current_db_session.reset(t_d)

    # Verificar cortafuegos en /api/v1/webhook (0 tokens, rechazo inmediato)
    payload_tg = {
        "update_id": 1001,
        "message": {
            "message_id": 10,
            "from": {"id": 99887766, "first_name": "Carlos"},
            "chat": {"id": 99887766, "type": "private"},
            "text": "Hola quiero una cita",
        },
    }
    headers = {
        "X-Internal-Api-Key": settings.INTERNAL_GATEWAY_SECRET,
        "X-Telegram-Bot-Api-Secret-Token": settings.TELEGRAM_WEBHOOK_SECRET,
    }
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        with patch("src.agent.orchestrator.process_message_with_ai") as mock_ai:
            resp = await client.post("/api/v1/webhook", json=payload_tg, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "blocked_inactive_user"
            assert "suspendida" in data["reply_text"].lower()
            mock_ai.assert_not_called()

        # Reactivar paciente y comprobar que ya pasa al flujo normal
        t_u = current_user_ctx.set(ctx)
        t_d = current_db_session.set(async_session)
        try:
            reactivated = await gestionar_pacientes(
                accion="REACTIVAR", identificador_paciente="Carlos Spam"
            )
            assert reactivated["success"] is True
            assert reactivated["paciente"]["activo"] is True
        finally:
            current_user_ctx.reset(t_u)
            current_db_session.reset(t_d)

        resp2 = await client.post("/api/v1/webhook", json=payload_tg, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_admin_appointment_support_reschedule_and_cancel_doctor_day(async_session):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_sop_1",
        nombre_completo="Admin Soporte",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    doc = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_sop_chat",
        nombre_completo="Dra. Sofía Martínez",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    pac1 = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_sop_1",
        nombre_completo="Pedro Gómez",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    pac2 = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_sop_2",
        nombre_completo="Ana Lucía",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([admin, doc, pac1, pac2])
    await async_session.commit()

    perfil = PerfilProfesional(id=uuid.uuid4(), usuario_id=doc.id, especialidad="Dermatología")
    async_session.add(perfil)
    await async_session.commit()

    day_target = date(2026, 12, 10)
    next_day = date(2026, 12, 11)
    c1 = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=pac1.id,
        fecha_hora_inicio=datetime.combine(day_target, time(9, 0), tzinfo=timezone.utc),
        fecha_hora_fin=datetime.combine(day_target, time(9, 30), tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("60.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    c2 = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=pac2.id,
        fecha_hora_inicio=datetime.combine(day_target, time(10, 0), tzinfo=timezone.utc),
        fecha_hora_fin=datetime.combine(day_target, time(10, 30), tzinfo=timezone.utc),
        estado="CONFIRMADA_PACIENTE",
        monto_acordado=Decimal("60.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add_all([c1, c2])
    await async_session.commit()

    ctx = _make_admin_ctx(admin.id)
    fake_redis = FakeRedis()
    t_u = current_user_ctx.set(ctx)
    t_d = current_db_session.set(async_session)
    t_r = current_redis_client.set(fake_redis)
    notified: list[tuple[str, str]] = []

    async def fake_send(self, recipient: str | None = None, text: str = "", **kwargs):
        notified.append((recipient or "", text))
        return True

    try:
        with patch("src.adapters.telegram.TelegramAdapter.send_message", new=fake_send):
            reprog = await gestionar_citas_soporte(
                accion="REPROGRAMAR_CITA",
                cita_id=str(c1.id),
                nueva_fecha=next_day.isoformat(),
                nueva_hora="11:00",
                motivo="Cambio de consultorio",
            )
            assert reprog["success"] is True
            assert reprog["nueva_fecha"] == "2026-12-11"
            assert reprog["nueva_hora"] == "11:00"

            cancel_day = await gestionar_citas_soporte(
                accion="CANCELAR_JORNADA_MEDICO",
                identificador_medico="Sofía Martínez",
                fecha_inicio=day_target.isoformat(),
                motivo="Urgencia médica familiar",
            )
            assert cancel_day["success"] is True
            assert cancel_day["citas_canceladas"] == 1
            assert "Ana Lucía" in cancel_day["pacientes_notificados"]
    finally:
        current_user_ctx.reset(t_u)
        current_db_session.reset(t_d)
        current_redis_client.reset(t_r)

    await async_session.refresh(c2)
    assert c2.estado == "CANCELADA"
    assert c2.estado_financiero == "EXONERADO"
    assert any(r == "pac_sop_2" and "Urgencia" in msg for r, msg in notified)


@pytest.mark.asyncio
async def test_admin_platform_metrics_and_doctor_broadcast_with_rbac(async_session):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_met_1",
        nombre_completo="Admin Metricas",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    doc_ready = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_ready_chat",
        nombre_completo="Dr. Completo",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    doc_pending = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_pend_chat",
        nombre_completo="Dra. Pendiente",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    pac = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_met_1",
        nombre_completo="Paciente Metricas",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add_all([admin, doc_ready, doc_pending, pac])
    await async_session.commit()

    p_ready = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=doc_ready.id,
        especialidad="Cardiología",
        tarifa_base_consulta=Decimal("100.00"),
        onboarding_campos_configurados="horario_consulta,duracion_cita,tarifa_consulta,notificacion_diaria",
    )
    p_pending = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=doc_pending.id,
        especialidad="Pediatría",
        tarifa_base_consulta=Decimal("50.00"),
        onboarding_campos_configurados="",
    )
    async_session.add_all([p_ready, p_pending])
    await async_session.commit()

    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=doc_ready.id,
        dias_semana=[1, 2, 3, 4, 5],
        hora_apertura=time(8, 0),
        hora_cierre=time(16, 0),
    )

    hoy = now_tz().date()
    cita_comp = Cita(
        id=uuid.uuid4(),
        profesional_id=p_ready.id,
        paciente_id=pac.id,
        fecha_hora_inicio=datetime.combine(hoy, time(9, 0), tzinfo=timezone.utc),
        fecha_hora_fin=datetime.combine(hoy, time(9, 30), tzinfo=timezone.utc),
        estado="COMPLETADA",
        monto_acordado=Decimal("100.00"),
        estado_financiero="PAGADO",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita_comp)
    await async_session.commit()

    pago = Pago(
        id=uuid.uuid4(),
        cita_id=cita_comp.id,
        monto_pagado=Decimal("100.00"),
        moneda="USD",
        tipo_pago="PAGO_COMPLETO",
        metodo_pago="EFECTIVO",
        estado_transaccion="COMPLETADO",
        fecha_pago=datetime.combine(hoy, time(9, 30), tzinfo=timezone.utc),
    )
    async_session.add(pago)
    await async_session.commit()

    ctx = _make_admin_ctx(admin.id)
    t_u = current_user_ctx.set(ctx)
    t_d = current_db_session.set(async_session)
    broadcast_sent: list[tuple[str, str]] = []

    async def fake_send(self, recipient: str | None = None, text: str = "", **kwargs):
        broadcast_sent.append((recipient or "", text))
        return True

    try:
        metrics = await consultar_metricas_plataforma(periodo="HOY")
        assert metrics["success"] is True
        assert metrics["medicos"]["total"] == 2
        assert len(metrics["medicos"]["con_onboarding_pendiente"]) == 1
        assert metrics["medicos"]["con_onboarding_pendiente"][0]["nombre"] == "Dra. Pendiente"
        assert metrics["citas_en_periodo"]["total"] == 1
        assert metrics["citas_en_periodo"]["por_estado"]["COMPLETADA"] == 1
        assert metrics["finanzas_en_periodo"]["total_cobrado"] == 100.0

        with patch("src.adapters.telegram.TelegramAdapter.send_message", new=fake_send):
            aviso = await enviar_comunicado_medicos(
                mensaje="Por favor configura tu horario para empezar a recibir citas.",
                destinatario="ONBOARDING_PENDIENTE",
            )
        assert aviso["success"] is True
        assert aviso["enviados"] == 1
        assert len(broadcast_sent) == 1
        assert broadcast_sent[0][0] == "doc_pend_chat"
    finally:
        current_user_ctx.reset(t_u)
        current_db_session.reset(t_d)

    # Barrera 1 y Barrera 2 RBAC: PACIENTE / MEDICO bloqueados
    pac_ctx = UserContext(
        user_id=pac.id,
        external_chat_id="pac_met_1",
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar"]),
    )
    pac_tools = {fn.__name__ for fn in get_tools_for_user(pac_ctx)}
    assert "gestionar_pacientes" not in pac_tools
    assert "consultar_metricas_plataforma" not in pac_tools

    t_u = current_user_ctx.set(pac_ctx)
    t_d = current_db_session.set(async_session)
    try:
        with pytest.raises(PermissionDeniedError):
            await gestionar_pacientes(accion="BUSCAR")
    finally:
        current_user_ctx.reset(t_u)
        current_db_session.reset(t_d)
