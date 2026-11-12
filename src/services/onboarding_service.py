from uuid import UUID
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Literal, Any
import secrets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.user import Usuario, PerfilProfesional, CodigoInvitacion
from src.models.schedule import HorarioBase
from src.core.exceptions import (
    EntityNotFoundError,
    InvalidInvitationCodeError,
    PermissionDeniedError,
    AppError,
)
from src.core.config import now_tz


async def generar_codigo_invitacion(
    db: AsyncSession,
    admin_id: UUID,
    especialidad_asignada: str,
    horas_validez: int = 48,
    frase_personalizada: str | None = None,
) -> CodigoInvitacion:
    """
    Genera una frase o código único de un solo uso para que un nuevo profesional se registre.
    """
    if frase_personalizada:
        frase = frase_personalizada.strip().upper()
    else:
        esp_tag = especialidad_asignada[:4].strip().upper()
        random_num = secrets.randbelow(9000) + 1000
        frase = f"MED-{esp_tag}-{random_num}"

    expira_en = now_tz() + timedelta(hours=horas_validez)

    codigo = CodigoInvitacion(
        frase_codigo=frase,
        creado_por_admin_id=admin_id,
        especialidad_asignada=especialidad_asignada,
        usado=False,
        expira_en=expira_en,
    )
    db.add(codigo)
    await db.commit()
    await db.refresh(codigo)
    return codigo


async def canjear_codigo_invitacion(
    db: AsyncSession,
    usuario_id: UUID,
    frase_codigo: str,
    nombre_profesional: str,
) -> PerfilProfesional:
    """
    Canjea un código de invitación válido, promoviendo al usuario a MEDICO y creando su perfil.
    """
    stmt_codigo = select(CodigoInvitacion).where(
        CodigoInvitacion.frase_codigo == frase_codigo.strip().upper(),
        CodigoInvitacion.usado.is_(False),
        CodigoInvitacion.expira_en > now_tz(),
    )
    res_codigo = await db.execute(stmt_codigo)
    codigo = res_codigo.scalars().first()
    if not codigo:
        raise InvalidInvitationCodeError()

    stmt_usuario = select(Usuario).where(Usuario.id == usuario_id)
    res_usuario = await db.execute(stmt_usuario)
    usuario = res_usuario.scalars().first()
    if not usuario:
        raise EntityNotFoundError("Usuario")

    # Promover a MEDICO
    usuario.rol_sistema = "MEDICO"
    usuario.modo_vista_actual = "MEDICO"
    usuario.nombre_completo = nombre_profesional

    # Crear PerfilProfesional inicial
    perfil = PerfilProfesional(
        usuario_id=usuario.id,
        especialidad=codigo.especialidad_asignada,
        duracion_cita_minutos=30,
        tarifa_base_consulta=Decimal("50.00"),
        moneda="USD",
        min_horas_anticipacion_cancelar=2,
        acepta_nuevas_citas=True,
    )
    db.add(perfil)

    # Marcar código como usado
    codigo.usado = True
    codigo.usado_por_usuario_id = usuario.id
    codigo.canjeado_en = now_tz()

    await db.commit()
    await db.refresh(perfil)
    return perfil


async def configurar_horario_base_y_perfil(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    dias_semana: list[int] | None = None,
    hora_apertura: time | None = None,
    hora_cierre: time | None = None,
    duracion_cita_minutos: int | None = None,
    tarifa_base_consulta: Decimal | None = None,
    hora_resumen_diario: time | None = None,
    alcance_resumen_diario: Literal["AGENDA_HOY", "AGENDA_MANANA"] | None = None,
) -> PerfilProfesional:
    """
    Actualiza la configuración del perfil y la jornada semanal base del médico.
    """
    stmt = (
        select(PerfilProfesional)
        .options(selectinload(PerfilProfesional.horarios_base))
        .where(PerfilProfesional.usuario_id == profesional_usuario_id)
    )
    res = await db.execute(stmt)
    perfil = res.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    if duracion_cita_minutos is not None:
        perfil.duracion_cita_minutos = duracion_cita_minutos
    if tarifa_base_consulta is not None:
        perfil.tarifa_base_consulta = tarifa_base_consulta
    if hora_resumen_diario is not None:
        perfil.hora_resumen_diario = hora_resumen_diario
    if alcance_resumen_diario is not None:
        perfil.alcance_resumen_diario = alcance_resumen_diario

    # Si se especificaron días y horas de apertura/cierre, actualizar jornada base de forma incremental
    if dias_semana and hora_apertura and hora_cierre:
        # Desactivar únicamente los horarios de los días indicados en dias_semana
        for hb in perfil.horarios_base:
            if hb.dia_semana in dias_semana:
                hb.activo = False

        for dia in dias_semana:
            nuevo_hb = HorarioBase(
                profesional_id=perfil.id,
                dia_semana=dia,
                hora_inicio=hora_apertura,
                hora_fin=hora_cierre,
                activo=True,
            )
            db.add(nuevo_hb)

    await db.commit()
    await db.refresh(perfil)
    return perfil


async def listar_y_gestionar_profesionales(
    db: AsyncSession,
    admin_id: UUID,
    accion: Literal["LISTAR", "ACTIVAR", "SUSPENDER"],
    profesional_id: UUID | None = None,
) -> dict[str, Any]:
    """
    Permite al administrador listar o suspender/activar a los profesionales médicos.
    """
    if accion == "LISTAR":
        stmt = select(PerfilProfesional).options(selectinload(PerfilProfesional.usuario))
        res = await db.execute(stmt)
        profesionales = res.scalars().all()
        return {
            "success": True,
            "profesionales": [
                {
                    "profesional_id": str(p.id),
                    "nombre": p.usuario.nombre_completo or "Sin nombre",
                    "especialidad": p.especialidad,
                    "tarifa": float(p.tarifa_base_consulta),
                    "acepta_nuevas_citas": p.acepta_nuevas_citas,
                }
                for p in profesionales
            ],
        }

    if not profesional_id:
        raise AppError("profesional_id es requerido para ACTIVAR o SUSPENDER", code="INVALID_PARAMS")

    stmt_p = select(PerfilProfesional).where(PerfilProfesional.id == profesional_id)
    res_p = await db.execute(stmt_p)
    perfil = res_p.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    if accion == "ACTIVAR":
        perfil.acepta_nuevas_citas = True
    elif accion == "SUSPENDER":
        perfil.acepta_nuevas_citas = False

    await db.commit()
    await db.refresh(perfil)
    return {
        "success": True,
        "profesional_id": str(perfil.id),
        "acepta_nuevas_citas": perfil.acepta_nuevas_citas,
    }


async def cambiar_modo_vista_sesion(
    db: AsyncSession,
    usuario_id: UUID,
    nuevo_modo: Literal["ADMIN", "MEDICO", "PACIENTE"],
) -> Usuario:
    """
    Permite EXCLUSIVAMENTE a los usuarios con rol_sistema == 'ADMIN' en base de datos
    modificar su campo modo_vista_actual para simular la experiencia de otros roles.
    """
    stmt = select(Usuario).where(Usuario.id == usuario_id)
    res = await db.execute(stmt)
    usuario = res.scalars().first()
    if not usuario:
        raise EntityNotFoundError("Usuario")

    # REGLA CRÍTICA DE SEGURIDAD (SPEC.md §3.2 y AGENTS.md §6.4)
    if usuario.rol_sistema != "ADMIN":
        raise PermissionDeniedError(
            "Exclusividad de cambio de vista: Solo los usuarios con rol_sistema == 'ADMIN' pueden cambiar de vista"
        )

    usuario.modo_vista_actual = nuevo_modo
    await db.commit()
    await db.refresh(usuario)
    return usuario

