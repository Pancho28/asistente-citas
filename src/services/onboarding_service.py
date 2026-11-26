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
    ValidationError,
    AppError,
)
from src.core.config import get_settings, normalize_timezone_name, now_tz


ONBOARDING_STEPS_ORDER: list[tuple[str, str, str]] = [
    (
        "horario_consulta",
        "Días y horario de consulta",
        "¿Qué días de la semana pasas consulta y en qué horario (puedes indicar jornada continua o partida con pausa de almuerzo)?",
    ),
    (
        "duracion_cita",
        "Duración de cada cita",
        "¿Cuántos minutos dura cada consulta (ej. 30, 45 o 60 minutos)?",
    ),
    (
        "tarifa_consulta",
        "Tarifa base de consulta",
        "¿Cuál es el costo o tarifa base de tu consulta?",
    ),
    (
        "notificacion_diaria",
        "Notificación diaria de pacientes",
        "¿A qué hora deseas recibir tu resumen diario de pacientes y si prefieres recibir la agenda del mismo día o del día siguiente?",
    ),
]


def _parse_configured_fields(raw_csv: str | None) -> set[str]:
    if not raw_csv:
        return set()
    return {item.strip() for item in raw_csv.split(",") if item.strip()}


def _serialize_configured_fields(fields: set[str]) -> str:
    ordered = [key for key, _, _ in ONBOARDING_STEPS_ORDER if key in fields]
    return ",".join(ordered)


async def obtener_estado_onboarding_medico(
    db: AsyncSession,
    usuario_id: UUID,
) -> dict[str, Any]:
    """Evalúa individualmente qué configuraciones iniciales faltan por definir en el perfil médico."""
    settings = get_settings()
    stmt = (
        select(PerfilProfesional)
        .options(
            selectinload(PerfilProfesional.horarios_base),
            selectinload(PerfilProfesional.usuario),
        )
        .where(PerfilProfesional.usuario_id == usuario_id)
    )
    perfil = (await db.execute(stmt)).scalars().first()
    if not perfil:
        return {
            "onboarding_completo": True,
            "pendientes": [],
            "zona_horaria": settings.DEFAULT_TIMEZONE,
        }

    campos_guardados = _parse_configured_fields(perfil.onboarding_campos_configurados)
    tiene_horario = any(hb.activo for hb in perfil.horarios_base)
    configurados = {
        "horario_consulta": tiene_horario or ("horario_consulta" in campos_guardados),
        "duracion_cita": "duracion_cita" in campos_guardados,
        "tarifa_consulta": "tarifa_consulta" in campos_guardados,
        "notificacion_diaria": "notificacion_diaria" in campos_guardados,
    }
    pendientes = [
        {"campo": key, "titulo": titulo, "pregunta_amable": pregunta}
        for key, titulo, pregunta in ONBOARDING_STEPS_ORDER
        if not configurados.get(key, False)
    ]
    zona_horaria = (
        perfil.usuario.zona_horaria
        if perfil.usuario and perfil.usuario.zona_horaria
        else settings.DEFAULT_TIMEZONE
    )
    return {
        "onboarding_completo": len(pendientes) == 0,
        "pendientes": pendientes,
        "zona_horaria": zona_horaria,
    }


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
    hora_apertura_2: time | None = None,
    hora_cierre_2: time | None = None,
    dias_cerrados: list[int] | None = None,
    duracion_cita_minutos: int | None = None,
    tarifa_base_consulta: Decimal | None = None,
    hora_resumen_diario: time | None = None,
    alcance_resumen_diario: Literal["AGENDA_HOY", "AGENDA_MANANA"] | None = None,
    zona_horaria: str | None = None,
) -> PerfilProfesional:
    """
    Actualiza la configuración del perfil, zona horaria y jornada semanal base del médico.
    Permite abrir/modificar días con uno o dos tramos (jornada partida con hora_apertura_2/hora_cierre_2)
    y cerrar días completos (dias_cerrados) de forma incremental.
    """
    stmt = (
        select(PerfilProfesional)
        .options(
            selectinload(PerfilProfesional.horarios_base),
            selectinload(PerfilProfesional.usuario),
        )
        .where(PerfilProfesional.usuario_id == profesional_usuario_id)
    )
    res = await db.execute(stmt)
    perfil = res.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    campos = _parse_configured_fields(perfil.onboarding_campos_configurados)

    if zona_horaria is not None:
        try:
            tz_norm = normalize_timezone_name(zona_horaria)
        except Exception as exc:
            raise ValidationError(
                f"Zona horaria '{zona_horaria}' no válida. Usa formato IANA (ej. 'America/Caracas', 'America/Bogota', 'Europe/Madrid')."
            ) from exc
        if perfil.usuario:
            perfil.usuario.zona_horaria = tz_norm

    if duracion_cita_minutos is not None:
        perfil.duracion_cita_minutos = duracion_cita_minutos
        campos.add("duracion_cita")
    if tarifa_base_consulta is not None:
        perfil.tarifa_base_consulta = tarifa_base_consulta
        campos.add("tarifa_consulta")
    if hora_resumen_diario is not None:
        perfil.hora_resumen_diario = hora_resumen_diario
        campos.add("notificacion_diaria")
    if alcance_resumen_diario is not None:
        perfil.alcance_resumen_diario = alcance_resumen_diario
        campos.add("notificacion_diaria")

    # Cerrar días completos sin necesidad de horas de apertura/cierre
    if dias_cerrados:
        for hb in perfil.horarios_base:
            if hb.dia_semana in dias_cerrados:
                hb.activo = False

    # Si se especificaron días y horas de apertura/cierre, actualizar jornada base de forma incremental
    if dias_semana and hora_apertura and hora_cierre:
        campos.add("horario_consulta")
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

            if hora_apertura_2 and hora_cierre_2:
                segundo_hb = HorarioBase(
                    profesional_id=perfil.id,
                    dia_semana=dia,
                    hora_inicio=hora_apertura_2,
                    hora_fin=hora_cierre_2,
                    activo=True,
                )
                db.add(segundo_hb)

    perfil.onboarding_campos_configurados = _serialize_configured_fields(campos)
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

