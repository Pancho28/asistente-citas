from datetime import datetime, date, time, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID
from src.agent.mcp_tools.registry import (
    require_permission,
    current_user_ctx,
    current_db_session,
)
from src.services.appointment_service import (
    ver_agenda_profesional as service_ver_agenda,
    bloquear_horario_agenda as service_bloquear_horario,
    agendar_o_mover_cita_manual_medico as service_cita_manual,
)
from src.services.finance_service import (
    registrar_pago_o_descuento as service_registrar_pago,
    consultar_resumen_ganancias as service_resumen_ganancias,
)
from src.services.onboarding_service import (
    configurar_horario_base_y_perfil as service_configurar_perfil,
)
from src.core.exceptions import EntityNotFoundError, ValidationError, SlotTakenError, AppError


@require_permission("agenda:ver_pacientes_dia")
async def ver_agenda_profesional(
    fecha_inicio: str,
    fecha_fin: str | None = None,
    filtro_estado: str = "TODAS",
) -> dict[str, Any]:
    """
    Obtiene la lista cronológica de citas del médico para un día o rango de fechas (YYYY-MM-DD).
    Permite filtrar por estado ('TODAS', 'PENDIENTES_HOY', 'COMPLETADAS', 'IMPAGAS').
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    try:
        parsed_inicio = date.fromisoformat(fecha_inicio)
        parsed_fin = date.fromisoformat(fecha_fin) if fecha_fin else None
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATE", "message": str(exc)}

    filtro = filtro_estado.upper() if filtro_estado.upper() in ("TODAS", "PENDIENTES_HOY", "COMPLETADAS", "IMPAGAS") else "TODAS"

    try:
        citas = await service_ver_agenda(
            db=db,
            profesional_usuario_id=ctx.user_id,
            fecha_inicio=parsed_inicio,
            fecha_fin=parsed_fin,
            filtro_estado=filtro,  # type: ignore
        )
        return {
            "success": True,
            "total_citas": len(citas),
            "citas": citas,
            "ui_hint": "SHOW_DOCTOR_ACTION_BUTTONS",
        }
    except EntityNotFoundError as exc:
        return {"success": False, "code": "NOT_FOUND", "message": exc.message}


@require_permission("agenda:bloquear_horario")
async def bloquear_horario_agenda(
    fecha_inicio: str,
    hora_inicio: str,
    fecha_fin: str,
    hora_fin: str,
    motivo: str | None = None,
    forzar_con_citas: bool = False,
    accion: str = "BLOQUEAR",
) -> dict[str, Any]:
    """
    Registra un bloqueo temporal en la agenda del médico ('BLOQUEAR') o elimina bloqueos
    existentes en ese rango para reabrir la disponibilidad ('DESBLOQUEAR').
    Las fechas deben ser YYYY-MM-DD y las horas HH:MM.
    En 'BLOQUEAR', si existen citas confirmadas en ese rango y forzar_con_citas es False,
    advierte con la lista de citas afectadas; usa forzar_con_citas=True únicamente cuando
    el médico confirme que desea bloquear de todas formas.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    act = accion.upper()
    if act not in ("BLOQUEAR", "DESBLOQUEAR"):
        return {"success": False, "code": "INVALID_ACTION", "message": "Acción debe ser 'BLOQUEAR' o 'DESBLOQUEAR'"}

    try:
        dt_inicio = datetime.fromisoformat(f"{fecha_inicio}T{hora_inicio}:00").replace(tzinfo=timezone.utc)
        dt_fin = datetime.fromisoformat(f"{fecha_fin}T{hora_fin}:00").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATETIME", "message": str(exc)}

    try:
        res = await service_bloquear_horario(
            db=db,
            profesional_usuario_id=ctx.user_id,
            fecha_hora_inicio=dt_inicio,
            fecha_hora_fin=dt_fin,
            motivo=motivo,
            forzar_con_citas=forzar_con_citas,
            accion=act,  # type: ignore
        )
        return res
    except (EntityNotFoundError, ValidationError, AppError) as exc:
        return {"success": False, "code": "BLOCK_ERROR", "message": exc.message}


@require_permission("agenda:configurar_perfil")
async def configurar_horario_base_y_perfil(
    dias_semana: list[int] | None = None,
    hora_apertura: str | None = None,
    hora_cierre: str | None = None,
    hora_apertura_2: str | None = None,
    hora_cierre_2: str | None = None,
    dias_cerrados: list[int] | None = None,
    duracion_cita_minutos: int | None = None,
    tarifa_base_consulta: float | None = None,
    hora_resumen_diario: str | None = None,
    alcance_resumen_diario: str | None = None,
) -> dict[str, Any]:
    """
    Actualiza la jornada semanal (continua o partida con horario de almuerzo), cierra días completos,
    o cambia duración de citas, tarifa base u hora de resumen diario del médico.
    dias_semana: lista de enteros a habilitar/modificar (1=Lunes ... 6=Sábado, 0=Domingo).
    hora_apertura / hora_cierre: formato HH:MM (primer turno o jornada continua).
    hora_apertura_2 / hora_cierre_2: formato HH:MM (segundo turno opcional para jornada partida).
    dias_cerrados: lista de enteros de días que el médico desea cerrar/desactivar por completo
    (1=Lunes ... 6=Sábado, 0=Domingo), sin necesidad de indicar horas.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    parsed_apertura = time.fromisoformat(hora_apertura) if hora_apertura else None
    parsed_cierre = time.fromisoformat(hora_cierre) if hora_cierre else None
    parsed_apertura_2 = time.fromisoformat(hora_apertura_2) if hora_apertura_2 else None
    parsed_cierre_2 = time.fromisoformat(hora_cierre_2) if hora_cierre_2 else None
    parsed_resumen = time.fromisoformat(hora_resumen_diario) if hora_resumen_diario else None
    tarifa_dec = Decimal(str(tarifa_base_consulta)) if tarifa_base_consulta is not None else None

    try:
        perfil = await service_configurar_perfil(
            db=db,
            profesional_usuario_id=ctx.user_id,
            dias_semana=dias_semana,
            hora_apertura=parsed_apertura,
            hora_cierre=parsed_cierre,
            hora_apertura_2=parsed_apertura_2,
            hora_cierre_2=parsed_cierre_2,
            dias_cerrados=dias_cerrados,
            duracion_cita_minutos=duracion_cita_minutos,
            tarifa_base_consulta=tarifa_dec,
            hora_resumen_diario=parsed_resumen,
            alcance_resumen_diario=alcance_resumen_diario,
        )
        return {
            "success": True,
            "code": "PROFILE_UPDATED",
            "duracion_cita_minutos": perfil.duracion_cita_minutos,
            "tarifa_base_consulta": float(perfil.tarifa_base_consulta),
            "hora_resumen_diario": perfil.hora_resumen_diario.strftime("%H:%M"),
            "message": "Configuración de agenda actualizada con éxito.",
        }
    except (EntityNotFoundError, ValidationError) as exc:
        return {"success": False, "code": "UPDATE_ERROR", "message": exc.message}


@require_permission("citas:gestion_manual_medico")
async def agendar_o_mover_cita_manual(
    accion: str,
    fecha_destino: str | None = None,
    hora_destino: str | None = None,
    nombre_paciente: str | None = None,
    telefono_paciente: str | None = None,
    cita_id: str | None = None,
    motivo_consulta: str | None = None,
    monto_personalizado: float | None = None,
) -> dict[str, Any]:
    """
    Permite al médico agendar una cita directa ('CREAR'), mover una existente ('MOVER')
    o cancelarla ('CANCELAR'). En 'CREAR' y 'MOVER' son obligatorios fecha_destino (YYYY-MM-DD)
    y hora_destino (HH:MM). Para 'MOVER' o 'CANCELAR', cita_id puede ser el UUID de la cita
    o el nombre del paciente (o pasarse en nombre_paciente).
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    act = accion.upper()
    if act not in ("CREAR", "MOVER", "CANCELAR"):
        return {"success": False, "code": "INVALID_ACTION", "message": "Acción debe ser 'CREAR', 'MOVER' o 'CANCELAR'"}

    parsed_fecha: date | None = None
    parsed_hora: time | None = None
    if act in ("CREAR", "MOVER"):
        if not (fecha_destino and hora_destino):
            return {
                "success": False,
                "code": "INVALID_DATETIME",
                "message": "fecha_destino y hora_destino son requeridas para crear o mover una cita.",
            }
    try:
        if fecha_destino:
            parsed_fecha = date.fromisoformat(fecha_destino)
        if hora_destino:
            parsed_hora = time.fromisoformat(hora_destino)
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATETIME", "message": str(exc)}

    monto_dec = Decimal(str(monto_personalizado)) if monto_personalizado is not None else None

    try:
        cita = await service_cita_manual(
            db=db,
            profesional_usuario_id=ctx.user_id,
            accion=act,  # type: ignore
            fecha_destino=parsed_fecha,
            hora_destino=parsed_hora,
            nombre_paciente=nombre_paciente,
            telefono_paciente=telefono_paciente,
            cita_id=cita_id,
            motivo_consulta=motivo_consulta,
            monto_personalizado=monto_dec,
        )
        fecha_res = parsed_fecha.isoformat() if parsed_fecha else cita.fecha_hora_inicio.strftime("%Y-%m-%d")
        hora_res = parsed_hora.strftime("%H:%M") if parsed_hora else cita.fecha_hora_inicio.strftime("%H:%M")
        return {
            "success": True,
            "code": f"APPOINTMENT_{act}ED",
            "cita_id": str(cita.id),
            "fecha": fecha_res,
            "hora": hora_res,
            "estado": cita.estado,
            "message": f"Cita {act.lower()} con éxito.",
        }
    except SlotTakenError as exc:
        return {"success": False, "code": "SLOT_TAKEN", "message": exc.message}
    except (EntityNotFoundError, ValidationError, AppError) as exc:
        return {"success": False, "code": "OPERATION_ERROR", "message": exc.message}


@require_permission("finanzas:registrar_pago")
async def registrar_pago_o_descuento(
    cita_id: str,
    monto_pagado: float = 0.0,
    metodo_pago: str | None = None,
    nuevo_monto_acordado: float | None = None,
    referencia: str | None = None,
) -> dict[str, Any]:
    """
    Registra un cobro parcial o total sobre una cita, o ajusta un nuevo monto acordado con descuento.
    'cita_id' puede ser el UUID de la cita o directamente el nombre del paciente (ej. 'Laura').
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    monto_p_dec = Decimal(str(monto_pagado))
    monto_a_dec = Decimal(str(nuevo_monto_acordado)) if nuevo_monto_acordado is not None else None

    try:
        res = await service_registrar_pago(
            db=db,
            profesional_usuario_id=ctx.user_id,
            cita_id=cita_id,
            monto_pagado=monto_p_dec,
            metodo_pago=metodo_pago or "EFECTIVO",
            nuevo_monto_acordado=monto_a_dec,
            referencia=referencia,
        )
        return res
    except (EntityNotFoundError, ValidationError, AppError) as exc:
        return {"success": False, "code": "PAYMENT_ERROR", "message": exc.message}


@require_permission("finanzas:ver_reportes")
async def consultar_resumen_ganancias(
    fecha_inicio: str, fecha_fin: str
) -> dict[str, Any]:
    """
    Calcula el reporte financiero del médico (caja cobrada, pendiente e ingresos proyectados).
    Fechas en formato YYYY-MM-DD.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    try:
        p_inicio = date.fromisoformat(fecha_inicio)
        p_fin = date.fromisoformat(fecha_fin)
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATE", "message": str(exc)}

    try:
        reporte = await service_resumen_ganancias(
            db=db,
            profesional_usuario_id=ctx.user_id,
            fecha_inicio=p_inicio,
            fecha_fin=p_fin,
        )
        return {
            "success": True,
            "periodo": f"{fecha_inicio} a {fecha_fin}",
            "moneda": reporte.moneda,
            "total_cobrado_en_caja": float(reporte.total_cobrado_en_caja),
            "desglose_por_metodo": {k: float(v) for k, v in reporte.desglose_por_metodo.items()},
            "pendiente_por_cobrar_completadas": float(reporte.pendiente_por_cobrar_completadas),
            "proyectado_citas_futuras": float(reporte.proyectado_citas_futuras),
            "total_citas_atendidas": reporte.total_citas_atendidas,
            "total_inasistencias": reporte.total_inasistencias,
        }
    except EntityNotFoundError as exc:
        return {"success": False, "code": "NOT_FOUND", "message": exc.message}

