from datetime import datetime, date, time
from typing import Any
from uuid import UUID
from pydantic import BaseModel
from sqlalchemy import select, String, cast
from sqlalchemy.ext.asyncio import AsyncSession
from src.schemas.context import UserContext, RoleEnum
from src.core.exceptions import (
    PermissionDeniedError,
    EntityNotFoundError,
    SlotTakenError,
    CancellationWindowExpiredError,
)
from src.models.user import Usuario, PerfilProfesional
from src.models.appointment import Cita
from src.services.appointment_service import (
    agendar_cita_paciente,
    confirmar_asistencia_recordatorio,
    cancelar_o_reagendar_cita_paciente,
    marcar_inasistencia_medico,
)
from src.services.finance_service import marcar_pago_completo_rapido
from src.services.onboarding_service import cambiar_modo_vista_sesion


class AgentResponse(BaseModel):
    reply_text: str
    ui_hint: str | None = None
    ui_data: dict[str, Any] | None = None


async def try_process_slash_view_command(
    ctx: UserContext, text: str, db: AsyncSession
) -> AgentResponse | None:
    """
    Intercepta comandos rápidos de vista (/modo_admin, /modo_medico, /modo_paciente)
    y los ejecuta en 0 tokens. Verifica en BD que el usuario sea realmente ADMIN.
    """
    clean_text = text.strip().lower()
    command_mode_map = {
        "/modo_admin": "ADMIN",
        "/modo_medico": "MEDICO",
        "/modo_paciente": "PACIENTE",
    }

    if clean_text not in command_mode_map:
        return None

    target_mode = command_mode_map[clean_text]

    # Validar en base de datos que sea un ADMIN real
    user_stmt = select(Usuario).where(Usuario.id == ctx.user_id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()

    if not user or user.rol_sistema != "ADMIN":
        raise PermissionDeniedError(
            "Solo el superusuario con rol ADMIN puede cambiar de modo de vista."
        )

    updated_user = await cambiar_modo_vista_sesion(
        db=db, usuario_id=ctx.user_id, nuevo_modo=target_mode  # type: ignore
    )
    return AgentResponse(
        reply_text=f"Modo de vista cambiado a {updated_user.modo_vista_actual}. Ahora interactúas con la experiencia de {updated_user.modo_vista_actual}."
    )


async def process_fast_route(
    ctx: UserContext, callback_data: str, db: AsyncSession, redis: Any
) -> AgentResponse:
    """
    Ruta Rápida determinista (0 tokens de IA, <50ms de latencia).
    Procesa clics en botones interactivos según los 5 formatos de SPEC.md §6:
    1. BOOK|{prof_short_id}|{YYYYMMDD_HHMM} (Paciente)
    2. R_OK|{cita_id} (Paciente confirmando recordatorio)
    3. R_CAN|{cita_id} (Paciente cancelando desde recordatorio)
    4. PAY_FULL|{cita_id} (Médico cobrando saldo)
    5. NO_SHOW|{cita_id} (Médico registrando inasistencia)
    """
    parts = callback_data.strip().split("|")
    action = parts[0].upper()

    # 1. Agendamiento rápido desde botón de disponibilidad
    if action == "BOOK" and len(parts) >= 3:
        if ctx.role not in (RoleEnum.PACIENTE, RoleEnum.ADMIN):
            raise PermissionDeniedError("Solo pacientes pueden agendar citas.")

        prof_short_id = parts[1]
        dt_str = parts[2]  # YYYYMMDD_HHMM
        try:
            dt = datetime.strptime(dt_str, "%Y%m%d_%H%M")
        except ValueError:
            return AgentResponse(reply_text="Formato de horario de cita inválido en el botón.")

        # Buscar perfil profesional por coincidencia de UUID corto o default
        prof_stmt = select(PerfilProfesional).where(
            cast(PerfilProfesional.id, String).startswith(prof_short_id)
        )
        perfil = (await db.execute(prof_stmt)).scalars().first()

        if not perfil:
            # Fallback al primer médico que acepte nuevas citas
            perfil = (
                await db.execute(
                    select(PerfilProfesional).where(PerfilProfesional.acepta_nuevas_citas == True)
                )
            ).scalars().first()

        if not perfil:
            return AgentResponse(reply_text="No se encontró un profesional médico activo para este horario.")

        # Obtener nombre del paciente
        user_stmt = select(Usuario).where(Usuario.id == ctx.user_id)
        paciente_user = (await db.execute(user_stmt)).scalar_one_or_none()
        nombre_paciente = paciente_user.nombre_completo if paciente_user and paciente_user.nombre_completo else "Paciente"

        try:
            cita = await agendar_cita_paciente(
                db=db,
                redis_client=redis,
                paciente_id=ctx.user_id,
                fecha=dt.date(),
                hora=dt.time(),
                nombre_completo_paciente=nombre_paciente,
                profesional_id=perfil.id,
                origen_creacion="BOTON_RAPIDO",
            )
            return AgentResponse(
                reply_text=f"¡Cita confirmada exitosamente para el {dt.strftime('%d/%m/%Y')} a las {dt.strftime('%H:%M')}!"
            )
        except SlotTakenError as exc:
            return AgentResponse(reply_text=f"Lo sentimos: {exc.message}. Por favor selecciona otro horario.")

    # 2. Confirmación de recordatorio
    elif action == "R_OK" and len(parts) >= 2:
        try:
            cita_uuid = UUID(parts[1])
            await confirmar_asistencia_recordatorio(db=db, paciente_id=ctx.user_id, cita_id=cita_uuid)
            return AgentResponse(
                reply_text="¡Gracias! Tu asistencia a la consulta médica ha sido confirmada."
            )
        except EntityNotFoundError:
            return AgentResponse(reply_text="No se encontró la cita médica indicada.")

    # 3. Cancelación desde recordatorio
    elif action == "R_CAN" and len(parts) >= 2:
        try:
            cita_uuid = UUID(parts[1])
            await cancelar_o_reagendar_cita_paciente(
                db=db,
                redis_client=redis,
                paciente_id=ctx.user_id,
                cita_id=cita_uuid,
                accion="CANCELAR",
            )
            return AgentResponse(
                reply_text="Tu cita ha sido cancelada exitosamente y el horario fue liberado."
            )
        except CancellationWindowExpiredError as exc:
            return AgentResponse(reply_text=exc.message)
        except EntityNotFoundError:
            return AgentResponse(reply_text="No se encontró la cita médica especificada.")

    # 4. Pago completo rápido (Médico)
    elif action == "PAY_FULL" and len(parts) >= 2:
        if ctx.role not in (RoleEnum.MEDICO, RoleEnum.ADMIN):
            raise PermissionDeniedError("Solo médicos pueden registrar pagos.")

        try:
            cita_uuid = UUID(parts[1])
            res = await marcar_pago_completo_rapido(
                db=db, profesional_usuario_id=ctx.user_id, cita_id=cita_uuid
            )
            return AgentResponse(
                reply_text=f"Cita marcada como PAGADA con éxito (Saldo cobrado: ${res['monto_completado']:.2f})."
            )
        except EntityNotFoundError:
            return AgentResponse(reply_text="No se encontró la cita especificada.")

    # 5. Inasistencia médica (Médico)
    elif action == "NO_SHOW" and len(parts) >= 2:
        if ctx.role not in (RoleEnum.MEDICO, RoleEnum.ADMIN):
            raise PermissionDeniedError("Solo médicos pueden marcar inasistencias.")

        try:
            cita_uuid = UUID(parts[1])
            await marcar_inasistencia_medico(
                db=db, profesional_usuario_id=ctx.user_id, cita_id=cita_uuid
            )
            return AgentResponse(
                reply_text="Cita marcada como NO ASISTIÓ y estado financiero exonerado."
            )
        except EntityNotFoundError:
            return AgentResponse(reply_text="No se encontró la cita especificada.")

    return AgentResponse(reply_text="Acción interactiva no reconocida.")
