import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.adapters.telegram import TelegramAdapter
from src.adapters.whatsapp import WhatsAppAdapter
from src.core.config import format_wall_time, normalize_timezone_name, now_tz, now_wall_utc
from src.core.exceptions import (
    AppError,
    EntityNotFoundError,
    PermissionDeniedError,
    SlotTakenError,
    ValidationError,
)
from src.core.redis import release_slot_lock
from src.models.appointment import Cita, Pago
from src.models.schedule import BloqueoAgenda
from src.models.user import CodigoInvitacion, PerfilProfesional, Usuario
from src.services.appointment_service import (
    _check_manual_slot_collision,
    _formatear_horario_semanal,
    _notificar_usuario_directo,
    _parse_uuid_safe,
)
from src.services.availability_service import _normalize_text
from src.services.onboarding_service import (
    configurar_horario_base_y_perfil,
    obtener_estado_onboarding_medico,
)

logger = logging.getLogger(__name__)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _select_channel_adapter(usuario: Usuario | None):
    if usuario and usuario.canal_origen == "WHATSAPP":
        return WhatsAppAdapter()
    return TelegramAdapter()


async def resolver_profesional_admin(
    db: AsyncSession,
    identificador: str | UUID,
) -> PerfilProfesional:
    """Resuelve un PerfilProfesional por UUID (perfil/usuario) o por coincidencia flexible de nombre/especialidad."""
    stmt = select(PerfilProfesional).options(
        selectinload(PerfilProfesional.usuario),
        selectinload(PerfilProfesional.horarios_base),
    )
    perfiles = list((await db.execute(stmt)).scalars().all())

    parsed_uuid = _parse_uuid_safe(identificador)
    if parsed_uuid is not None:
        for p in perfiles:
            if p.id == parsed_uuid or p.usuario_id == parsed_uuid:
                return p
        raise EntityNotFoundError("Profesional médico")

    query_str = str(identificador or "").strip()
    if not query_str:
        raise ValidationError("Debes indicar el nombre, especialidad o ID del médico.")

    query_norm = _normalize_text(query_str)
    exactas: list[PerfilProfesional] = []
    parciales: list[PerfilProfesional] = []
    for p in perfiles:
        nombre = _normalize_text(p.usuario.nombre_completo or "") if p.usuario else ""
        esp = _normalize_text(p.especialidad or "")
        if query_norm == nombre or query_norm == esp:
            exactas.append(p)
        elif query_norm in nombre or nombre in query_norm or query_norm in esp:
            parciales.append(p)

    candidatos = exactas if len(exactas) == 1 else (exactas or parciales)
    if not candidatos:
        raise EntityNotFoundError("Profesional médico")
    if len(candidatos) > 1:
        opciones = [
            f"- {p.usuario.nombre_completo if p.usuario else 'Sin nombre'} ({p.especialidad}) | ID: {p.id}"
            for p in candidatos
        ]
        raise ValidationError(
            "Se encontraron varios médicos que coinciden:\n"
            + "\n".join(opciones)
            + "\nIndica el nombre completo o el ID exacto."
        )
    return candidatos[0]


async def resolver_paciente_admin(
    db: AsyncSession,
    identificador: str | UUID,
) -> Usuario:
    """Resuelve un paciente por UUID, chat ID, teléfono o nombre completo."""
    parsed_uuid = _parse_uuid_safe(identificador)
    if parsed_uuid is not None:
        u = (await db.execute(select(Usuario).where(Usuario.id == parsed_uuid))).scalars().first()
        if not u:
            raise EntityNotFoundError("Paciente")
        return u

    query_str = str(identificador or "").strip()
    if not query_str:
        raise ValidationError("Debes indicar el nombre, teléfono, chat ID o UUID del paciente.")

    stmt = select(Usuario).where(Usuario.rol_sistema == "PACIENTE")
    pacientes = list((await db.execute(stmt)).scalars().all())

    query_norm = _normalize_text(query_str)
    exactas: list[Usuario] = []
    parciales: list[Usuario] = []
    for pac in pacientes:
        nombre = _normalize_text(pac.nombre_completo or "")
        tel = (pac.telefono or "").strip()
        chat_id = (pac.external_chat_id or "").strip()
        if query_str in (chat_id, tel) or (nombre and query_norm == nombre):
            exactas.append(pac)
        elif (nombre and (query_norm in nombre or nombre in query_norm)) or (
            tel and query_str in tel
        ):
            parciales.append(pac)

    candidatos = exactas if len(exactas) == 1 else (exactas or parciales)
    if not candidatos:
        raise EntityNotFoundError("Paciente")
    if len(candidatos) > 1:
        opciones = [
            f"- {u.nombre_completo or 'Sin nombre'} (Tel: {u.telefono or 'N/A'} | Chat: {u.external_chat_id} | ID: {u.id})"
            for u in candidatos
        ]
        raise ValidationError(
            "Se encontraron varios pacientes coincidentes:\n"
            + "\n".join(opciones)
            + "\nIndica el teléfono, chat ID o UUID exacto."
        )
    return candidatos[0]


async def gestionar_codigos_invitacion_admin(
    db: AsyncSession,
    accion: Literal["LISTAR", "REVOCAR"],
    filtro_estado: Literal["VIGENTES", "USADOS", "TODOS"] = "VIGENTES",
    frase_codigo: str | None = None,
) -> dict[str, Any]:
    """Lista códigos de invitación emitidos o revoca uno vigente de inmediato."""
    ahora = now_tz()
    if accion == "REVOCAR":
        if not frase_codigo or not frase_codigo.strip():
            raise ValidationError("Debes indicar la frase_codigo a revocar.")
        stmt = select(CodigoInvitacion).where(
            CodigoInvitacion.frase_codigo == frase_codigo.strip().upper()
        )
        codigo = (await db.execute(stmt)).scalars().first()
        if not codigo:
            raise EntityNotFoundError("Código de invitación")
        if codigo.usado:
            raise ValidationError("El código ya fue canjeado y no puede revocarse.")
        codigo.expira_en = ahora - timedelta(seconds=1)
        await db.commit()
        return {
            "success": True,
            "code": "INVITATION_REVOKED",
            "frase_codigo": codigo.frase_codigo,
            "message": f"Código {codigo.frase_codigo} revocado correctamente.",
        }

    stmt_all = select(CodigoInvitacion).order_by(CodigoInvitacion.created_at.desc())
    codigos = list((await db.execute(stmt_all)).scalars().all())
    ahora_utc = _to_utc(ahora)

    resultado: list[dict[str, Any]] = []
    for c in codigos:
        expira_utc = _to_utc(c.expira_en)
        vigente = (not c.usado) and (expira_utc > ahora_utc)
        if filtro_estado == "VIGENTES" and not vigente:
            continue
        if filtro_estado == "USADOS" and not c.usado:
            continue
        estado = "USADO" if c.usado else ("VIGENTE" if vigente else "EXPIRADO")
        resultado.append({
            "frase_codigo": c.frase_codigo,
            "especialidad_asignada": c.especialidad_asignada,
            "estado": estado,
            "expira_en": expira_utc.isoformat(),
            "usado_por_usuario_id": str(c.usado_por_usuario_id) if c.usado_por_usuario_id else None,
        })
    return {
        "success": True,
        "total": len(resultado),
        "codigos": resultado,
    }


async def _build_doctor_summary(
    db: AsyncSession,
    perfil: PerfilProfesional,
    now_ref: datetime,
) -> dict[str, Any]:
    horarios_activos = [hb for hb in perfil.horarios_base if hb.activo]
    citas = list(
        (
            await db.execute(
                select(Cita).where(Cita.profesional_id == perfil.id)
            )
        )
        .scalars()
        .all()
    )
    futuras = sum(
        1
        for c in citas
        if c.estado in ("CONFIRMADA", "CONFIRMADA_PACIENTE")
        and _to_utc(c.fecha_hora_inicio) >= now_ref
    )
    completadas = sum(1 for c in citas if c.estado == "COMPLETADA")
    onboarding = await obtener_estado_onboarding_medico(db, perfil.usuario_id)
    user = perfil.usuario
    return {
        "profesional_id": str(perfil.id),
        "usuario_id": str(perfil.usuario_id),
        "nombre": (user.nombre_completo if user else None) or "Sin nombre",
        "telefono": user.telefono if user else None,
        "especialidad": perfil.especialidad,
        "tarifa_base_consulta": float(perfil.tarifa_base_consulta),
        "moneda": perfil.moneda,
        "duracion_cita_minutos": perfil.duracion_cita_minutos,
        "zona_horaria": onboarding["zona_horaria"],
        "acepta_nuevas_citas": perfil.acepta_nuevas_citas,
        "usuario_activo": bool(user.activo) if user else False,
        "horario_semanal": _formatear_horario_semanal(horarios_activos),
        "citas_futuras_activas": futuras,
        "citas_completadas": completadas,
        "onboarding": onboarding,
    }


async def gestionar_profesionales_admin(
    db: AsyncSession,
    accion: Literal[
        "LISTAR",
        "DETALLE",
        "ACTIVAR",
        "SUSPENDER",
        "EDITAR",
        "DAR_DE_BAJA",
        "REACTIVAR_CUENTA",
    ],
    identificador: str | UUID | None = None,
    nuevo_nombre: str | None = None,
    nuevo_telefono: str | None = None,
    nueva_especialidad: str | None = None,
    zona_horaria: str | None = None,
    tarifa_base_consulta: Decimal | None = None,
    duracion_cita_minutos: int | None = None,
    dias_semana: list[int] | None = None,
    hora_apertura: time | None = None,
    hora_cierre: time | None = None,
    forzar: bool = False,
) -> dict[str, Any]:
    """Gestiona integralmente a los médicos (listado, ficha 360°, edición, suspensión y baja)."""
    ahora_wall = now_wall_utc()
    if accion == "LISTAR":
        stmt = select(PerfilProfesional).options(
            selectinload(PerfilProfesional.usuario),
            selectinload(PerfilProfesional.horarios_base),
        )
        perfiles = list((await db.execute(stmt)).scalars().all())
        lista = []
        for p in perfiles:
            ficha = await _build_doctor_summary(db, p, ahora_wall)
            lista.append({
                "profesional_id": ficha["profesional_id"],
                "nombre": ficha["nombre"],
                "especialidad": ficha["especialidad"],
                "tarifa": ficha["tarifa_base_consulta"],
                "zona_horaria": ficha["zona_horaria"],
                "acepta_nuevas_citas": ficha["acepta_nuevas_citas"],
                "usuario_activo": ficha["usuario_activo"],
                "onboarding_completo": ficha["onboarding"]["onboarding_completo"],
                "citas_futuras_activas": ficha["citas_futuras_activas"],
            })
        return {"success": True, "total": len(lista), "profesionales": lista}

    if not identificador:
        raise ValidationError("Debes indicar el nombre, especialidad o ID del médico.")

    perfil = await resolver_profesional_admin(db, identificador)

    if accion == "DETALLE":
        return {"success": True, "profesional": await _build_doctor_summary(db, perfil, ahora_wall)}

    if accion in ("ACTIVAR", "SUSPENDER"):
        perfil.acepta_nuevas_citas = accion == "ACTIVAR"
        await db.commit()
        await db.refresh(perfil)
        return {
            "success": True,
            "profesional_id": str(perfil.id),
            "acepta_nuevas_citas": perfil.acepta_nuevas_citas,
        }

    if accion == "EDITAR":
        if perfil.usuario and nuevo_nombre:
            perfil.usuario.nombre_completo = nuevo_nombre.strip()
        if perfil.usuario and nuevo_telefono is not None:
            perfil.usuario.telefono = nuevo_telefono.strip()
        if nueva_especialidad:
            perfil.especialidad = nueva_especialidad.strip()
        await db.flush()

        needs_profile_update = any(
            x is not None
            for x in (
                zona_horaria,
                tarifa_base_consulta,
                duracion_cita_minutos,
                dias_semana,
                hora_apertura,
                hora_cierre,
            )
        )
        if needs_profile_update:
            await configurar_horario_base_y_perfil(
                db=db,
                profesional_usuario_id=perfil.usuario_id,
                dias_semana=dias_semana,
                hora_apertura=hora_apertura,
                hora_cierre=hora_cierre,
                duracion_cita_minutos=duracion_cita_minutos,
                tarifa_base_consulta=tarifa_base_consulta,
                zona_horaria=zona_horaria,
            )
        else:
            await db.commit()

        perfil = await resolver_profesional_admin(db, perfil.id)
        return {
            "success": True,
            "code": "DOCTOR_UPDATED",
            "profesional": await _build_doctor_summary(db, perfil, ahora_wall),
        }

    if accion == "DAR_DE_BAJA":
        ficha = await _build_doctor_summary(db, perfil, ahora_wall)
        if ficha["citas_futuras_activas"] > 0 and not forzar:
            return {
                "success": False,
                "code": "HAS_ACTIVE_APPOINTMENTS",
                "citas_futuras": ficha["citas_futuras_activas"],
                "message": (
                    f"El médico tiene {ficha['citas_futuras_activas']} cita(s) futura(s) activa(s). "
                    "Confirma con forzar=True si deseas desactivar su cuenta de todas formas."
                ),
            }
        perfil.acepta_nuevas_citas = False
        if perfil.usuario:
            perfil.usuario.activo = False
        await db.commit()
        return {
            "success": True,
            "code": "DOCTOR_DEACTIVATED",
            "profesional_id": str(perfil.id),
            "usuario_activo": False,
            "acepta_nuevas_citas": False,
        }

    if accion == "REACTIVAR_CUENTA":
        perfil.acepta_nuevas_citas = True
        if perfil.usuario:
            perfil.usuario.activo = True
        await db.commit()
        return {
            "success": True,
            "code": "DOCTOR_REACTIVATED",
            "profesional_id": str(perfil.id),
            "usuario_activo": True,
            "acepta_nuevas_citas": True,
        }

    raise AppError(f"Acción no soportada: {accion}", code="INVALID_ACTION")


async def _serialize_patient_metrics(
    db: AsyncSession,
    pac: Usuario,
    now_ref: datetime,
) -> dict[str, Any]:
    citas = list(
        (await db.execute(select(Cita).where(Cita.paciente_id == pac.id))).scalars().all()
    )
    futuras = sum(
        1
        for c in citas
        if c.estado in ("CONFIRMADA", "CONFIRMADA_PACIENTE")
        and _to_utc(c.fecha_hora_inicio) >= now_ref
    )
    completadas = sum(1 for c in citas if c.estado == "COMPLETADA")
    inasistencias = sum(1 for c in citas if c.estado == "NO_ASISTIO")
    return {
        "usuario_id": str(pac.id),
        "nombre": pac.nombre_completo or "Sin nombre",
        "telefono": pac.telefono,
        "canal_origen": pac.canal_origen,
        "external_chat_id": pac.external_chat_id,
        "zona_horaria": pac.zona_horaria,
        "activo": pac.activo,
        "total_citas": len(citas),
        "futuras": futuras,
        "completadas": completadas,
        "inasistencias": inasistencias,
    }


async def gestionar_pacientes_admin(
    db: AsyncSession,
    accion: Literal["BUSCAR", "DETALLE", "EDITAR", "BLOQUEAR", "REACTIVAR"],
    identificador: str | None = None,
    nuevo_nombre: str | None = None,
    nuevo_telefono: str | None = None,
    zona_horaria: str | None = None,
) -> dict[str, Any]:
    """Busca pacientes, consulta historial, actualiza contacto o bloquea/reactiva acceso al bot."""
    ahora_wall = now_wall_utc()
    if accion == "BUSCAR":
        stmt = (
            select(Usuario)
            .where(Usuario.rol_sistema == "PACIENTE")
            .order_by(Usuario.created_at.desc())
        )
        todos = list((await db.execute(stmt)).scalars().all())
        query_norm = _normalize_text(identificador or "")
        filtrados: list[Usuario] = []
        for u in todos:
            if not query_norm:
                filtrados.append(u)
                continue
            nombre_norm = _normalize_text(u.nombre_completo or "")
            if (
                query_norm in nombre_norm
                or (u.telefono and identificador.strip() in u.telefono)
                or (u.external_chat_id and identificador.strip() in u.external_chat_id)
            ):
                filtrados.append(u)
        resultados = [await _serialize_patient_metrics(db, pac, ahora_wall) for pac in filtrados[:25]]
        return {"success": True, "total": len(resultados), "pacientes": resultados}

    if not identificador:
        raise ValidationError("Debes indicar nombre, teléfono, chat ID o UUID del paciente.")

    pac = await resolver_paciente_admin(db, identificador)
    if pac.rol_sistema == "ADMIN" and accion == "BLOQUEAR":
        raise PermissionDeniedError("No está permitido bloquear a un administrador del sistema.")

    if accion == "DETALLE":
        info = await _serialize_patient_metrics(db, pac, ahora_wall)
        stmt_citas = (
            select(Cita)
            .options(selectinload(Cita.profesional).selectinload(PerfilProfesional.usuario))
            .where(Cita.paciente_id == pac.id)
            .order_by(Cita.fecha_hora_inicio.desc())
            .limit(10)
        )
        ultimas = list((await db.execute(stmt_citas)).scalars().all())
        info["ultimas_citas"] = [
            {
                "cita_id": str(c.id),
                "fecha": format_wall_time(c.fecha_hora_inicio, "%Y-%m-%d"),
                "hora": format_wall_time(c.fecha_hora_inicio, "%H:%M"),
                "estado": c.estado,
                "medico": (
                    c.profesional.usuario.nombre_completo
                    if c.profesional and c.profesional.usuario
                    else "Médico"
                ),
            }
            for c in ultimas
        ]
        return {"success": True, "paciente": info}

    if accion == "EDITAR":
        if nuevo_nombre:
            pac.nombre_completo = nuevo_nombre.strip()
        if nuevo_telefono is not None:
            pac.telefono = nuevo_telefono.strip()
        if zona_horaria is not None:
            try:
                pac.zona_horaria = normalize_timezone_name(zona_horaria)
            except Exception as exc:
                raise ValidationError(f"Zona horaria '{zona_horaria}' inválida.") from exc
        await db.commit()
        await db.refresh(pac)
        return {"success": True, "paciente": await _serialize_patient_metrics(db, pac, ahora_wall)}

    if accion in ("BLOQUEAR", "REACTIVAR"):
        pac.activo = accion == "REACTIVAR"
        await db.commit()
        await db.refresh(pac)
        return {
            "success": True,
            "code": "PATIENT_BLOCKED" if accion == "BLOQUEAR" else "PATIENT_REACTIVATED",
            "paciente": await _serialize_patient_metrics(db, pac, ahora_wall),
        }

    raise AppError(f"Acción inválida: {accion}", code="INVALID_ACTION")


async def gestionar_citas_soporte_admin(
    db: AsyncSession,
    redis_client: Any,
    accion: Literal[
        "CONSULTAR",
        "CANCELAR_CITA",
        "REPROGRAMAR_CITA",
        "CANCELAR_JORNADA_MEDICO",
    ],
    cita_id: str | None = None,
    identificador_medico: str | None = None,
    identificador_paciente: str | None = None,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    nueva_fecha: date | None = None,
    nueva_hora: time | None = None,
    motivo: str | None = None,
) -> dict[str, Any]:
    """Mesa de ayuda administrativa para consultar e intervenir citas o cancelar una jornada médica."""
    perfil = (
        await resolver_profesional_admin(db, identificador_medico)
        if identificador_medico
        else None
    )
    paciente = (
        await resolver_paciente_admin(db, identificador_paciente)
        if identificador_paciente
        else None
    )

    if accion == "CONSULTAR":
        stmt = (
            select(Cita)
            .options(
                selectinload(Cita.paciente),
                selectinload(Cita.profesional).selectinload(PerfilProfesional.usuario),
            )
            .order_by(Cita.fecha_hora_inicio.asc())
        )
        if perfil:
            stmt = stmt.where(Cita.profesional_id == perfil.id)
        if paciente:
            stmt = stmt.where(Cita.paciente_id == paciente.id)
        if fecha_inicio:
            ini_dt = datetime.combine(fecha_inicio, time.min, tzinfo=timezone.utc)
            fin_d = fecha_fin or fecha_inicio
            fin_dt = datetime.combine(fin_d, time.max, tzinfo=timezone.utc)
            stmt = stmt.where(Cita.fecha_hora_inicio >= ini_dt, Cita.fecha_hora_inicio <= fin_dt)

        citas = list((await db.execute(stmt.limit(50))).scalars().all())
        return {
            "success": True,
            "total": len(citas),
            "citas": [
                {
                    "cita_id": str(c.id),
                    "fecha": format_wall_time(c.fecha_hora_inicio, "%Y-%m-%d"),
                    "hora": format_wall_time(c.fecha_hora_inicio, "%H:%M"),
                    "estado": c.estado,
                    "paciente": c.paciente.nombre_completo if c.paciente else "Paciente",
                    "medico": (
                        c.profesional.usuario.nombre_completo
                        if c.profesional and c.profesional.usuario
                        else "Médico"
                    ),
                }
                for c in citas
            ],
        }

    if accion == "CANCELAR_JORNADA_MEDICO":
        if not perfil or not fecha_inicio:
            raise ValidationError(
                "Debes indicar identificador_medico y fecha_inicio (YYYY-MM-DD) para cancelar una jornada."
            )
        ini_dt = datetime.combine(fecha_inicio, time(0, 0, 0), tzinfo=timezone.utc)
        fin_dt = datetime.combine(fecha_inicio, time(23, 59, 59), tzinfo=timezone.utc)
        stmt_dia = (
            select(Cita)
            .options(selectinload(Cita.paciente))
            .where(
                Cita.profesional_id == perfil.id,
                Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]),
                Cita.fecha_hora_inicio >= ini_dt,
                Cita.fecha_hora_inicio <= fin_dt,
            )
        )
        citas_dia = list((await db.execute(stmt_dia)).scalars().all())
        notificados: list[str] = []
        motivo_txt = motivo or "Reorganización administrativa de agenda"

        for c in citas_dia:
            c.estado = "CANCELADA"
            c.estado_financiero = "EXONERADO"
            if redis_client is not None:
                slot_key = format_wall_time(c.fecha_hora_inicio, "%Y-%m-%d_%H:%M")
                await release_slot_lock(redis_client, perfil.id, slot_key, c.paciente_id)
            if c.paciente:
                pac_nom = c.paciente.nombre_completo or "Paciente"
                notificados.append(pac_nom)
                hora_txt = format_wall_time(c.fecha_hora_inicio, "%H:%M")
                await _notificar_usuario_directo(
                    c.paciente,
                    f"⚠️ Tu cita del {fecha_inicio.isoformat()} a las {hora_txt} con "
                    f"{perfil.usuario.nombre_completo if perfil.usuario else 'tu médico'} fue cancelada. Motivo: {motivo_txt}.",
                )

        bloqueo = BloqueoAgenda(
            profesional_id=perfil.id,
            fecha_hora_inicio=ini_dt,
            fecha_hora_fin=fin_dt,
            motivo=motivo_txt,
        )
        db.add(bloqueo)
        await db.commit()
        return {
            "success": True,
            "code": "DOCTOR_DAY_CANCELLED",
            "citas_canceladas": len(citas_dia),
            "pacientes_notificados": notificados,
            "bloqueo_id": str(bloqueo.id),
        }

    # CANCELAR_CITA / REPROGRAMAR_CITA
    parsed_cita_uuid = _parse_uuid_safe(cita_id)
    cita: Cita | None = None
    if parsed_cita_uuid is not None:
        stmt_c = (
            select(Cita)
            .options(
                selectinload(Cita.paciente),
                selectinload(Cita.profesional).selectinload(PerfilProfesional.usuario),
            )
            .where(Cita.id == parsed_cita_uuid)
        )
        cita = (await db.execute(stmt_c)).scalars().first()
    elif paciente or perfil:
        stmt_c = (
            select(Cita)
            .options(
                selectinload(Cita.paciente),
                selectinload(Cita.profesional).selectinload(PerfilProfesional.usuario),
            )
            .where(Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]))
            .order_by(Cita.fecha_hora_inicio.asc())
        )
        if paciente:
            stmt_c = stmt_c.where(Cita.paciente_id == paciente.id)
        if perfil:
            stmt_c = stmt_c.where(Cita.profesional_id == perfil.id)
        cita = (await db.execute(stmt_c)).scalars().first()

    if not cita:
        raise EntityNotFoundError("Cita médica")

    if accion == "CANCELAR_CITA":
        fecha_ant = format_wall_time(cita.fecha_hora_inicio, "%Y-%m-%d")
        hora_ant = format_wall_time(cita.fecha_hora_inicio, "%H:%M")
        cita.estado = "CANCELADA"
        cita.estado_financiero = "EXONERADO"
        if redis_client is not None:
            await release_slot_lock(
                redis_client, cita.profesional_id, f"{fecha_ant}_{hora_ant}", cita.paciente_id
            )
        await db.commit()
        motivo_txt = motivo or "Cancelación gestionada por soporte"
        await _notificar_usuario_directo(
            cita.paciente,
            f"⚠️ Tu cita del {fecha_ant} a las {hora_ant} fue cancelada por administración ({motivo_txt}).",
        )
        if cita.profesional and cita.profesional.usuario:
            await _notificar_usuario_directo(
                cita.profesional.usuario,
                f"ℹ️ Administración canceló la cita de {cita.paciente.nombre_completo if cita.paciente else 'un paciente'} ({fecha_ant} {hora_ant}).",
            )
        return {"success": True, "code": "APPOINTMENT_CANCELLED", "cita_id": str(cita.id)}

    if accion == "REPROGRAMAR_CITA":
        if not (nueva_fecha and nueva_hora):
            raise ValidationError("nueva_fecha y nueva_hora son requeridas para REPROGRAMAR_CITA.")
        prof = cita.profesional
        duracion = prof.duracion_cita_minutos if prof else 30
        slot_start = datetime.combine(nueva_fecha, nueva_hora, tzinfo=timezone.utc)
        slot_end = slot_start + timedelta(minutes=duracion)
        await _check_manual_slot_collision(
            db, cita.profesional_id, slot_start, slot_end, exclude_cita_id=cita.id
        )
        old_slot = format_wall_time(cita.fecha_hora_inicio, "%Y-%m-%d_%H:%M")
        cita.fecha_hora_inicio = slot_start
        cita.fecha_hora_fin = slot_end
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise SlotTakenError() from exc

        if redis_client is not None:
            await release_slot_lock(redis_client, cita.profesional_id, old_slot, cita.paciente_id)

        motivo_txt = motivo or "Reprogramación solicitada a soporte"
        await _notificar_usuario_directo(
            cita.paciente,
            f"🔄 Administración reprogramó tu cita para el {nueva_fecha.isoformat()} a las {nueva_hora.strftime('%H:%M')} ({motivo_txt}).",
        )
        if prof and prof.usuario:
            await _notificar_usuario_directo(
                prof.usuario,
                f"🔄 Administración movió la cita de {cita.paciente.nombre_completo if cita.paciente else 'paciente'} al {nueva_fecha.isoformat()} {nueva_hora.strftime('%H:%M')}.",
            )
        return {
            "success": True,
            "code": "APPOINTMENT_RESCHEDULED",
            "cita_id": str(cita.id),
            "nueva_fecha": nueva_fecha.isoformat(),
            "nueva_hora": nueva_hora.strftime("%H:%M"),
        }

    raise AppError(f"Acción no soportada: {accion}", code="INVALID_ACTION")


async def consultar_metricas_plataforma_admin(
    db: AsyncSession,
    periodo: Literal["HOY", "SEMANA", "MES", "PERSONALIZADO"] = "HOY",
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
) -> dict[str, Any]:
    """Calcula métricas globales operativas y financieras de la plataforma para el ADMIN."""
    hoy = now_tz().date()
    if periodo == "SEMANA":
        d_ini = hoy - timedelta(days=hoy.weekday())
        d_fin = d_ini + timedelta(days=6)
    elif periodo == "MES":
        d_ini = hoy.replace(day=1)
        d_fin = hoy
    elif periodo == "PERSONALIZADO" and fecha_inicio:
        d_ini = fecha_inicio
        d_fin = fecha_fin or fecha_inicio
    else:
        d_ini = hoy
        d_fin = hoy

    ini_dt = datetime.combine(d_ini, time.min, tzinfo=timezone.utc)
    fin_dt = datetime.combine(d_fin, time.max, tzinfo=timezone.utc)

    perfiles = list(
        (
            await db.execute(
                select(PerfilProfesional).options(selectinload(PerfilProfesional.usuario))
            )
        )
        .scalars()
        .all()
    )
    medicos_activos = sum(
        1 for p in perfiles if p.acepta_nuevas_citas and p.usuario and p.usuario.activo
    )
    con_onboarding_pendiente: list[dict[str, Any]] = []
    for p in perfiles:
        onb = await obtener_estado_onboarding_medico(db, p.usuario_id)
        if not onb["onboarding_completo"]:
            con_onboarding_pendiente.append({
                "profesional_id": str(p.id),
                "nombre": p.usuario.nombre_completo if p.usuario else "Sin nombre",
                "pendientes": [x["campo"] for x in onb["pendientes"]],
            })

    pacientes = list(
        (await db.execute(select(Usuario).where(Usuario.rol_sistema == "PACIENTE"))).scalars().all()
    )
    pac_activos = sum(1 for u in pacientes if u.activo)
    pac_bloqueados = sum(1 for u in pacientes if not u.activo)
    pac_nuevos = sum(
        1 for u in pacientes if u.created_at and ini_dt <= _to_utc(u.created_at) <= fin_dt
    )

    citas_rango = list(
        (
            await db.execute(
                select(Cita).where(
                    Cita.fecha_hora_inicio >= ini_dt,
                    Cita.fecha_hora_inicio <= fin_dt,
                )
            )
        )
        .scalars()
        .all()
    )
    estados = {
        "CONFIRMADA": 0,
        "CONFIRMADA_PACIENTE": 0,
        "COMPLETADA": 0,
        "CANCELADA": 0,
        "NO_ASISTIO": 0,
    }
    for c in citas_rango:
        estados[c.estado] = estados.get(c.estado, 0) + 1

    total_citas = len(citas_rango)
    tasa_inasistencia = (
        round((estados["NO_ASISTIO"] / total_citas) * 100, 2) if total_citas else 0.0
    )

    pagos_rango = list(
        (
            await db.execute(
                select(Pago).where(
                    Pago.estado_transaccion == "COMPLETADO",
                    Pago.fecha_pago >= ini_dt,
                    Pago.fecha_pago <= fin_dt,
                )
            )
        )
        .scalars()
        .all()
    )
    total_cobrado = sum((float(p.monto_pagado) for p in pagos_rango), 0.0)
    total_proyectado = sum(
        (
            float(c.monto_acordado)
            for c in citas_rango
            if c.estado in ("CONFIRMADA", "CONFIRMADA_PACIENTE")
        ),
        0.0,
    )

    return {
        "success": True,
        "periodo": f"{d_ini.isoformat()} a {d_fin.isoformat()}",
        "medicos": {
            "total": len(perfiles),
            "activos_aceptando_citas": medicos_activos,
            "suspendidos_o_baja": len(perfiles) - medicos_activos,
            "con_onboarding_pendiente": con_onboarding_pendiente,
        },
        "pacientes": {
            "total": len(pacientes),
            "activos": pac_activos,
            "bloqueados": pac_bloqueados,
            "nuevos_en_periodo": pac_nuevos,
        },
        "citas_en_periodo": {
            "total": total_citas,
            "por_estado": estados,
            "tasa_inasistencia_pct": tasa_inasistencia,
        },
        "finanzas_en_periodo": {
            "total_cobrado": round(total_cobrado, 2),
            "total_proyectado": round(total_proyectado, 2),
        },
    }


async def enviar_comunicado_medicos_admin(
    db: AsyncSession,
    mensaje: str,
    destinatario: str = "TODOS_ACTIVOS",
) -> dict[str, Any]:
    """Envía un comunicado directo por bot a todos los médicos, los de onboarding pendiente o uno puntual."""
    if not mensaje or not mensaje.strip():
        raise ValidationError("El mensaje del comunicado no puede estar vacío.")

    dest_norm = destinatario.strip().upper()
    objetivos: list[PerfilProfesional] = []
    if dest_norm in ("TODOS_ACTIVOS", "ONBOARDING_PENDIENTE"):
        stmt = (
            select(PerfilProfesional)
            .join(Usuario)
            .options(selectinload(PerfilProfesional.usuario))
            .where(Usuario.activo.is_(True))
        )
        todos = list((await db.execute(stmt)).scalars().all())
        if dest_norm == "TODOS_ACTIVOS":
            objetivos = todos
        else:
            for p in todos:
                onb = await obtener_estado_onboarding_medico(db, p.usuario_id)
                if not onb["onboarding_completo"]:
                    objetivos.append(p)
    else:
        objetivos = [await resolver_profesional_admin(db, destinatario)]

    texto_final = f"📢 <b>Aviso de Administración:</b>\n{mensaje.strip()}"
    enviados = 0
    fallidos = 0
    nombres_enviados: list[str] = []

    for p in objetivos:
        u = p.usuario
        if not u or not u.external_chat_id or str(u.external_chat_id).startswith("manual_"):
            fallidos += 1
            continue
        adapter = _select_channel_adapter(u)
        try:
            ok = await adapter.send_message(recipient=u.external_chat_id, text=texto_final)
            if ok:
                enviados += 1
                nombres_enviados.append(u.nombre_completo or "Médico")
            else:
                fallidos += 1
        except Exception as exc:
            logger.warning("[ADMIN BROADCAST] Error enviando aviso a %s: %s", u.id, exc)
            fallidos += 1

    return {
        "success": True,
        "destinatario": destinatario,
        "enviados": enviados,
        "fallidos": fallidos,
        "medicos_notificados": nombres_enviados,
    }
