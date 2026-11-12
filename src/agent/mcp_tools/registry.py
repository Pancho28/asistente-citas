import functools
from contextvars import ContextVar
from typing import Callable, Any
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.exceptions import PermissionDeniedError
from src.schemas.context import UserContext, RoleEnum

current_user_ctx: ContextVar[UserContext] = ContextVar("current_user_ctx")
current_db_session: ContextVar[AsyncSession] = ContextVar("current_db_session")
current_redis_client: ContextVar[Any] = ContextVar("current_redis_client")


def require_permission(permission: str):
    """
    Decorador asíncrono (Barrera 2 de Seguridad Post-LLM) que verifica que el
    UserContext activo posea el permiso requerido antes de ejecutar la herramienta.
    Lanza PermissionDeniedError si el usuario no tiene la autorización.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                ctx = current_user_ctx.get()
            except LookupError:
                raise PermissionDeniedError("No active UserContext in request context")

            if not ctx.has_permission(permission):
                raise PermissionDeniedError(
                    f"User {ctx.user_id} with role {ctx.role} lacks required permission: {permission}"
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def get_tools_for_user(ctx: UserContext) -> list[Callable]:
    """
    Barrera 1 de Seguridad Pre-LLM:
    Filtra estrictamente el catálogo de herramientas según el rol efectivo (ctx.role)
    y permisos del usuario autenticado para que Gemini Flash jamás conozca
    herramientas de roles superiores.
    """
    from src.agent.mcp_tools import patient as patient_tools
    from src.agent.mcp_tools import doctor as doctor_tools
    from src.agent.mcp_tools import admin as admin_tools

    if ctx.role == RoleEnum.PACIENTE:
        tools: list[Callable] = [
            patient_tools.consultar_disponibilidad,
            patient_tools.agendar_cita_paciente,
            patient_tools.ver_mis_citas_activas,
            patient_tools.cancelar_o_reagendar_cita_propia,
            patient_tools.canjear_codigo_invitacion_medico,
        ]
        if ctx.has_permission("admin:cambiar_vista"):
            tools.append(admin_tools.cambiar_modo_vista_sesion)
        return tools

    elif ctx.role == RoleEnum.MEDICO:
        tools = [
            doctor_tools.ver_agenda_profesional,
            doctor_tools.bloquear_horario_agenda,
            doctor_tools.configurar_horario_base_y_perfil,
            doctor_tools.agendar_o_mover_cita_manual,
            doctor_tools.registrar_pago_o_descuento,
            doctor_tools.consultar_resumen_ganancias,
        ]
        if ctx.has_permission("admin:cambiar_vista"):
            tools.append(admin_tools.cambiar_modo_vista_sesion)
        return tools

    elif ctx.role == RoleEnum.ADMIN:
        return [
            admin_tools.generar_codigo_invitacion_medico,
            admin_tools.listar_y_gestionar_profesionales,
            admin_tools.cambiar_modo_vista_sesion,
        ]

    return []

