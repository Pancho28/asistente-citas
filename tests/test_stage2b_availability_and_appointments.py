import uuid
from datetime import datetime, date, time, timezone, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select

from src.models.user import Usuario, PerfilProfesional
from src.models.schedule import HorarioBase, BloqueoAgenda
from src.models.appointment import Cita
from src.services.availability_service import consultar_disponibilidad
from src.services.appointment_service import (
    agendar_cita_paciente,
    listar_citas_activas_paciente,
    cancelar_o_reagendar_cita_paciente,
    bloquear_horario_agenda,
    ver_agenda_profesional,
    agendar_o_mover_cita_manual_medico,
    confirmar_asistencia_recordatorio,
    marcar_inasistencia_medico,
)
from src.core.exceptions import (
    SlotTakenError,
    CancellationWindowExpiredError,
    EntityNotFoundError,
    ValidationError,
)
from tests.conftest import FakeRedis


@pytest.mark.asyncio
async def test_availability_calculation_by_exception(async_session):
    fake_redis = FakeRedis()

    # 1. Crear médico con jornada: Lunes (dia_semana=1) de 08:00 a 11:00 (citas de 30 min: 6 bloques)
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_1",
        nombre_completo="Dr. Gregory House",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    async_session.add(medico)
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Medicina Interna",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("50.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=2,
    )
    async_session.add(perfil)
    await async_session.commit()

    # Lunes = dia_semana 1 según convención SPEC.md (0=Dom, 1=Lun)
    horario = HorarioBase(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        dia_semana=1,
        hora_inicio=time(8, 0),
        hora_fin=time(11, 0),
        activo=True,
    )
    async_session.add(horario)
    await async_session.commit()

    # Lunes futuro (es un lunes)
    fecha_lunes = date(2026, 11, 16)
    assert (fecha_lunes.weekday() + 1) % 7 == 1  # Verifica que es día 1 (Lunes)

    # Bloque 1: 08:00
    # Bloque 2: 08:30 (Ocupado por una Cita activa)
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_1",
        nombre_completo="Juan Perez",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add(paciente)
    await async_session.commit()

    cita_ocupada = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime(2026, 11, 16, 8, 30, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita_ocupada)

    # Bloque 3: 09:00 (Ocupado por un Bloqueo de Agenda)
    bloqueo = BloqueoAgenda(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        fecha_hora_inicio=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 16, 9, 30, tzinfo=timezone.utc),
        motivo="Reunión clínica",
    )
    async_session.add(bloqueo)
    await async_session.commit()

    # Bloque 4: 09:30 (Bloqueado temporalmente por un lock en Redis)
    await fake_redis.set(f"lock:slot:{perfil.id}:2026-11-16_09:30", "otro_paciente_id")

    # Bloque 5: 10:00 (Libre)
    # Bloque 6: 10:30 (Libre)

    # Ejecutar consulta de disponibilidad
    resultado = await consultar_disponibilidad(
        db=async_session,
        redis_client=fake_redis,
        fecha=fecha_lunes,
        profesional_id=perfil.id,
    )

    assert resultado.success is True
    assert resultado.profesional_id == perfil.id
    # De 6 bloques iniciales [08:00, 08:30, 09:00, 09:30, 10:00, 10:30],
    # solo deben quedar 08:00, 10:00 y 10:30
    assert resultado.horarios_libres == ["08:00", "10:00", "10:30"]


@pytest.mark.asyncio
async def test_appointment_booking_and_conflict(async_session):
    fake_redis = FakeRedis()

    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_2",
        nombre_completo="Dra. Cuddy",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_2",
        nombre_completo="Lisa Simpson",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico, paciente])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Endocrinología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("80.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=2,
    )
    async_session.add(perfil)
    await async_session.commit()

    # Horario martes (dia_semana=2) de 14:00 a 16:00
    horario = HorarioBase(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        dia_semana=2,
        hora_inicio=time(14, 0),
        hora_fin=time(16, 0),
        activo=True,
    )
    async_session.add(horario)
    await async_session.commit()

    fecha_martes = date(2026, 11, 10)  # Martes

    # 1. Agendar cita exitosamente
    cita = await agendar_cita_paciente(
        db=async_session,
        redis_client=fake_redis,
        paciente_id=paciente.id,
        fecha=fecha_martes,
        hora=time(14, 0),
        nombre_completo_paciente="Lisa Simpson",
        motivo_consulta="Chequeo preventivo",
        profesional_id=perfil.id,
    )
    assert cita.id is not None
    assert cita.monto_acordado == Decimal("80.00")
    assert cita.estado == "CONFIRMADA"
    assert cita.estado_financiero == "PENDIENTE"

    # 2. Intentar agendar en el mismo horario debe lanzar SlotTakenError
    with pytest.raises(SlotTakenError):
        await agendar_cita_paciente(
            db=async_session,
            redis_client=fake_redis,
            paciente_id=paciente.id,
            fecha=fecha_martes,
            hora=time(14, 0),
            nombre_completo_paciente="Otro Paciente",
            profesional_id=perfil.id,
        )


@pytest.mark.asyncio
async def test_appointment_cancellation_window_enforcement(async_session):
    fake_redis = FakeRedis()

    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_3",
        nombre_completo="Dr. Foreman",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_3",
        nombre_completo="Homero Simpson",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico, paciente])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Neurología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("70.00"),
        min_horas_anticipacion_cancelar=24,  # Requiere 24 horas de anticipación
    )
    async_session.add(perfil)
    await async_session.commit()

    # Cita programada para dentro de solo 2 horas (viola la ventana de 24h)
    ahora_utc = datetime.now(timezone.utc)
    cita_inminente = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=ahora_utc + timedelta(hours=2),
        fecha_hora_fin=ahora_utc + timedelta(hours=2, minutes=30),
        estado="CONFIRMADA",
        monto_acordado=Decimal("70.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita_inminente)
    await async_session.commit()

    # Rechazo por ventana de anticipación expirada
    with pytest.raises(CancellationWindowExpiredError):
        await cancelar_o_reagendar_cita_paciente(
            db=async_session,
            redis_client=fake_redis,
            paciente_id=paciente.id,
            cita_id=cita_inminente.id,
            accion="CANCELAR",
        )


@pytest.mark.asyncio
async def test_doctor_blocking_agenda_warns_on_affected_appointments(async_session):
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_4",
        nombre_completo="Dr. Chase",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_4",
        nombre_completo="Marge Simpson",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico, paciente])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Cirugía",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("100.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    cita_existente = Cita(
        id=uuid.uuid4(),
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 20, 10, 30, tzinfo=timezone.utc),
        estado="CONFIRMADA",
        monto_acordado=Decimal("100.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="IA_TEXTO",
    )
    async_session.add(cita_existente)
    await async_session.commit()

    # El médico intenta bloquear toda la mañana del 2026-11-20 (08:00 a 12:00)
    res = await bloquear_horario_agenda(
        db=async_session,
        profesional_usuario_id=medico.id,
        fecha_hora_inicio=datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
        motivo="Congreso",
        forzar_con_citas=False,
    )

    assert res["success"] is False
    assert res["code"] == "AFFECTED_APPOINTMENTS"
    assert len(res["citas_afectadas"]) == 1
    assert res["citas_afectadas"][0]["cita_id"] == str(cita_existente.id)


@pytest.mark.asyncio
async def test_doctor_manual_cancel_and_collision_prevention(async_session):
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_manual_7",
        nombre_completo="Dr. Wilson",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    async_session.add(medico)
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Oncología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("90.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    fecha_futura = date(2026, 11, 23)
    # 1. Médico crea cita manual para "Roberto Gómez" a las 10:00
    cita_roberto = await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CREAR",
        fecha_destino=fecha_futura,
        hora_destino=time(10, 0),
        nombre_paciente="Roberto Gómez",
        telefono_paciente="584121112233",
    )
    assert cita_roberto.estado == "CONFIRMADA"

    # 2. Intentar CREAR otra cita a la misma hora debe lanzar SlotTakenError
    with pytest.raises(SlotTakenError):
        await agendar_o_mover_cita_manual_medico(
            db=async_session,
            profesional_usuario_id=medico.id,
            accion="CREAR",
            fecha_destino=fecha_futura,
            hora_destino=time(10, 0),
            nombre_paciente="Segundo Paciente",
        )

    # 3. Crear otra cita a las 11:00 e intentar MOVERla a las 10:00 -> SlotTakenError
    cita_lucia = await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CREAR",
        fecha_destino=fecha_futura,
        hora_destino=time(11, 0),
        nombre_paciente="Lucía Fernández",
    )
    with pytest.raises(SlotTakenError):
        await agendar_o_mover_cita_manual_medico(
            db=async_session,
            profesional_usuario_id=medico.id,
            accion="MOVER",
            cita_id=cita_lucia.id,
            fecha_destino=fecha_futura,
            hora_destino=time(10, 0),
        )

    # 4. Médico cancela la cita de Roberto buscando solo por nombre ("Roberto") sin UUID ni fecha/hora
    cita_cancelada = await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CANCELAR",
        nombre_paciente="Roberto",
    )
    assert cita_cancelada.id == cita_roberto.id
    assert cita_cancelada.estado == "CANCELADA"
    assert cita_cancelada.estado_financiero == "EXONERADO"


@pytest.mark.asyncio
async def test_patient_cancel_without_uuid_and_out_of_hours_validation(async_session):
    fake_redis = FakeRedis()

    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_pac_7",
        nombre_completo="Dra. Cameron",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_7",
        nombre_completo="Ana Morales",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
    )
    async_session.add_all([medico, paciente])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Inmunología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("60.00"),
        min_horas_anticipacion_cancelar=2,
    )
    async_session.add(perfil)
    await async_session.commit()

    # Solo atiende los lunes (dia_semana=1) de 09:00 a 12:00
    async_session.add(
        HorarioBase(
            profesional_id=perfil.id,
            dia_semana=1,
            hora_inicio=time(9, 0),
            hora_fin=time(12, 0),
            activo=True,
        )
    )
    await async_session.commit()

    lunes_futuro = date(2026, 11, 23)  # Lunes
    domingo_futuro = date(2026, 11, 22)  # Domingo (no laboral)

    # 1. Intentar agendar el domingo o a las 23:00 el lunes debe lanzar ValidationError con el horario general
    with pytest.raises(ValidationError) as exc_info:
        await agendar_cita_paciente(
            db=async_session,
            redis_client=fake_redis,
            paciente_id=paciente.id,
            fecha=domingo_futuro,
            hora=time(10, 0),
            nombre_completo_paciente="Ana Morales",
            profesional_id=perfil.id,
        )
    assert "Lunes de 09:00 a 12:00" in exc_info.value.message

    with pytest.raises(ValidationError):
        await agendar_cita_paciente(
            db=async_session,
            redis_client=fake_redis,
            paciente_id=paciente.id,
            fecha=lunes_futuro,
            hora=time(23, 0),
            nombre_completo_paciente="Ana Morales",
            profesional_id=perfil.id,
        )

    # 2. Agendar en horario válido (Lunes 09:30)
    cita = await agendar_cita_paciente(
        db=async_session,
        redis_client=fake_redis,
        paciente_id=paciente.id,
        fecha=lunes_futuro,
        hora=time(9, 30),
        nombre_completo_paciente="Ana Morales",
        profesional_id=perfil.id,
    )
    old_lock_key = f"lock:slot:{perfil.id}:{lunes_futuro.isoformat()}_09:30"
    assert await fake_redis.get(old_lock_key) == str(paciente.id)

    # 3. Reagendar SIN UUID (cita_id=None) a las 10:30: debe liberar lock viejo y conservar nombre del paciente
    nueva_cita = await cancelar_o_reagendar_cita_paciente(
        db=async_session,
        redis_client=fake_redis,
        paciente_id=paciente.id,
        cita_id=None,
        accion="REAGENDAR",
        nueva_fecha=lunes_futuro,
        nueva_hora=time(10, 30),
    )
    assert nueva_cita.id != cita.id
    assert await fake_redis.get(old_lock_key) is None
    await async_session.refresh(paciente)
    assert paciente.nombre_completo == "Ana Morales"

    # 4. Cancelar SIN UUID (cita_id=None)
    cita_cancelada = await cancelar_o_reagendar_cita_paciente(
        db=async_session,
        redis_client=fake_redis,
        paciente_id=paciente.id,
        cita_id=None,
        accion="CANCELAR",
    )
    assert cita_cancelada.id == nueva_cita.id
    assert cita_cancelada.estado == "CANCELADA"


@pytest.mark.asyncio
async def test_doctor_unblock_schedule_restores_availability(async_session):
    fake_redis = FakeRedis()

    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_unblock_8",
        nombre_completo="Dr. House Unblock",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(medico)
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Diagnóstico",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("60.00"),
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil)
    await async_session.commit()

    async_session.add(
        HorarioBase(
            profesional_id=perfil.id,
            dia_semana=1,
            hora_inicio=time(9, 0),
            hora_fin=time(12, 0),
            activo=True,
        )
    )
    await async_session.commit()

    lunes_futuro = date(2026, 11, 23)

    # 1. Bloquear de 09:00 a 11:00
    res_block = await bloquear_horario_agenda(
        db=async_session,
        profesional_usuario_id=medico.id,
        fecha_hora_inicio=datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 23, 11, 0, tzinfo=timezone.utc),
        motivo="Junta médica",
    )
    assert res_block["success"] is True

    disp_bloqueada = await consultar_disponibilidad(
        db=async_session,
        redis_client=fake_redis,
        fecha=lunes_futuro,
        profesional_id=perfil.id,
    )
    assert disp_bloqueada.horarios_libres == ["11:00", "11:30"]

    # 2. Desbloquear el rango 09:00 a 11:00
    res_unblock = await bloquear_horario_agenda(
        db=async_session,
        profesional_usuario_id=medico.id,
        fecha_hora_inicio=datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 23, 11, 0, tzinfo=timezone.utc),
        accion="DESBLOQUEAR",
    )
    assert res_unblock["success"] is True
    assert res_unblock["code"] == "SLOT_UNBLOCKED"
    assert res_unblock["bloqueos_eliminados"] == 1

    # 3. Verificar que la disponibilidad completa fue restaurada
    disp_restaurada = await consultar_disponibilidad(
        db=async_session,
        redis_client=fake_redis,
        fecha=lunes_futuro,
        profesional_id=perfil.id,
    )
    assert disp_restaurada.horarios_libres == [
        "09:00", "09:30", "10:00", "10:30", "11:00", "11:30"
    ]

    # 4. Intentar desbloquear de nuevo cuando no hay bloqueos -> NO_BLOCKS_FOUND
    res_empty = await bloquear_horario_agenda(
        db=async_session,
        profesional_usuario_id=medico.id,
        fecha_hora_inicio=datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc),
        fecha_hora_fin=datetime(2026, 11, 23, 11, 0, tzinfo=timezone.utc),
        accion="DESBLOQUEAR",
    )
    assert res_empty["success"] is False
    assert res_empty["code"] == "NO_BLOCKS_FOUND"
    assert res_empty["bloqueos_eliminados"] == 0


@pytest.mark.asyncio
async def test_doctor_disambiguation_on_homonym_patients(async_session):
    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_homonym_9",
        nombre_completo="Dr. House Homónimos",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(medico)
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Medicina Interna",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("50.00"),
    )
    async_session.add(perfil)
    await async_session.commit()

    # Crear 2 pacientes con nombre "Carlos"
    c1 = await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CREAR",
        fecha_destino=date(2026, 11, 24),
        hora_destino=time(9, 0),
        nombre_paciente="Carlos Pérez",
    )
    c2 = await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CREAR",
        fecha_destino=date(2026, 11, 24),
        hora_destino=time(10, 0),
        nombre_paciente="Carlos Gómez",
    )

    # Buscar por "Carlos" debe exigir desambiguación con ValidationError mostrando ambas opciones
    with pytest.raises(ValidationError) as exc_info:
        await agendar_o_mover_cita_manual_medico(
            db=async_session,
            profesional_usuario_id=medico.id,
            accion="CANCELAR",
            nombre_paciente="Carlos",
        )
    msg = exc_info.value.message
    assert "Carlos Pérez" in msg
    assert "Carlos Gómez" in msg
    assert str(c1.id) in msg
    assert str(c2.id) in msg

    # Al elegir uno por su cita_id específico, sí cancela ese y deja intacto el otro
    cancelada = await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CANCELAR",
        cita_id=c2.id,
    )
    assert cancelada.id == c2.id
    assert cancelada.estado == "CANCELADA"

    await async_session.refresh(c1)
    assert c1.estado == "CONFIRMADA"


@pytest.mark.asyncio
async def test_realtime_cross_notifications_between_doctor_and_patient(async_session, monkeypatch):
    fake_redis = FakeRedis()
    sent_notifications: list[tuple[str, str]] = []

    async def fake_send_message(self, recipient: str | None = None, text: str = "", **kwargs) -> bool:
        target = recipient or kwargs.get("chat_id") or kwargs.get("phone_number") or ""
        sent_notifications.append((str(target), text))
        return True

    from src.adapters.telegram import TelegramAdapter
    monkeypatch.setattr(TelegramAdapter, "send_message", fake_send_message)

    medico = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="doc_notif_9",
        nombre_completo="Dr. Pérez Notif",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    paciente = Usuario(
        id=uuid.uuid4(),
        canal_origen="TELEGRAM",
        external_chat_id="pac_notif_9",
        nombre_completo="Sofía Rivas",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add_all([medico, paciente])
    await async_session.commit()

    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Dermatología",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("60.00"),
        min_horas_anticipacion_cancelar=2,
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil)
    await async_session.commit()

    # 1. Paciente agenda -> debe notificar al chat del médico
    cita = await agendar_cita_paciente(
        db=async_session,
        redis_client=fake_redis,
        paciente_id=paciente.id,
        fecha=date(2026, 11, 25),
        hora=time(10, 0),
        nombre_completo_paciente="Sofía Rivas",
        profesional_id=perfil.id,
    )
    assert any(target == "doc_notif_9" and "Sofía Rivas" in txt for target, txt in sent_notifications)
    sent_notifications.clear()

    # 2. Médico mueve manualmente la cita -> debe notificar al chat del paciente
    await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="MOVER",
        cita_id=cita.id,
        fecha_destino=date(2026, 11, 25),
        hora_destino=time(15, 0),
    )
    assert any(target == "pac_notif_9" and "15:00" in txt for target, txt in sent_notifications)
    sent_notifications.clear()

    # 3. Médico cancela manualmente la cita -> debe notificar al chat del paciente
    await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CANCELAR",
        cita_id=cita.id,
    )
    assert any(target == "pac_notif_9" and "cancel" in txt.lower() for target, txt in sent_notifications)
    sent_notifications.clear()

    # 4. Cita manual con paciente sin chat real ("manual_...") no debe enviar notificación
    cita_manual = await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CREAR",
        fecha_destino=date(2026, 11, 26),
        hora_destino=time(11, 0),
        nombre_paciente="Paciente Externo",
    )
    await agendar_o_mover_cita_manual_medico(
        db=async_session,
        profesional_usuario_id=medico.id,
        accion="CANCELAR",
        cita_id=cita_manual.id,
    )
    assert sent_notifications == []
