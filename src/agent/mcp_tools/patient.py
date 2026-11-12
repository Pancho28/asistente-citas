from datetime import datetime, date, time
from typing import Any
from uuid import UUID
from src.agent.mcp_tools.registry import (
    require_permission,
    current_user_ctx,
    current_db_session,
    current_redis_client,
)
from src.services.availability_service import consultar_disponibilidad as service_consultar_disp
from src.services.appointment_service import (
    agendar_cita_paciente as service_agendar_cita,
    listar_citas_activas_paciente as service_listar_citas,
    cancelar_o_reagendar_cita_paciente as service_cancelar_reagendar,
    _formatear_horario_semanal,
)
from src.models.schedule import HorarioBase
from sqlalchemy import select
from src.services.onboarding_service import canjear_codigo_invitacion as service_canjear_codigo
from src.core.exceptions import (
    SlotTakenError,
    CancellationWindowExpiredError,
    EntityNotFoundError,
    ValidationError,
    AppError,
)


@require_permission("disponibilidad:consultar")
async def consultar_disponibilidad(
    fecha: str, especialidad: str | None = None, turno_preferido: str = "TODOS"
) -> dict[str, Any]:
    """
    Calcula los horarios y bloques libres para una fecha solicitada (formato YYYY-MM-DD).
    Opcionalmente filtra por especialidad y turno ('MANANA', 'TARDE', 'TODOS').
    """
    db = current_db_session.get()
    redis = current_redis_client.get()
    ctx = current_user_ctx.get()

    try:
        parsed_date = date.fromisoformat(fecha)
    except ValueError:
        return {"success": False, "code": "INVALID_DATE", "message": f"Formato de fecha inválido: {fecha}. Use YYYY-MM-DD."}

    turno = turno_preferido.upper() if turno_preferido.upper() in ("MANANA", "TARDE", "TODOS") else "TODOS"

    try:
        result = await service_consultar_disp(
            db=db,
            redis_client=redis,
            fecha=parsed_date,
            especialidad=especialidad,
            turno_preferido=turno,  # type: ignore
        )
    except EntityNotFoundError as exc:
        return {
            "success": False,
            "code": "NOT_FOUND",
            "fecha": fecha,
            "horarios_libres": [],
            "message": exc.message,
        }

    # Guardar profesional_id en Redis como memoria de contexto para agendar_cita_paciente
    if result.profesional_id and ctx:
        await redis.set(f"chat:last_prof:{ctx.user_id}", str(result.profesional_id), ex=7200)

    if not result.horarios_libres:
        resumen_horario = ""
        if result.profesional_id:
            stmt_hb = select(HorarioBase).where(
                HorarioBase.profesional_id == result.profesional_id,
                HorarioBase.activo.is_(True),
            )
            hbs = list((await db.execute(stmt_hb)).scalars().all())
            if hbs:
                resumen_horario = (
                    f" Su horario general de atención es: {_formatear_horario_semanal(hbs)}. "
                    f"Informa este horario completo al paciente y pregúntale qué día prefiere consultar "
                    f"(no consultes un día específico por tu cuenta)."
                )
        return {
            "success": True,
            "fecha": fecha,
            "profesional_id": str(result.profesional_id) if result.profesional_id else None,
            "nombre_profesional": result.nombre_profesional,
            "especialidad": result.especialidad,
            "horarios_libres": [],
            "message": f"No hay horarios disponibles para el {fecha}.{resumen_horario}",
        }

    return {
        "success": True,
        "fecha": fecha,
        "profesional_id": str(result.profesional_id),
        "nombre_profesional": result.nombre_profesional,
        "especialidad": result.especialidad,
        "tarifa_consulta": float(result.tarifa_consulta),
        "moneda": result.moneda,
        "horarios_libres": result.horarios_libres,
        "ui_hint": "SHOW_BOOKING_BUTTONS",
    }


@require_permission("citas:agendar_propia")
async def agendar_cita_paciente(
    fecha: str,
    hora: str,
    nombre_completo_paciente: str,
    motivo_consulta: str | None = None,
    profesional_id: str | None = None,
) -> dict[str, Any]:
    """
    Agenda una cita médica para el paciente en una fecha (YYYY-MM-DD) y hora (HH:MM).
    REGLA ESTRICTA DE PRIVACIDAD: NUNCA preguntes el motivo de consulta al paciente;
    déjalo vacío o None a menos que el usuario lo haya expresado voluntariamente por su cuenta.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()
    redis = current_redis_client.get()

    try:
        parsed_date = date.fromisoformat(fecha)
        parsed_time = time.fromisoformat(hora)
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATETIME", "message": f"Formato inválido: {exc}"}

    prof_id_or_uuid: UUID | str | None = None
    if profesional_id:
        try:
            prof_id_or_uuid = UUID(profesional_id)
        except ValueError:
            prof_id_or_uuid = profesional_id
    elif ctx:
        last_prof = await redis.get(f"chat:last_prof:{ctx.user_id}")
        if last_prof:
            prof_val = last_prof.decode("utf-8") if isinstance(last_prof, bytes) else str(last_prof)
            try:
                prof_id_or_uuid = UUID(prof_val)
            except ValueError:
                prof_id_or_uuid = prof_val

    try:
        cita = await service_agendar_cita(
            db=db,
            redis_client=redis,
            paciente_id=ctx.user_id,
            fecha=parsed_date,
            hora=parsed_time,
            nombre_completo_paciente=nombre_completo_paciente,
            motivo_consulta=motivo_consulta,
            profesional_id=prof_id_or_uuid,
            origen_creacion="IA_TEXTO",
        )
        return {
            "success": True,
            "code": "APPOINTMENT_CONFIRMED",
            "cita_id": str(cita.id),
            "fecha": parsed_date.isoformat(),
            "hora": parsed_time.strftime("%H:%M"),
            "monto_acordado": float(cita.monto_acordado),
            "estado": cita.estado,
            "message": f"¡Cita agendada con éxito para el {fecha} a las {hora}!",
        }
    except SlotTakenError as exc:
        return {"success": False, "code": "SLOT_TAKEN", "message": exc.message}
    except (ValidationError, EntityNotFoundError) as exc:
        return {"success": False, "code": "VALIDATION_ERROR", "message": exc.message}


@require_permission("citas:ver_propias")
async def ver_mis_citas_activas() -> dict[str, Any]:
    """
    Lista todas las próximas citas activas del paciente (en estado CONFIRMADA o CONFIRMADA_PACIENTE).
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    citas = await service_listar_citas(db=db, paciente_id=ctx.user_id)
    citas_list = []
    for c in citas:
        citas_list.append({
            "cita_id": str(c.id),
            "fecha": c.fecha_hora_inicio.strftime("%Y-%m-%d"),
            "hora": c.fecha_hora_inicio.strftime("%H:%M"),
            "estado": c.estado,
            "estado_financiero": c.estado_financiero,
            "monto_acordado": float(c.monto_acordado),
        })

    return {"success": True, "total_citas": len(citas_list), "citas": citas_list}


@require_permission("citas:modificar_propia")
async def cancelar_o_reagendar_cita_propia(
    accion: str,
    cita_id: str | None = None,
    nueva_fecha: str | None = None,
    nueva_hora: str | None = None,
) -> dict[str, Any]:
    """
    Cancela o reagenda una cita previa del paciente.
    'accion' debe ser 'CANCELAR' o 'REAGENDAR'.
    'cita_id' es opcional (puede ser UUID, fecha YYYY-MM-DD o None para tomar automáticamente la próxima cita activa del paciente).
    Si es 'REAGENDAR', 'nueva_fecha' (YYYY-MM-DD) y 'nueva_hora' (HH:MM) son obligatorias.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()
    redis = current_redis_client.get()

    try:
        parsed_n_fecha = date.fromisoformat(nueva_fecha) if nueva_fecha else None
        parsed_n_hora = time.fromisoformat(nueva_hora) if nueva_hora else None
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATETIME", "message": f"Formato de fecha/hora inválido: {exc}"}

    act = accion.upper()
    if act not in ("CANCELAR", "REAGENDAR"):
        return {"success": False, "code": "INVALID_ACTION", "message": "Acción debe ser 'CANCELAR' o 'REAGENDAR'"}

    try:
        cita = await service_cancelar_reagendar(
            db=db,
            redis_client=redis,
            paciente_id=ctx.user_id,
            cita_id=cita_id,
            accion=act,  # type: ignore
            nueva_fecha=parsed_n_fecha,
            nueva_hora=parsed_n_hora,
        )
        return {
            "success": True,
            "code": f"APPOINTMENT_{act}ED",
            "cita_id": str(cita.id),
            "estado": cita.estado,
            "message": f"Cita {act.lower()} exitosamente.",
        }
    except SlotTakenError as exc:
        return {"success": False, "code": "SLOT_TAKEN", "message": exc.message}
    except CancellationWindowExpiredError as exc:
        return {"success": False, "code": "CANCELLATION_WINDOW_EXPIRED", "message": exc.message}
    except EntityNotFoundError as exc:
        return {"success": False, "code": "NOT_FOUND", "message": exc.message}
    except (ValidationError, AppError) as exc:
        return {"success": False, "code": "VALIDATION_ERROR", "message": exc.message}


@require_permission("onboarding:canjear_codigo")
async def canjear_codigo_invitacion_medico(
    frase_codigo: str, nombre_profesional: str
) -> dict[str, Any]:
    """
    Permite al usuario canjear una frase o código de invitación entregado por el administrador
    para convertirse en MÉDICO y activar su perfil profesional.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    try:
        perfil = await service_canjear_codigo(
            db=db,
            usuario_id=ctx.user_id,
            frase_codigo=frase_codigo,
            nombre_profesional=nombre_profesional,
        )
        return {
            "success": True,
            "code": "CODE_REDEEMED",
            "profesional_id": str(perfil.id),
            "especialidad": perfil.especialidad,
            "message": f"¡Felicidades {nombre_profesional}! Tu cuenta ha sido activada con rol MÉDICO.",
        }
    except (ValidationError, EntityNotFoundError) as exc:
        return {"success": False, "code": "REDEEM_ERROR", "message": exc.message}
