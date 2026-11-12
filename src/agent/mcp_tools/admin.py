from typing import Any
from uuid import UUID
from src.agent.mcp_tools.registry import (
    require_permission,
    current_user_ctx,
    current_db_session,
)
from src.services.onboarding_service import (
    generar_codigo_invitacion as service_generar_codigo,
    listar_y_gestionar_profesionales as service_gestionar_profesionales,
    cambiar_modo_vista_sesion as service_cambiar_modo,
)
from src.core.exceptions import EntityNotFoundError, ValidationError, PermissionDeniedError
from src.core.config import get_settings

settings = get_settings()


@require_permission("admin:generar_invitacion")
async def generar_codigo_invitacion_medico(
    especialidad_asignada: str,
    horas_validez: int = 48,
    frase_personalizada: str | None = None,
) -> dict[str, Any]:
    """
    Genera un código o frase secreta de invitación para que un médico active su cuenta profesional.
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


@require_permission("admin:gestionar_profesionales")
async def listar_y_gestionar_profesionales(
    accion: str, profesional_id: str | None = None
) -> dict[str, Any]:
    """
    Permite listar todos los médicos del sistema ('LISTAR') o cambiar su disponibilidad ('ACTIVAR', 'SUSPENDER').
    """
    ctx = current_user_ctx.get()
    db = current_db_session.get()

    act = accion.upper()
    if act not in ("LISTAR", "ACTIVAR", "SUSPENDER"):
        return {"success": False, "code": "INVALID_ACTION", "message": "Acción debe ser 'LISTAR', 'ACTIVAR' o 'SUSPENDER'"}

    try:
        prof_uuid = UUID(profesional_id) if profesional_id else None
    except ValueError:
        return {"success": False, "code": "INVALID_UUID", "message": f"ID de profesional inválido: {profesional_id}"}

    try:
        res = await service_gestionar_profesionales(
            db=db,
            admin_id=ctx.user_id,
            accion=act,  # type: ignore
            profesional_id=prof_uuid,
        )
        return res
    except (EntityNotFoundError, ValidationError) as exc:
        return {"success": False, "code": "MANAGEMENT_ERROR", "message": exc.message}


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

