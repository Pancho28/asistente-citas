import unicodedata
from uuid import UUID
from datetime import date, time, datetime, timedelta, timezone
from typing import Literal, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.user import PerfilProfesional, Usuario
from src.models.schedule import HorarioBase, BloqueoAgenda
from src.models.appointment import Cita
from src.schemas.appointment import AvailabilityResult
from src.core.exceptions import EntityNotFoundError
from src.core.config import now_tz


def _normalize_text(value: str) -> str:
    """Elimina tildes/acentos y convierte a minúsculas para comparaciones flexibles."""
    return (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )


def _ensure_utc(dt: datetime) -> datetime:
    """Garantiza que el datetime tenga tzinfo=timezone.utc para comparaciones seguras."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def listar_catalogo_medicos_activos(db: AsyncSession) -> list[dict[str, Any]]:
    """
    Retorna la lista de médicos activos que aceptan nuevas citas (nombre, especialidad,
    tarifa_consulta, moneda y profesional_id) para inyectar en el contexto del paciente.
    """
    stmt = (
        select(PerfilProfesional)
        .join(Usuario)
        .options(selectinload(PerfilProfesional.usuario))
        .where(
            PerfilProfesional.acepta_nuevas_citas.is_(True),
            Usuario.activo.is_(True),
        )
        .order_by(PerfilProfesional.especialidad.asc())
    )
    res = await db.execute(stmt)
    perfiles = res.scalars().all()

    catalogo: list[dict[str, Any]] = []
    for p in perfiles:
        catalogo.append({
            "profesional_id": str(p.id),
            "nombre_profesional": p.usuario.nombre_completo or "Profesional Médico",
            "especialidad": p.especialidad,
            "tarifa_consulta": float(p.tarifa_base_consulta),
            "moneda": p.moneda,
        })
    return catalogo


async def consultar_disponibilidad(
    db: AsyncSession,
    redis_client: Any,
    fecha: date,
    especialidad: str | None = None,
    turno_preferido: Literal["MANANA", "TARDE", "TODOS"] = "TODOS",
    profesional_id: UUID | None = None,
) -> AvailabilityResult:
    """
    Calcula los bloques horarios libres cruzando en memoria la jornada semanal recurrente
    (HORARIOS_BASE) menos excepciones (BLOQUEOS_AGENDA), citas activas (CITAS)
    y bloqueos atómicos temporales en Redis.
    """
    # 1. Resolver el profesional médico activo
    stmt = (
        select(PerfilProfesional)
        .join(Usuario)
        .options(selectinload(PerfilProfesional.usuario))
        .where(PerfilProfesional.acepta_nuevas_citas.is_(True))
    )
    if profesional_id:
        stmt = stmt.where(PerfilProfesional.id == profesional_id)

    res = await db.execute(stmt)
    perfiles = list(res.scalars().all())

    perfil: PerfilProfesional | None = None
    if profesional_id:
        perfil = perfiles[0] if perfiles else None
    elif especialidad:
        norm_esp = _normalize_text(especialidad)
        for p in perfiles:
            if norm_esp in _normalize_text(p.especialidad or "") or norm_esp in _normalize_text(
                p.usuario.nombre_completo or ""
            ):
                perfil = p
                break
    else:
        perfil = perfiles[0] if perfiles else None

    if not perfil:
        raise EntityNotFoundError("Profesional médico disponible")

    # 2. Convertir día de la semana (SPEC: 0=Dom, 1=Lun ... 6=Sab; Python: 0=Lun ... 6=Dom)
    dia_semana_spec = (fecha.weekday() + 1) % 7

    # 3. Obtener jornadas base del profesional para este día de la semana
    stmt_horarios = select(HorarioBase).where(
        HorarioBase.profesional_id == perfil.id,
        HorarioBase.dia_semana == dia_semana_spec,
        HorarioBase.activo.is_(True),
    )
    res_horarios = await db.execute(stmt_horarios)
    horarios_base = res_horarios.scalars().all()

    if not horarios_base:
        return AvailabilityResult(
            fecha=fecha.isoformat(),
            profesional_id=perfil.id,
            nombre_profesional=perfil.usuario.nombre_completo or "Profesional Médico",
            especialidad=perfil.especialidad,
            tarifa_consulta=perfil.tarifa_base_consulta,
            moneda=perfil.moneda,
            horarios_libres=[],
        )

    # 4. Generar todos los bloques potenciales según duracion_cita_minutos
    duracion = timedelta(minutes=perfil.duracion_cita_minutos)
    bloques_potenciales: list[tuple[time, datetime, datetime]] = []

    for hb in horarios_base:
        current_dt = datetime.combine(fecha, hb.hora_inicio, tzinfo=timezone.utc)
        end_dt = datetime.combine(fecha, hb.hora_fin, tzinfo=timezone.utc)

        while current_dt + duracion <= end_dt:
            slot_end = current_dt + duracion
            bloques_potenciales.append((current_dt.time(), current_dt, slot_end))
            current_dt += duracion

    if not bloques_potenciales:
        return AvailabilityResult(
            fecha=fecha.isoformat(),
            profesional_id=perfil.id,
            nombre_profesional=perfil.usuario.nombre_completo or "Profesional Médico",
            especialidad=perfil.especialidad,
            tarifa_consulta=perfil.tarifa_base_consulta,
            moneda=perfil.moneda,
            horarios_libres=[],
        )

    # 5. Obtener citas activas que se solapan con la fecha solicitada
    inicio_dia = datetime.combine(fecha, time.min, tzinfo=timezone.utc)
    fin_dia = datetime.combine(fecha, time.max, tzinfo=timezone.utc)

    stmt_citas = select(Cita).where(
        Cita.profesional_id == perfil.id,
        Cita.estado != "CANCELADA",
        Cita.fecha_hora_inicio < fin_dia,
        Cita.fecha_hora_fin > inicio_dia,
    )
    res_citas = await db.execute(stmt_citas)
    citas_activas = res_citas.scalars().all()

    # 6. Obtener bloqueos de agenda que se solapan con la fecha solicitada
    stmt_bloqueos = select(BloqueoAgenda).where(
        BloqueoAgenda.profesional_id == perfil.id,
        BloqueoAgenda.fecha_hora_inicio < fin_dia,
        BloqueoAgenda.fecha_hora_fin > inicio_dia,
    )
    res_bloqueos = await db.execute(stmt_bloqueos)
    bloqueos_agenda = res_bloqueos.scalars().all()

    # 7. Filtrar bloques libres restando Citas, Bloqueos y Redis Locks
    horarios_libres: list[str] = []
    current_time_tz = now_tz()

    for slot_time, slot_start, slot_end in bloques_potenciales:
        slot_str = slot_time.strftime("%H:%M")

        # Filtro de turno preferido
        if turno_preferido == "MANANA" and not (time(6, 0) <= slot_time < time(12, 0)):
            continue
        if turno_preferido == "TARDE" and not (time(12, 0) <= slot_time < time(19, 0)):
            continue

        # Si la fecha es hoy, descartar bloques pasados respecto a la hora actual
        if fecha == current_time_tz.date() and slot_time <= current_time_tz.time():
            continue

        # Verificar solapamiento con citas existentes
        solapa_cita = any(
            _ensure_utc(cita.fecha_hora_inicio) < slot_end and _ensure_utc(cita.fecha_hora_fin) > slot_start
            for cita in citas_activas
        )
        if solapa_cita:
            continue

        # Verificar solapamiento con bloqueos de agenda
        solapa_bloqueo = any(
            _ensure_utc(bloqueo.fecha_hora_inicio) < slot_end and _ensure_utc(bloqueo.fecha_hora_fin) > slot_start
            for bloqueo in bloqueos_agenda
        )
        if solapa_bloqueo:
            continue

        # Verificar si existe un lock atómico temporal en Redis
        redis_key = f"lock:slot:{perfil.id}:{fecha.isoformat()}_{slot_str}"
        holder = await redis_client.get(redis_key)
        if holder is not None:
            continue

        horarios_libres.append(slot_str)

    return AvailabilityResult(
        fecha=fecha.isoformat(),
        profesional_id=perfil.id,
        nombre_profesional=perfil.usuario.nombre_completo or "Profesional Médico",
        especialidad=perfil.especialidad,
        tarifa_consulta=perfil.tarifa_base_consulta,
        moneda=perfil.moneda,
        horarios_libres=horarios_libres,
    )

