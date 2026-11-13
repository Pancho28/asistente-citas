import uuid
from datetime import datetime, date, time, timezone, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select

from src.models.user import Usuario, PerfilProfesional, CodigoInvitacion
from src.models.schedule import HorarioBase
from src.models.appointment import Cita, Pago
from src.services.finance_service import (
    recalcular_estado_financiero_cita,
    registrar_pago_o_descuento,
    marcar_pago_completo_rapido,
    consultar_resumen_ganancias,
)
from src.services.onboarding_service import (
    generar_codigo_invitacion,
    canjear_codigo_invitacion,
    cambiar_modo_vista_sesion,
    configurar_horario_base_y_perfil,
)
from src.core.exceptions import (
    InvalidInvitationCodeError,
    PermissionDeniedError,
)


@pytest.mark.asyncio
async def test_finance_state_machine_and_discounts(async_session):
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_fin",
        nombre_completo="Dr. Robert Chase",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_fin",
        nombre_completo="Ned Flanders",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico, paciente])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Cardiología",
        tarifa_base_consulta=Decimal("100.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    cita = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 25, 9, 30, tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("100.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita)
    await async_session.commit()

    # 1. Sin pagos -> PENDIENTE
    estado = await recalcular_estado_financiero_cita(async_session, cita)
    assert estado == "PENDIENTE"

    # 2. Registrar abono parcial de $40 -> PARCIAL
    res_pago1 = await registrar_pago_o_descuento(
        db=async_session,
        profesional_usuario_id=medico.id,
        cita_id=cita.id,
        monto_pagado=Decimal("40.00"),
        metodo_pago="TRANSFERENCIA",
    )
    assert res_pago1["nuevo_estado_financiero"] == "PARCIAL"

    # 3. Aplicar descuento de $20 (nuevo_monto_acordado = 80.00) sin nuevo pago -> Sigue PARCIAL ($40 de $80)
    res_desc = await registrar_pago_o_descuento(
        db=async_session,
        profesional_usuario_id=medico.id,
        cita_id=cita.id,
        monto_pagado=Decimal("0.00"),
        nuevo_monto_acordado=Decimal("80.00"),
    )
    assert res_desc["nuevo_estado_financiero"] == "PARCIAL"
    assert cita.monto_acordado == Decimal("80.00")

    # 4. Pago rápido del saldo restante ($40 restantes) -> PAGADO
    res_full = await marcar_pago_completo_rapido(
        db=async_session,
        profesional_usuario_id=medico.id,
        cita_id=cita.id,
    )
    assert res_full["nuevo_estado_financiero"] == "PAGADO"
    assert res_full["monto_completado"] == Decimal("40.00")


@pytest.mark.asyncio
async def test_onboarding_invitation_code_and_redemption(async_session):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_1",
        nombre_completo="Admin Principal",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="nuevo_doc_telegram",
        nombre_completo="Dr. Futuro",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([admin, paciente])
    await async_session.commit()

    # 1. Admin genera código de invitación para "Pediatría"
    codigo = await generar_codigo_invitacion(
        db=async_session,
        admin_id=admin.id,
        especialidad_asignada="Pediatría",
        horas_validez=48,
        frase_personalizada="MED-PEDI-2026",
    )
    assert codigo.frase_codigo == "MED-PEDI-2026"
    assert codigo.usado is False

    # 2. Paciente canjea el código
    perfil = await canjear_codigo_invitacion(
        db=async_session,
        usuario_id=paciente.id,
        frase_codigo="MED-PEDI-2026",
        nombre_profesional="Dr. Carlos Mendoza",
    )

    assert perfil.usuario_id == paciente.id
    assert perfil.especialidad == "Pediatría"
    assert paciente.rol_sistema == "MEDICO"
    assert paciente.modo_vista_actual == "MEDICO"
    assert codigo.usado is True

    # 3. Intentar canjear el mismo código de nuevo debe fallar
    with pytest.raises(InvalidInvitationCodeError):
        await canjear_codigo_invitacion(
            db=async_session,
            usuario_id=uuid.uuid4(),
            frase_codigo="MED-PEDI-2026",
            nombre_profesional="Dra. Infiltrada",
        )


@pytest.mark.asyncio
async def test_view_mode_switching_exclusivity(async_session):
    admin = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="admin_real",
        nombre_completo="Admin Real",
        rol_sistema="ADMIN",
        modo_vista_actual="ADMIN",
    )
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="medico_comun",
        nombre_completo="Dr. Simple",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    async_session.add_all([admin, medico])
    await async_session.commit()

    # 1. Admin puede cambiar a PACIENTE
    u_admin = await cambiar_modo_vista_sesion(
        db=async_session,
        usuario_id=admin.id,
        nuevo_modo="PACIENTE",
    )
    assert u_admin.modo_vista_actual == "PACIENTE"
    assert u_admin.rol_sistema == "ADMIN"  # rol_sistema permanece intacto

    # 2. Admin (actualmente en vista PACIENTE) puede volver a ADMIN
    u_admin_back = await cambiar_modo_vista_sesion(
        db=async_session,
        usuario_id=admin.id,
        nuevo_modo="ADMIN",
    )
    assert u_admin_back.modo_vista_actual == "ADMIN"

    # 3. Un usuario con rol_sistema == 'MEDICO' no puede cambiar su vista -> PermissionDeniedError
    with pytest.raises(PermissionDeniedError):
        await cambiar_modo_vista_sesion(
            db=async_session,
            usuario_id=medico.id,
            nuevo_modo="ADMIN",
        )


@pytest.mark.asyncio
async def test_doctor_payment_by_patient_name_and_incremental_schedule(async_session):
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_inc_7",
        nombre_completo="Dr. House Incremental",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_laura_7",
        nombre_completo="Laura Martínez",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico, paciente])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Medicina Interna",
        tarifa_base_consulta=Decimal("50.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    cita = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita)
    await async_session.commit()

    # 1. Registrar pago pasando el nombre ("Laura") en vez de UUID
    res_pago = await registrar_pago_o_descuento(
        db=async_session,
        profesional_usuario_id=medico.id,
        cita_id="Laura",
        monto_pagado=Decimal("50.00"),
        metodo_pago="EFECTIVO",
    )
    assert res_pago["success"] is True
    assert res_pago["cita_id"] == str(cita.id)
    assert res_pago["nuevo_estado_financiero"] == "PAGADO"

    # 2. Configurar Lunes a Viernes (1..5) de 09:00 a 17:00
    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=medico.id,
        dias_semana=[1, 2, 3, 4, 5],
        hora_apertura=time(9, 0),
        hora_cierre=time(17, 0),
    )

    # 3. Agregar Sábado (6) de 09:00 a 13:00 -> NO debe borrar Lunes a Viernes
    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=medico.id,
        dias_semana=[6],
        hora_apertura=time(9, 0),
        hora_cierre=time(13, 0),
    )

    stmt_activos = select(HorarioBase).where(
        HorarioBase.profesional_id == perfil.id,
        HorarioBase.activo.is_(True),
    )
    activos = (await async_session.execute(stmt_activos)).scalars().all()
    dias_activos = sorted(hb.dia_semana for hb in activos)
    assert dias_activos == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_doctor_close_specific_days_in_schedule(async_session):
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_close_days_8",
        nombre_completo="Dr. Cuddy Schedule",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    async_session.add(medico)
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Administración Médica",
        tarifa_base_consulta=Decimal("60.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    # 1. Configurar Lunes a Sábado (1..6)
    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=medico.id,
        dias_semana=[1, 2, 3, 4, 5, 6],
        hora_apertura=time(9, 0),
        hora_cierre=time(13, 0),
    )

    # 2. Cerrar únicamente los sábados (6) sin pasar hora_apertura ni hora_cierre
    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=medico.id,
        dias_cerrados=[6],
    )

    stmt_activos = select(HorarioBase).where(
        HorarioBase.profesional_id == perfil.id,
        HorarioBase.activo.is_(True),
    )
    activos = (await async_session.execute(stmt_activos)).scalars().all()
    assert sorted(hb.dia_semana for hb in activos) == [1, 2, 3, 4, 5]

    # 3. En la misma llamada: abrir domingo (0) y cerrar viernes (5)
    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=medico.id,
        dias_semana=[0],
        hora_apertura=time(10, 0),
        hora_cierre=time(14, 0),
        dias_cerrados=[5],
    )

    activos_2 = (await async_session.execute(stmt_activos)).scalars().all()
    assert sorted(hb.dia_semana for hb in activos_2) == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_doctor_split_shift_schedule_generation(async_session):
    from src.services.availability_service import consultar_disponibilidad
    from src.services.appointment_service import _formatear_horario_semanal
    from tests.conftest import FakeRedis

    fake_redis = FakeRedis()
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_split_9",
        nombre_completo="Dr. House Split",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(medico)
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Cardiología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("75.00"),
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil)
    await async_session.commit()

    # Configurar Lunes (1) con jornada partida: 08:00-10:00 y 14:00-16:00
    await configurar_horario_base_y_perfil(
        db=async_session,
        profesional_usuario_id=medico.id,
        dias_semana=[1],
        hora_apertura=time(8, 0),
        hora_cierre=time(10, 0),
        hora_apertura_2=time(14, 0),
        hora_cierre_2=time(16, 0),
    )

    stmt_activos = select(HorarioBase).where(
        HorarioBase.profesional_id == perfil.id,
        HorarioBase.activo.is_(True),
    )
    activos = list((await async_session.execute(stmt_activos)).scalars().all())
    assert len(activos) == 2

    resumen = _formatear_horario_semanal(activos)
    assert "Lunes de 08:00 a 10:00 y de 14:00 a 16:00" in resumen

    disp = await consultar_disponibilidad(
        db=async_session,
        redis_client=fake_redis,
        fecha=date(2026, 11, 23),  # Lunes
        profesional_id=perfil.id,
    )
    assert disp.horarios_libres == [
        "08:00", "08:30", "09:00", "09:30",
        "14:00", "14:30", "15:00", "15:30",
    ]

