import inspect
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.schemas.context import UserContext, RoleEnum, ChannelOrigin
from src.core.exceptions import PermissionDeniedError
from src.core.security import ROLE_PERMISSIONS_MAP


def create_user_context(role: RoleEnum, is_real_admin: bool = False) -> UserContext:
    perms = set(ROLE_PERMISSIONS_MAP.get(role, set()))
    if is_real_admin:
        perms.add("admin:cambiar_vista")
    return UserContext(
        user_id=uuid.uuid4(),
        external_chat_id="test_chat",
        channel=ChannelOrigin.TELEGRAM,
        role=role,
        permissions=frozenset(perms),
    )


# =========================================================================
# 1. BARRERA 1: Filtrado de herramientas por rol antes de enviar a Gemini
# =========================================================================

def test_barrier_1_filters_tools_strictly_by_role():
    from src.agent.mcp_tools.registry import get_tools_for_user
    import src.agent.mcp_tools.patient as patient_tools
    import src.agent.mcp_tools.doctor as doctor_tools
    import src.agent.mcp_tools.admin as admin_tools

    # Paciente estándar
    ctx_paciente = create_user_context(RoleEnum.PACIENTE, is_real_admin=False)
    tools_paciente = get_tools_for_user(ctx_paciente)
    tool_names_paciente = [t.__name__ for t in tools_paciente]

    # Debe contener las 5 tools de paciente
    assert "consultar_disponibilidad" in tool_names_paciente
    assert "agendar_cita_paciente" in tool_names_paciente
    assert "ver_mis_citas_activas" in tool_names_paciente
    assert "cancelar_o_reagendar_cita_propia" in tool_names_paciente
    assert "canjear_codigo_invitacion_medico" in tool_names_paciente

    # JAMÁS debe contener tools de médico ni admin
    assert "ver_agenda_profesional" not in tool_names_paciente
    assert "bloquear_horario_agenda" not in tool_names_paciente
    assert "registrar_pago_o_descuento" not in tool_names_paciente
    assert "generar_codigo_invitacion_medico" not in tool_names_paciente
    assert "cambiar_modo_vista_sesion" not in tool_names_paciente

    # Médico estándar
    ctx_medico = create_user_context(RoleEnum.MEDICO, is_real_admin=False)
    tools_medico = get_tools_for_user(ctx_medico)
    tool_names_medico = [t.__name__ for t in tools_medico]

    assert "ver_agenda_profesional" in tool_names_medico
    assert "bloquear_horario_agenda" in tool_names_medico
    assert "configurar_horario_base_y_perfil" in tool_names_medico
    assert "agendar_o_mover_cita_manual" in tool_names_medico
    assert "registrar_pago_o_descuento" in tool_names_medico
    assert "consultar_resumen_ganancias" in tool_names_medico
    assert "consultar_disponibilidad" not in tool_names_medico
    assert "generar_codigo_invitacion_medico" not in tool_names_medico

    # Administrador
    ctx_admin = create_user_context(RoleEnum.ADMIN, is_real_admin=True)
    tools_admin = get_tools_for_user(ctx_admin)
    tool_names_admin = [t.__name__ for t in tools_admin]

    assert "generar_codigo_invitacion_medico" in tool_names_admin
    assert "listar_y_gestionar_profesionales" in tool_names_admin
    assert "cambiar_modo_vista_sesion" in tool_names_admin


def test_barrier_1_includes_cambiar_modo_vista_if_admin_viewing_as_patient():
    """Si un ADMIN está usando /modo_paciente, debe tener las tools de paciente + cambiar_modo_vista_sesion."""
    from src.agent.mcp_tools.registry import get_tools_for_user

    ctx_admin_as_paciente = create_user_context(RoleEnum.PACIENTE, is_real_admin=True)
    tools = get_tools_for_user(ctx_admin_as_paciente)
    tool_names = [t.__name__ for t in tools]

    assert "consultar_disponibilidad" in tool_names
    assert "cambiar_modo_vista_sesion" in tool_names


# =========================================================================
# 2. BARRERA 2: Decorador @require_permission en tiempo de ejecución
# =========================================================================

@pytest.mark.asyncio
async def test_barrier_2_decorator_blocks_unauthorized_execution():
    from src.agent.mcp_tools.registry import current_user_ctx
    from src.agent.mcp_tools.doctor import registrar_pago_o_descuento
    from src.agent.mcp_tools.admin import generar_codigo_invitacion_medico

    # Contexto de un paciente intentando ejecutar tool de médico
    ctx_paciente = create_user_context(RoleEnum.PACIENTE, is_real_admin=False)
    token = current_user_ctx.set(ctx_paciente)
    try:
        with pytest.raises(PermissionDeniedError):
            await registrar_pago_o_descuento(cita_id=str(uuid.uuid4()), monto_pagado=50.0)

        with pytest.raises(PermissionDeniedError):
            await generar_codigo_invitacion_medico(especialidad_asignada="Cardiología")
    finally:
        current_user_ctx.reset(token)


# =========================================================================
# 3. ANTI-IDOR & Firmas Puras para el LLM
# =========================================================================

def test_anti_idor_tool_signatures_do_not_expose_identity_parameters():
    """
    Verifica que NINGUNA función en mcp_tools/ reciba paciente_id, medico_id, admin_id,
    user_id, db ni redis como parámetro expuesto en su firma (el LLM no debe verlos).
    """
    import src.agent.mcp_tools.patient as patient_tools
    import src.agent.mcp_tools.doctor as doctor_tools
    import src.agent.mcp_tools.admin as admin_tools

    all_modules = [patient_tools, doctor_tools, admin_tools]
    prohibited_params = {
        "paciente_id", "medico_id", "admin_id", "user_id", "usuario_id",
        "profesional_usuario_id", "creado_por_admin_id", "db", "redis"
    }

    for mod in all_modules:
        for name, func in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("_") or func.__module__ != mod.__name__:
                continue
            sig = inspect.signature(func)
            for param_name in sig.parameters.keys():
                assert param_name not in prohibited_params, (
                    f"Violación Anti-IDOR: La tool '{name}' en '{mod.__name__}' "
                    f"expone el parámetro prohibido '{param_name}' al esquema de Gemini."
                )


def test_agendar_cita_paciente_docstring_strictly_forbids_asking_motivo():
    """
    Verifica que el docstring de agendar_cita_paciente instruya a Gemini a NO pedir
    el motivo de consulta al paciente.
    """
    import src.agent.mcp_tools.patient as patient_tools

    doc = patient_tools.agendar_cita_paciente.__doc__ or ""
    assert "motivo de consulta" in doc.lower()
    assert ("no" in doc.lower() or "nunca" in doc.lower())


# =========================================================================
# 4. MEMORIA EN REDIS Y RESOLUCIÓN INTELIGENTE DEL MÉDICO (BUG 8)
# =========================================================================

@pytest.mark.asyncio
async def test_booking_remembers_last_consulted_doctor_from_redis(async_session: AsyncSession):
    from datetime import date, time, timedelta
    from decimal import Decimal
    from sqlalchemy import select
    from src.models.user import Usuario, PerfilProfesional
    from src.models.schedule import HorarioBase
    from src.models.appointment import Cita
    from src.core.config import now_tz
    from tests.conftest import FakeRedis
    from src.agent.mcp_tools.registry import current_user_ctx, current_db_session, current_redis_client
    from src.agent.mcp_tools.patient import consultar_disponibilidad, agendar_cita_paciente

    fake_redis = FakeRedis()

    # Médico A: Medicina General
    doc_a = Usuario(canal_origen="TELEGRAM", external_chat_id="doc_a", nombre_completo="Dr. Carlos Mendoza", rol_sistema="MEDICO", modo_vista_actual="MEDICO", activo=True)
    async_session.add(doc_a)
    await async_session.commit()
    await async_session.refresh(doc_a)
    perfil_a = PerfilProfesional(id=uuid.uuid4(), usuario_id=doc_a.id, especialidad="Medicina General", duracion_cita_minutos=30, tarifa_base_consulta=Decimal("30.00"), moneda="USD", min_horas_anticipacion_cancelar=2, acepta_nuevas_citas=True)
    async_session.add(perfil_a)

    # Médico B: Cardiología (Dr. Juan Pérez)
    doc_b = Usuario(canal_origen="TELEGRAM", external_chat_id="doc_b", nombre_completo="Dr. Juan Pérez", rol_sistema="MEDICO", modo_vista_actual="MEDICO", activo=True)
    async_session.add(doc_b)
    await async_session.commit()
    await async_session.refresh(doc_b)
    perfil_b = PerfilProfesional(id=uuid.uuid4(), usuario_id=doc_b.id, especialidad="Cardiología", duracion_cita_minutos=30, tarifa_base_consulta=Decimal("50.00"), moneda="USD", min_horas_anticipacion_cancelar=2, acepta_nuevas_citas=True)
    async_session.add(perfil_b)

    # Horarios base para ambos
    for d in range(1, 6):
        async_session.add(HorarioBase(profesional_id=perfil_a.id, dia_semana=d, hora_inicio=time(9, 0), hora_fin=time(17, 0), activo=True))
        async_session.add(HorarioBase(profesional_id=perfil_b.id, dia_semana=d, hora_inicio=time(9, 0), hora_fin=time(17, 0), activo=True))
    await async_session.commit()

    # Paciente
    paciente = Usuario(canal_origen="TELEGRAM", external_chat_id="pac_maria", nombre_completo="María Paciente", rol_sistema="PACIENTE", modo_vista_actual="PACIENTE", activo=True)
    async_session.add(paciente)
    await async_session.commit()
    await async_session.refresh(paciente)

    ctx_paciente = UserContext(
        user_id=paciente.id,
        external_chat_id=paciente.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=frozenset(["disponibilidad:consultar", "citas:agendar_propia"]),
    )

    t_user = current_user_ctx.set(ctx_paciente)
    t_db = current_db_session.set(async_session)
    t_redis = current_redis_client.set(fake_redis)

    try:
        # Próximo lunes
        today = now_tz().date()
        days_ahead = (0 - today.weekday() + 7) % 7 or 7
        target_date = today + timedelta(days=days_ahead)
        target_date_str = target_date.strftime("%Y-%m-%d")

        # 1. Paciente consulta Cardiología
        disp_res = await consultar_disponibilidad(fecha=target_date_str, especialidad="Cardiología")
        assert disp_res["success"] is True
        assert disp_res["profesional_id"] == str(perfil_b.id)

        # Verificar que se guardó chat:last_prof:{user_id} en Redis
        last_prof_in_redis = await fake_redis.get(f"chat:last_prof:{paciente.id}")
        assert last_prof_in_redis == str(perfil_b.id)

        # 2. Paciente agenda SIN pasar profesional_id (profesional_id=None)
        book_res = await agendar_cita_paciente(
            fecha=target_date_str,
            hora="10:00",
            nombre_completo_paciente="María Alejandra Gómez",
            profesional_id=None,
        )
        assert book_res["success"] is True
        cita_id = uuid.UUID(book_res["cita_id"])

        # 3. Verificar que en la base de datos la cita está asignada a perfil_b (Dr. Pérez), NO a perfil_a (Dr. Mendoza)
        cita_db = (await async_session.execute(select(Cita).where(Cita.id == cita_id))).scalar_one()
        assert cita_db.profesional_id == perfil_b.id
        assert cita_db.profesional_id != perfil_a.id
    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)
        current_redis_client.reset(t_redis)


@pytest.mark.asyncio
async def test_doctor_block_schedule_force_with_appointments(async_session: AsyncSession):
    from datetime import datetime, timezone
    from decimal import Decimal
    from src.models.user import Usuario, PerfilProfesional
    from src.models.appointment import Cita
    from src.agent.mcp_tools.registry import current_user_ctx, current_db_session
    from src.agent.mcp_tools.doctor import (
        bloquear_horario_agenda,
        agendar_o_mover_cita_manual,
        registrar_pago_o_descuento,
    )

    doc = Usuario(canal_origen="TELEGRAM", external_chat_id="doc_mcp_7", nombre_completo="Dr. Chase MCP", rol_sistema="MEDICO", modo_vista_actual="MEDICO", activo=True)
    pac = Usuario(canal_origen="TELEGRAM", external_chat_id="pac_mcp_7", nombre_completo="Laura Gómez", rol_sistema="PACIENTE", modo_vista_actual="PACIENTE", activo=True)
    async_session.add_all([doc, pac])
    await async_session.commit()
    await async_session.refresh(doc)
    await async_session.refresh(pac)

    perfil = PerfilProfesional(id=uuid.uuid4(), usuario_id=doc.id, especialidad="Cardiología", duracion_cita_minutos=30, tarifa_base_consulta=Decimal("50.00"))
    async_session.add(perfil)
    await async_session.commit()

    cita = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=pac.id,
        fecha_hora_inicio=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 25, 10, 30, tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita)
    await async_session.commit()

    ctx_doc = create_user_context(RoleEnum.MEDICO)
    ctx_doc = UserContext(
        user_id=doc.id,
        external_chat_id=doc.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.MEDICO,
        permissions=ctx_doc.permissions,
    )

    t_user = current_user_ctx.set(ctx_doc)
    t_db = current_db_session.set(async_session)
    try:
        # 1. Sin forzar -> advierte citas afectadas
        res_warn = await bloquear_horario_agenda(
            fecha_inicio="2026-11-25",
            hora_inicio="09:00",
            fecha_fin="2026-11-25",
            hora_fin="12:00",
            motivo="Emergencia",
            forzar_con_citas=False,
        )
        assert res_warn["success"] is False
        assert res_warn["code"] == "AFFECTED_APPOINTMENTS"

        # 2. Con forzar_con_citas=True -> permite bloquear
        res_force = await bloquear_horario_agenda(
            fecha_inicio="2026-11-25",
            hora_inicio="09:00",
            fecha_fin="2026-11-25",
            hora_fin="12:00",
            motivo="Emergencia",
            forzar_con_citas=True,
        )
        assert res_force["success"] is True
        assert res_force["code"] == "SLOT_BLOCKED"

        # 3. Registrar pago por nombre de paciente ("Laura") en tool MCP
        res_pay = await registrar_pago_o_descuento(
            cita_id="Laura",
            monto_pagado=50.0,
            metodo_pago="EFECTIVO",
        )
        assert res_pay["success"] is True
        assert res_pay["nuevo_estado_financiero"] == "PAGADO"

        # 4. Colisión en tool MCP agendar_o_mover_cita_manual -> code SLOT_TAKEN
        res_dup = await agendar_o_mover_cita_manual(
            accion="CREAR",
            fecha_destino="2026-11-25",
            hora_destino="10:00",
            nombre_paciente="Otro",
        )
        assert res_dup["success"] is False
        assert res_dup["code"] == "SLOT_TAKEN"

        # 5. Cancelar por tool MCP usando nombre_paciente sin fecha/hora ni UUID
        res_cancel = await agendar_o_mover_cita_manual(
            accion="CANCELAR",
            nombre_paciente="Laura",
        )
        assert res_cancel["success"] is True
        assert res_cancel["estado"] == "CANCELADA"
    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)


@pytest.mark.asyncio
async def test_mcp_patient_cancel_without_uuid(async_session: AsyncSession):
    from datetime import datetime, timezone, timedelta
    from decimal import Decimal
    from src.models.user import Usuario, PerfilProfesional
    from src.models.schedule import HorarioBase
    from src.models.appointment import Cita
    from src.core.config import now_tz
    from tests.conftest import FakeRedis
    from src.agent.mcp_tools.registry import current_user_ctx, current_db_session, current_redis_client
    from src.agent.mcp_tools.patient import cancelar_o_reagendar_cita_propia

    fake_redis = FakeRedis()
    doc = Usuario(canal_origen="TELEGRAM", external_chat_id="doc_pac_mcp", nombre_completo="Dr. House", rol_sistema="MEDICO", modo_vista_actual="MEDICO", activo=True)
    pac = Usuario(canal_origen="TELEGRAM", external_chat_id="pac_mcp_8", nombre_completo="Pedro Pérez", rol_sistema="PACIENTE", modo_vista_actual="PACIENTE", activo=True)
    async_session.add_all([doc, pac])
    await async_session.commit()
    await async_session.refresh(doc)
    await async_session.refresh(pac)

    perfil = PerfilProfesional(id=uuid.uuid4(), usuario_id=doc.id, especialidad="Medicina", duracion_cita_minutos=30, tarifa_base_consulta=Decimal("40.00"), min_horas_anticipacion_cancelar=2)
    async_session.add(perfil)
    await async_session.commit()

    future_dt = now_tz() + timedelta(days=3)
    future_dt = future_dt.replace(hour=10, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    cita = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=pac.id,
        fecha_hora_inicio=future_dt,
        fecha_hora_fin=future_dt + timedelta(minutes=30),
        estado="CONFIRMADA",
        monto_acordado=Decimal("40.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita)
    await async_session.commit()

    ctx_pac = create_user_context(RoleEnum.PACIENTE)
    ctx_pac = UserContext(
        user_id=pac.id,
        external_chat_id=pac.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.PACIENTE,
        permissions=ctx_pac.permissions,
    )
    t_user = current_user_ctx.set(ctx_pac)
    t_db = current_db_session.set(async_session)
    t_redis = current_redis_client.set(fake_redis)
    try:
        res = await cancelar_o_reagendar_cita_propia(accion="CANCELAR", cita_id=None)
        assert res["success"] is True
        assert res["cita_id"] == str(cita.id)
        assert res["estado"] == "CANCELADA"
    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)
        current_redis_client.reset(t_redis)


@pytest.mark.asyncio
async def test_doctor_mcp_unblock_and_close_days(async_session: AsyncSession):
    from datetime import time
    from decimal import Decimal
    from sqlalchemy import select
    from src.models.user import Usuario, PerfilProfesional
    from src.models.schedule import HorarioBase
    from src.agent.mcp_tools.registry import current_user_ctx, current_db_session
    from src.agent.mcp_tools.doctor import (
        bloquear_horario_agenda,
        configurar_horario_base_y_perfil,
    )

    doc = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="doc_mcp_stage8",
        nombre_completo="Dr. Foreman Stage 8",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(doc)
    await async_session.commit()
    await async_session.refresh(doc)

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=doc.id,
        especialidad="Neurología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("70.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    ctx_doc = create_user_context(RoleEnum.MEDICO)
    ctx_doc = UserContext(
        user_id=doc.id,
        external_chat_id=doc.external_chat_id,
        channel=ChannelOrigin.TELEGRAM,
        role=RoleEnum.MEDICO,
        permissions=ctx_doc.permissions,
    )
    t_user = current_user_ctx.set(ctx_doc)
    t_db = current_db_session.set(async_session)
    try:
        # 1. Configurar Lunes y Sábado, luego cerrar Sábado via MCP
        res_cfg = await configurar_horario_base_y_perfil(
            dias_semana=[1, 6],
            hora_apertura="08:00",
            hora_cierre="12:00",
        )
        assert res_cfg["success"] is True

        res_close = await configurar_horario_base_y_perfil(dias_cerrados=[6])
        assert res_close["success"] is True

        activos = (
            await async_session.execute(
                select(HorarioBase).where(
                    HorarioBase.profesional_id == perfil.id,
                    HorarioBase.activo.is_(True),
                )
            )
        ).scalars().all()
        assert [hb.dia_semana for hb in activos] == [1]

        # 2. Bloquear y luego desbloquear via MCP
        res_blk = await bloquear_horario_agenda(
            fecha_inicio="2026-11-30",
            hora_inicio="08:00",
            fecha_fin="2026-11-30",
            hora_fin="10:00",
            motivo="Cita personal",
        )
        assert res_blk["success"] is True

        res_unblk = await bloquear_horario_agenda(
            fecha_inicio="2026-11-30",
            hora_inicio="08:00",
            fecha_fin="2026-11-30",
            hora_fin="10:00",
            accion="DESBLOQUEAR",
        )
        assert res_unblk["success"] is True
        assert res_unblk["code"] == "SLOT_UNBLOCKED"
        assert res_unblk["bloqueos_eliminados"] == 1

        # 3. Jornada partida via MCP (08:00-12:00 y 14:00-18:00)
        res_split = await configurar_horario_base_y_perfil(
            dias_semana=[2],
            hora_apertura="08:00",
            hora_cierre="12:00",
            hora_apertura_2="14:00",
            hora_cierre_2="18:00",
        )
        assert res_split["success"] is True

        activos_martes = (
            await async_session.execute(
                select(HorarioBase).where(
                    HorarioBase.profesional_id == perfil.id,
                    HorarioBase.dia_semana == 2,
                    HorarioBase.activo.is_(True),
                )
            )
        ).scalars().all()
        assert len(activos_martes) == 2
    finally:
        current_user_ctx.reset(t_user)
        current_db_session.reset(t_db)

