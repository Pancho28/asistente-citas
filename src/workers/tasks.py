import logging
from datetime import datetime, date, time, timedelta
from typing import Any
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select, cast, Date, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import get_settings, now_tz
from src.core.database import async_session_factory
from src.models.appointment import Cita
from src.models.user import PerfilProfesional, Usuario
from src.adapters.base import build_buttons_from_ui_hint
from src.adapters.telegram import TelegramAdapter
from src.adapters.whatsapp import WhatsAppAdapter

logger = logging.getLogger(__name__)
settings = get_settings()


async def _do_enviar_recordatorios(db: AsyncSession) -> int:
    current_tz = now_tz()
    manana = current_tz.date() + timedelta(days=1)
    inicio_manana = datetime.combine(manana, time(0, 0, 0), tzinfo=current_tz.tzinfo)
    fin_manana = datetime.combine(manana, time(23, 59, 59), tzinfo=current_tz.tzinfo)

    # Buscar citas confirmadas del día siguiente con recordatorio pendiente
    stmt = (
        select(Cita)
        .options(selectinload(Cita.paciente), selectinload(Cita.profesional))
        .where(
            Cita.estado == "CONFIRMADA",
            Cita.recordatorio_paciente_enviado.is_(False),
            Cita.fecha_hora_inicio >= inicio_manana,
            Cita.fecha_hora_inicio <= fin_manana,
        )
    )
    res = await db.execute(stmt)
    citas = res.scalars().all()

    enviados = 0
    for cita in citas:
        if not cita.paciente:
            continue

        buttons = build_buttons_from_ui_hint(
            "SHOW_REMINDER_BUTTONS", {"cita_id": str(cita.id)}
        )
        adapter = (
            TelegramAdapter()
            if cita.paciente.canal_origen == "TELEGRAM"
            else WhatsAppAdapter()
        )

        hora_fmt = cita.fecha_hora_inicio.strftime("%H:%M")
        nombre_paciente = cita.paciente.nombre_completo or "Estimado(a) paciente"
        text = (
            f"Hola {nombre_paciente}, te recordamos tu cita médica para mañana a las {hora_fmt}.\n\n"
            "Por favor confirma tu asistencia o cancela con anticipación:"
        )

        try:
            ok = await adapter.send_message(
                recipient=cita.paciente.external_chat_id,
                text=text,
                buttons=buttons,
            )
            if ok:
                cita.recordatorio_paciente_enviado = True
                cita.recordatorio_enviado_en = now_tz()
                enviados += 1
        except Exception as exc:
            logger.error("[WORKER] Error al enviar recordatorio para cita %s: %s", cita.id, exc)

    await db.commit()
    logger.info("[WORKER] Lote de recordatorios de las 12:00 PM: %d enviados.", enviados)
    return enviados


async def enviar_recordatorios_diarios_pacientes(ctx: dict[str, Any]) -> int:
    """
    Tarea programada diaria a las 12:00 PM.
    Selecciona las citas del día siguiente y envía mensaje interactivo con botones
    [Confirmar Asistencia] y [Cancelar Cita]. Es 100% idempotente.
    """
    if "db" in ctx and ctx["db"] is not None:
        return await _do_enviar_recordatorios(ctx["db"])

    async with async_session_factory() as session:
        return await _do_enviar_recordatorios(session)


async def _do_heartbeat(db: AsyncSession) -> dict[str, int]:
    current_tz = now_tz()

    # 1. Cierre automático de citas pasadas
    stmt_past = select(Cita).where(
        Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]),
        Cita.fecha_hora_fin < current_tz,
    )
    res_past = await db.execute(stmt_past)
    citas_pasadas = res_past.scalars().all()

    for c in citas_pasadas:
        c.estado = "COMPLETADA"

    count_completadas = len(citas_pasadas)

    # 2. Envío de resumen de agenda a médicos configurados en esta hora
    hora_actual_hour = current_tz.hour
    stmt_prof = (
        select(PerfilProfesional)
        .options(selectinload(PerfilProfesional.usuario))
        .where(PerfilProfesional.acepta_nuevas_citas.is_(True))
    )
    res_prof = await db.execute(stmt_prof)
    perfiles = res_prof.scalars().all()

    count_resumenes = 0
    for perfil in perfiles:
        if perfil.hora_resumen_diario.hour != hora_actual_hour:
            continue
        if perfil.ultimo_resumen_enviado_en and perfil.ultimo_resumen_enviado_en >= current_tz.date():
            continue  # Idempotencia: ya se envió hoy

        target_date = (
            current_tz.date()
            if perfil.alcance_resumen_diario == "AGENDA_HOY"
            else current_tz.date() + timedelta(days=1)
        )
        inicio_dia = datetime.combine(target_date, time(0, 0, 0), tzinfo=current_tz.tzinfo)
        fin_dia = datetime.combine(target_date, time(23, 59, 59), tzinfo=current_tz.tzinfo)

        stmt_citas_dia = (
            select(Cita)
            .options(selectinload(Cita.paciente))
            .where(
                Cita.profesional_id == perfil.id,
                Cita.estado != "CANCELADA",
                Cita.fecha_hora_inicio >= inicio_dia,
                Cita.fecha_hora_inicio <= fin_dia,
            )
            .order_by(Cita.fecha_hora_inicio)
        )
        res_dia = await db.execute(stmt_citas_dia)
        citas_dia = res_dia.scalars().all()

        alcance_nombre = "hoy" if perfil.alcance_resumen_diario == "AGENDA_HOY" else "mañana"
        resumen_lineas = [
            f"📋 <b>Resumen de Agenda para {alcance_nombre} ({target_date.strftime('%d/%m/%Y')}):</b>",
            f"Total de pacientes programados: {len(citas_dia)}",
            "",
        ]

        if not citas_dia:
            resumen_lineas.append("No tienes citas programadas para esta jornada.")
        else:
            for c in citas_dia:
                h_fmt = c.fecha_hora_inicio.strftime("%H:%M")
                p_nom = c.paciente.nombre_completo or "Paciente"
                conf = " (Confirmó ✅)" if c.estado == "CONFIRMADA_PACIENTE" else ""
                resumen_lineas.append(f"• <b>{h_fmt}</b> — {p_nom}{conf}")

        text_resumen = "\n".join(resumen_lineas)
        adapter = (
            TelegramAdapter()
            if perfil.usuario.canal_origen == "TELEGRAM"
            else WhatsAppAdapter()
        )

        try:
            ok = await adapter.send_message(
                recipient=perfil.usuario.external_chat_id,
                text=text_resumen,
            )
            if ok:
                perfil.ultimo_resumen_enviado_en = current_tz.date()
                count_resumenes += 1
        except Exception as exc:
            logger.error("[WORKER] Error al enviar resumen a médico %s: %s", perfil.id, exc)

    await db.commit()
    logger.info(
        "[WORKER] Heartbeat horario: %d citas completadas, %d resúmenes enviados.",
        count_completadas,
        count_resumenes,
    )
    return {
        "citas_completadas": count_completadas,
        "resumenes_enviados": count_resumenes,
    }


async def heartbeat_horario_medicos_y_cierre_citas(ctx: dict[str, Any]) -> dict[str, int]:
    """
    Heartbeat programado cada hora en punto.
    1. Cierra automáticamente citas pasadas actualizándolas a 'COMPLETADA'.
    2. Envía el resumen diario a los médicos cuya hora_resumen_diario coincida con la hora actual.
    """
    if "db" in ctx and ctx["db"] is not None:
        return await _do_heartbeat(ctx["db"])

    async with async_session_factory() as session:
        return await _do_heartbeat(session)


class WorkerSettings:
    """Configuración del worker asíncrono en segundo plano (arq)."""
    functions = [
        enviar_recordatorios_diarios_pacientes,
        heartbeat_horario_medicos_y_cierre_citas,
    ]
    cron_jobs = [
        cron(enviar_recordatorios_diarios_pacientes, hour=12, minute=0),
        cron(heartbeat_horario_medicos_y_cierre_citas, minute=0),
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

