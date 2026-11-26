from datetime import date, time
from decimal import Decimal
from typing import Any

from src.agent.mcp_tools.registry import (
    current_db_session,
    current_redis_client,
    current_user_ctx,
    require_permission,
)
from src.core.config import get_settings
from src.core.exceptions import (
    AppError,
    EntityNotFoundError,
    PermissionDeniedError,
    SlotTakenError,
    ValidationError,
)
from src.services.admin_service import (
    consultar_metricas_plataforma_admin,
    enviar_comunicado_medicos_admin,
    gestionar_citas_soporte_admin,
    gestionar_codigos_invitacion_admin,
    gestionar_pacientes_admin,
    gestionar_profesionales_admin,
)
from src.services.onboarding_service import (
    cambiar_modo_vista_sesion as service_cambiar_modo,
    generar_codigo_invitacion as service_generar_codigo,
)

settings = get_settings()


@require_permission("admin:generar_invitacion")
async def generar_codigo_invitacion_medico(
    especialidad_asignada: str,
    horas_validez: int = 48,
    frase_personalizada: str | None = None,
) -> dict[str, Any]:
    """
    Genera un código o frase secreta de invitación de un solo uso para que un médico active su cuenta profesional.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    try:
        codigo = await service_generar_codigo(
            db=db,
            admin_id=ctx.user_id,
            especialidad_asignada=especialidad_asignada,
            horas_validez=horas_validez,
            frase_personalizada=frase_personalizada,
        )
        bot_user = settings.TELEGRAM_BOT_USERNAME
        link_tg = f"https://t.me/{bot_user}?start={codigo.frase_codigo}" if bot_user else None

        return {
            "success": True,
            "frase_codigo": codigo.frase_codigo,
            "especialidad": codigo.especialidad_asignada,
            "expira_en": codigo.expira_en.isoformat(),
            "link_directo_telegram": link_tg,
            "message": f"Código generado exitosamente: {codigo.frase_codigo} (Válido por {horas_validez} horas).",
        }
    except (ValidationError, EntityNotFoundError) as exc:
        return {"success": False, "code": "GENERATION_ERROR", "message": exc.message}


@require_permission("admin:generar_invitacion")
async def gestionar_codigos_invitacion(
    accion: str,
    filtro_estado: str = "VIGENTES",
    frase_codigo: str | None = None,
) -> dict[str, Any]:
    """
    Lista códigos de invitación emitidos ('LISTAR', filtrando por 'VIGENTES', 'USADOS' o 'TODOS')
    o anula inmediatamente un código vigente enviado por error ('REVOCAR' pasando frase_codigo).
    """
    db = current_db_session.get()
    act = accion.upper()
    if act not in ("LISTAR", "REVOCAR"):
        return {"success": False, "code": "INVALID_ACTION", "message": "Acción debe ser 'LISTAR' o 'REVOCAR'."}

    filtro = filtro_estado.upper()
    if filtro not in ("VIGENTES", "USADOS", "TODOS"):
        filtro = "VIGENTES"

    try:
        return await gestionar_codigos_invitacion_admin(
            db=db,
            accion=act,  # type: ignore
            filtro_estado=filtro,  # type: ignore
            frase_codigo=frase_codigo,
        )
    except (EntityNotFoundError, ValidationError) as exc:
        return {"success": False, "code": "CODE_ERROR", "message": exc.message}


@require_permission("admin:gestionar_profesionales")
async def listar_y_gestionar_profesionales(
    accion: str,
    profesional_id: str | None = None,
    nuevo_nombre: str | None = None,
    nuevo_telefono: str | None = None,
    nueva_especialidad: str | None = None,
    zona_horaria: str | None = None,
    tarifa_base_consulta: float | None = None,
    duracion_cita_minutos: int | None = None,
    dias_semana: list[int] | None = None,
    hora_apertura: str | None = None,
    hora_cierre: str | None = None,
    forzar: bool = False,
) -> dict[str, Any]:
    """
    Gestiona a los médicos del sistema buscando por nombre, especialidad o UUID (no exige UUID manual):
    'LISTAR', 'DETALLE' (ficha 360°), 'ACTIVAR', 'SUSPENDER', 'EDITAR', 'DAR_DE_BAJA' y 'REACTIVAR_CUENTA'.
    """
    db = current_db_session.get()
    act = accion.upper()
    valid_actions = (
        "LISTAR",
        "DETALLE",
        "ACTIVAR",
        "SUSPENDER",
        "EDITAR",
        "DAR_DE_BAJA",
        "REACTIVAR_CUENTA",
    )
    if act not in valid_actions:
        return {
            "success": False,
            "code": "INVALID_ACTION",
            "message": f"Acción inválida. Usa: {', '.join(valid_actions)}.",
        }

    try:
        parsed_ap = time.fromisoformat(hora_apertura) if hora_apertura else None
        parsed_ci = time.fromisoformat(hora_cierre) if hora_cierre else None
    except ValueError as exc:
        return {"success": False, "code": "INVALID_TIME", "message": str(exc)}

    tarifa_dec = Decimal(str(tarifa_base_consulta)) if tarifa_base_consulta is not None else None

    try:
        return await gestionar_profesionales_admin(
            db=db,
            accion=act,  # type: ignore
            identificador=profesional_id,
            nuevo_nombre=nuevo_nombre,
            nuevo_telefono=nuevo_telefono,
            nueva_especialidad=nueva_especialidad,
            zona_horaria=zona_horaria,
            tarifa_base_consulta=tarifa_dec,
            duracion_cita_minutos=duracion_cita_minutos,
            dias_semana=dias_semana,
            hora_apertura=parsed_ap,
            hora_cierre=parsed_ci,
            forzar=forzar,
        )
    except (EntityNotFoundError, ValidationError, AppError) as exc:
        return {"success": False, "code": "MANAGEMENT_ERROR", "message": exc.message}


@require_permission("admin:gestionar_pacientes")
async def gestionar_pacientes(
    accion: str,
    identificador_paciente: str | None = None,
    nuevo_nombre: str | None = None,
    nuevo_telefono: str | None = None,
    zona_horaria: str | None = None,
) -> dict[str, Any]:
    """
    Directorio e intervención de pacientes: 'BUSCAR' (por nombre, teléfono o chat ID), 'DETALLE' (historial),
    'EDITAR' (datos de contacto/zona horaria), 'BLOQUEAR' (suspende acceso al bot) y 'REACTIVAR'.
    """
    db = current_db_session.get()
    act = accion.upper()
    if act not in ("BUSCAR", "DETALLE", "EDITAR", "BLOQUEAR", "REACTIVAR"):
        return {
            "success": False,
            "code": "INVALID_ACTION",
            "message": "Acción debe ser 'BUSCAR', 'DETALLE', 'EDITAR', 'BLOQUEAR' o 'REACTIVAR'.",
        }

    try:
        return await gestionar_pacientes_admin(
            db=db,
            accion=act,  # type: ignore
            identificador=identificador_paciente,
            nuevo_nombre=nuevo_nombre,
            nuevo_telefono=nuevo_telefono,
            zona_horaria=zona_horaria,
        )
    except (EntityNotFoundError, ValidationError, PermissionDeniedError, AppError) as exc:
        return {"success": False, "code": "PATIENT_MANAGEMENT_ERROR", "message": exc.message}


@require_permission("admin:soporte_citas")
async def gestionar_citas_soporte(
    accion: str,
    cita_id: str | None = None,
    identificador_medico: str | None = None,
    identificador_paciente: str | None = None,
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    nueva_fecha: str | None = None,
    nueva_hora: str | None = None,
    motivo: str | None = None,
) -> dict[str, Any]:
    """
    Mesa de ayuda administrativa sobre citas: 'CONSULTAR', 'CANCELAR_CITA', 'REPROGRAMAR_CITA'
    y 'CANCELAR_JORNADA_MEDICO' (cancela citas de un día completo y notifica automáticamente).
    """
    db = current_db_session.get()
    try:
        redis = current_redis_client.get()
    except LookupError:
        redis = None

    act = accion.upper()
    valid = ("CONSULTAR", "CANCELAR_CITA", "REPROGRAMAR_CITA", "CANCELAR_JORNADA_MEDICO")
    if act not in valid:
        return {"success": False, "code": "INVALID_ACTION", "message": f"Acción inválida: {act}"}

    try:
        p_ini = date.fromisoformat(fecha_inicio) if fecha_inicio else None
        p_fin = date.fromisoformat(fecha_fin) if fecha_fin else None
        p_n_fecha = date.fromisoformat(nueva_fecha) if nueva_fecha else None
        p_n_hora = time.fromisoformat(nueva_hora) if nueva_hora else None
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATETIME", "message": str(exc)}

    try:
        return await gestionar_citas_soporte_admin(
            db=db,
            redis_client=redis,
            accion=act,  # type: ignore
            cita_id=cita_id,
            identificador_medico=identificador_medico,
            identificador_paciente=identificador_paciente,
            fecha_inicio=p_ini,
            fecha_fin=p_fin,
            nueva_fecha=p_n_fecha,
            nueva_hora=p_n_hora,
            motivo=motivo,
        )
    except (EntityNotFoundError, ValidationError, SlotTakenError, AppError) as exc:
        return {"success": False, "code": "SUPPORT_ERROR", "message": exc.message}


@require_permission("admin:ver_metricas")
async def consultar_metricas_plataforma(
    periodo: str = "HOY",
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
) -> dict[str, Any]:
    """
    Obtiene el reporte operativo y financiero global del SaaS: médicos activos vs. onboarding pendiente,
    pacientes activos/bloqueados/nuevos, citas por estado, tasa de inasistencias e ingresos.
    periodo: 'HOY', 'SEMANA', 'MES' o 'PERSONALIZADO' (YYYY-MM-DD en fecha_inicio/fecha_fin).
    """
    db = current_db_session.get()
    per = periodo.upper()
    if per not in ("HOY", "SEMANA", "MES", "PERSONALIZADO"):
        per = "HOY"

    try:
        p_ini = date.fromisoformat(fecha_inicio) if fecha_inicio else None
        p_fin = date.fromisoformat(fecha_fin) if fecha_fin else None
    except ValueError as exc:
        return {"success": False, "code": "INVALID_DATE", "message": str(exc)}

    return await consultar_metricas_plataforma_admin(
        db=db,
        periodo=per,  # type: ignore
        fecha_inicio=p_ini,
        fecha_fin=p_fin,
    )


@require_permission("admin:enviar_comunicados")
async def enviar_comunicado_medicos(
    mensaje: str,
    destinatario: str = "TODOS_ACTIVOS",
) -> dict[str, Any]:
    """
    Envía un comunicado directo por el bot a médicos:
    destinatario puede ser 'TODOS_ACTIVOS', 'ONBOARDING_PENDIENTE' o el nombre/ID de un médico puntual.
    """
    db = current_db_session.get()
    try:
        return await enviar_comunicado_medicos_admin(
            db=db,
            mensaje=mensaje,
            destinatario=destinatario,
        )
    except (EntityNotFoundError, ValidationError) as exc:
        return {"success": False, "code": "BROADCAST_ERROR", "message": exc.message}


@require_permission("admin:cambiar_vista")
async def cambiar_modo_vista_sesion(nuevo_modo: str) -> dict[str, Any]:
    """
    Herramienta exclusiva del SUPERUSUARIO (ADMIN) para alternar su vista a 'ADMIN', 'MEDICO' o 'PACIENTE'
    y simular la experiencia completa de otros roles.
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    modo = nuevo_modo.upper()
    if modo not in ("ADMIN", "MEDICO", "PACIENTE"):
        return {"success": False, "code": "INVALID_MODE", "message": "Modo debe ser 'ADMIN', 'MEDICO' o 'PACIENTE'"}

    try:
        user = await service_cambiar_modo(db=db, usuario_id=ctx.user_id, nuevo_modo=modo)  # type: ignore
        return {
            "success": True,
            "code": "VIEW_MODE_CHANGED",
            "nuevo_modo": user.modo_vista_actual,
            "message": f"Modo de vista cambiado a {user.modo_vista_actual}. Ahora interactúas con la experiencia de {user.modo_vista_actual}.",
        }
    except (PermissionDeniedError, EntityNotFoundError) as exc:
        return {"success": False, "code": "PERMISSION_DENIED", "message": exc.message}
