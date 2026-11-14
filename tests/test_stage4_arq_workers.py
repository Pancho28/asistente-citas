import uuid
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import get_settings, now_tz
from src.models.user import Usuario, PerfilProfesional
from src.models.appointment import Cita

settings = get_settings()


@pytest.fixture
async def setup_worker_infra(async_session: AsyncSession):
    # 1. Crear médico
    medico = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="doc_worker_1",
        nombre_completo="Dr. Gregory House",
        rol_sistema="MEDICO",
        modo_vista_actual="MEDICO",
        activo=True,
    )
    async_session.add(medico)
    await async_session.commit()
    await async_session.refresh(medico)

    # Hora de resumen coincide con la hora actual para probar heartbeat
    hora_actual = now_tz().time().replace(minute=0, second=0, microsecond=0)
    perfil = PerfilProfesional(
        id=uuid.uuid4(),
        usuario_id=medico.id,
        especialidad="Diagnóstico",
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("50.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=2,
        hora_resumen_diario=hora_actual,
        alcance_resumen_diario="AGENDA_HOY",
        acepta_nuevas_citas=True,
    )
    async_session.add(perfil)

    # 2. Crear paciente
    paciente = Usuario(
        canal_origen="TELEGRAM",
        external_chat_id="pac_worker_1",
        nombre_completo="Paciente Worker",
        rol_sistema="PACIENTE",
        modo_vista_actual="PACIENTE",
        activo=True,
    )
    async_session.add(paciente)
    await async_session.commit()
    await async_session.refresh(paciente)

    return medico, perfil, paciente


@pytest.mark.asyncio
async def test_daily_12pm_patient_reminder_marks_sent_and_is_idempotent(
    async_session: AsyncSession, setup_worker_infra
):
    from src.workers.tasks import enviar_recordatorios_diarios_pacientes

    medico, perfil, paciente = setup_worker_infra

    current_tz = now_tz()
    manana = current_tz.date() + timedelta(days=1)
    pasado_manana = current_tz.date() + timedelta(days=2)

    # Cita 1: Para mañana, confirmada, recordatorio no enviado (DEBE ENVIARSE)
    cita_manana = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime.combine(manana, time(10, 0), tzinfo=current_tz.tzinfo),
        fecha_hora_fin=datetime.combine(manana, time(10, 30), tzinfo=current_tz.tzinfo),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
        recordatorio_paciente_enviado=False,
    )
    async_session.add(cita_manana)

    # Cita 2: Para pasado mañana (NO DEBE ENVIARSE HOY)
    cita_pasado = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime.combine(pasado_manana, time(11, 0), tzinfo=current_tz.tzinfo),
        fecha_hora_fin=datetime.combine(pasado_manana, time(11, 30), tzinfo=current_tz.tzinfo),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
        recordatorio_paciente_enviado=False,
    )
    async_session.add(cita_pasado)

    # Cita 3: Para mañana pero ya enviada (IDEMPOTENCIA: NO DEBE DUPLICAR)
    cita_ya_enviada = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=datetime.combine(manana, time(14, 0), tzinfo=current_tz.tzinfo),
        fecha_hora_fin=datetime.combine(manana, time(14, 30), tzinfo=current_tz.tzinfo),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
        recordatorio_paciente_enviado=True,
    )
    async_session.add(cita_ya_enviada)
    await async_session.commit()

    # Ejecutar tarea con mock de envío de mensajes
    with patch("src.adapters.telegram.TelegramAdapter.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        ctx = {"db": async_session}
        enviados = await enviar_recordatorios_diarios_pacientes(ctx)

        assert enviados == 1
        assert mock_send.call_count == 1

        await async_session.refresh(cita_manana)
        assert cita_manana.recordatorio_paciente_enviado is True
        assert cita_manana.recordatorio_enviado_en is not None

        # Segunda ejecución: Debe ser 0 (Idempotente)
        enviados_segunda = await enviar_recordatorios_diarios_pacientes(ctx)
        assert enviados_segunda == 0
        assert mock_send.call_count == 1  # No se incrementa


@pytest.mark.asyncio
async def test_hourly_heartbeat_auto_completes_past_appointments_and_sends_doctor_summary(
    async_session: AsyncSession, setup_worker_infra
):
    from src.workers.tasks import heartbeat_horario_medicos_y_cierre_citas

    medico, perfil, paciente = setup_worker_infra
    current_tz = now_tz()

    # Cita en el pasado (hace 2 horas) -> Debe pasar a COMPLETADA
    cita_pasada = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=current_tz - timedelta(hours=2),
        fecha_hora_fin=current_tz - timedelta(hours=1, minutes=30),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
    )
    async_session.add(cita_pasada)

    # Cita en el futuro (en 2 horas) -> Debe permanecer CONFIRMADA
    cita_futura = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente.id,
        fecha_hora_inicio=current_tz + timedelta(hours=2),
        fecha_hora_fin=current_tz + timedelta(hours=2, minutes=30),
        estado="CONFIRMADA",
        monto_acordado=Decimal("50.00"),
        estado_financiero="PENDIENTE",
        origen_creacion="BOTON_RAPIDO",
    )
    async_session.add(cita_futura)
    await async_session.commit()

    with patch("src.adapters.telegram.TelegramAdapter.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        ctx = {"db": async_session}
        res = await heartbeat_horario_medicos_y_cierre_citas(ctx)

        assert res["citas_completadas"] >= 1
        assert res["resumenes_enviados"] == 1
        assert mock_send.called

        await async_session.refresh(cita_pasada)
        await async_session.refresh(cita_futura)
        assert cita_pasada.estado == "COMPLETADA"
        assert cita_futura.estado == "CONFIRMADA"

        await async_session.refresh(perfil)
        assert perfil.ultimo_resumen_enviado_en == current_tz.date()

        # Idempotencia: segunda ejecución no reenvía resumen al mismo médico
        res_segunda = await heartbeat_horario_medicos_y_cierre_citas(ctx)
        assert res_segunda["resumenes_enviados"] == 0


def test_worker_settings_uses_configured_timezone():
    from zoneinfo import ZoneInfo
    from src.workers.tasks import WorkerSettings

    assert WorkerSettings.timezone == ZoneInfo(settings.DEFAULT_TIMEZONE)


