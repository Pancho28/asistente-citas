import uuid
from uuid import UUID
from datetime import date, time, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Any
from sqlalchemy import select, or_, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from src.models.user import PerfilProfesional, Usuario
from src.models.schedule import HorarioBase, BloqueoAgenda
from src.models.appointment import Cita
from src.core.exceptions import (
    EntityNotFoundError,
    SlotTakenError,
    CancellationWindowExpiredError,
    ValidationError,
    AppError,
)
from src.core.redis import acquire_slot_lock, release_slot_lock
from src.core.config import now_tz
from src.services.availability_service import _normalize_text

DIAS_SEMANA_ORDENADOS: list[tuple[int, str]] = [
    (1, "Lunes"),
    (2, "Martes"),
    (3, "Miércoles"),
    (4, "Jueves"),
    (5, "Viernes"),
    (6, "Sábado"),
    (0, "Domingo"),
]


def _formatear_horario_semanal(horarios_activos: list[HorarioBase]) -> str:
    """Formatea los bloques de HorarioBase activos en un texto legible agrupado por rango de horas."""
    if not horarios_activos:
        return "Sin horario configurado"

    grupos: dict[tuple[str, str], list[str]] = {}
    por_dia = {hb.dia_semana: hb for hb in horarios_activos if hb.activo}
    for dia_num, dia_nombre in DIAS_SEMANA_ORDENADOS:
        hb = por_dia.get(dia_num)
        if hb:
            rango = (hb.hora_inicio.strftime("%H:%M"), hb.hora_fin.strftime("%H:%M"))
            grupos.setdefault(rango, []).append(dia_nombre)

    partes = [
        f"{', '.join(dias)} de {inicio} a {fin}"
        for (inicio, fin), dias in grupos.items()
    ]
    return "; ".join(partes)


def _parse_uuid_safe(value: UUID | str | None) -> UUID | None:
    """Convierte un valor a UUID si tiene formato válido; retorna None si no."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value).strip())
    except ValueError:
        return None


async def _check_manual_slot_collision(
    db: AsyncSession,
    perfil_id: UUID,
    slot_start: datetime,
    slot_end: datetime,
    exclude_cita_id: UUID | None = None,
) -> None:
    """Lanza SlotTakenError si existe otra cita activa solapada en el horario indicado."""
    stmt_conf = select(Cita).where(
        Cita.profesional_id == perfil_id,
        Cita.estado != "CANCELADA",
        Cita.fecha_hora_inicio < slot_end,
        Cita.fecha_hora_fin > slot_start,
    )
    if exclude_cita_id is not None:
        stmt_conf = stmt_conf.where(Cita.id != exclude_cita_id)
    res_conf = await db.execute(stmt_conf)
    if res_conf.scalars().first():
        raise SlotTakenError("El horario seleccionado ya está ocupado por otro paciente en su agenda.")


async def resolver_cita_medico(
    db: AsyncSession,
    perfil_id: UUID,
    cita_id_or_query: UUID | str | None = None,
    nombre_paciente: str | None = None,
) -> Cita:
    """
    Resuelve una cita del médico por UUID exacto o por coincidencia flexible del nombre del paciente.
    Prioriza la cita próxima/reciente sobre citas antiguas.
    """
    parsed_uuid = _parse_uuid_safe(cita_id_or_query)
    if parsed_uuid is not None:
        stmt = (
            select(Cita)
            .options(selectinload(Cita.paciente), selectinload(Cita.pagos))
            .where(Cita.id == parsed_uuid, Cita.profesional_id == perfil_id)
        )
        cita = (await db.execute(stmt)).scalars().first()
        if not cita:
            raise EntityNotFoundError("Cita médica")
        return cita

    query_str = nombre_paciente or (str(cita_id_or_query).strip() if cita_id_or_query else None)
    if not query_str:
        raise AppError("Debe especificar cita_id o nombre_paciente para identificar la cita.", code="INVALID_PARAMS")

    query_norm = _normalize_text(query_str)
    stmt_citas = (
        select(Cita)
        .options(selectinload(Cita.paciente), selectinload(Cita.pagos))
        .where(Cita.profesional_id == perfil_id, Cita.estado != "CANCELADA")
        .order_by(Cita.fecha_hora_inicio.asc())
    )
    citas = list((await db.execute(stmt_citas)).scalars().all())
    coincidencias = []
    for c in citas:
        if not c.paciente:
            continue
        pac_norm = _normalize_text(c.paciente.nombre_completo or "")
        if not pac_norm:
            continue
        if query_norm in pac_norm or pac_norm in query_norm:
            coincidencias.append(c)
    if not coincidencias:
        raise EntityNotFoundError("Cita médica")

    ventana_reciente = now_tz() - timedelta(hours=2)
    proximas = [
        c
        for c in coincidencias
        if (
            c.fecha_hora_inicio.replace(tzinfo=timezone.utc)
            if c.fecha_hora_inicio.tzinfo is None
            else c.fecha_hora_inicio
        )
        >= ventana_reciente
    ]
    return proximas[0] if proximas else coincidencias[-1]


async def agendar_cita_paciente(
    db: AsyncSession,
    redis_client: Any,
    paciente_id: UUID,
    fecha: date,
    hora: time,
    nombre_completo_paciente: str,
    motivo_consulta: str | None = None,
    profesional_id: UUID | str | None = None,
    origen_creacion: Literal["IA_TEXTO", "BOTON_RAPIDO"] = "IA_TEXTO",
) -> Cita:
    """
    Aparta un bloqueo atómico en Redis, valida disponibilidad e inserta la cita en PostgreSQL.
    """
    # 1. Obtener perfil profesional
    stmt = (
        select(PerfilProfesional)
        .join(Usuario)
        .options(selectinload(PerfilProfesional.usuario))
        .where(
            PerfilProfesional.acepta_nuevas_citas.is_(True),
            Usuario.activo.is_(True),
        )
    )
    res = await db.execute(stmt)
    perfiles = list(res.scalars().all())

    perfil: PerfilProfesional | None = None
    if profesional_id:
        prof_raw = str(profesional_id).strip()
        prof_norm = _normalize_text(prof_raw)
        for p in perfiles:
            p_id_str = str(p.id).lower()
            p_user_id_str = str(p.usuario_id).lower()
            p_name_norm = _normalize_text(p.usuario.nombre_completo or "")
            p_esp_norm = _normalize_text(p.especialidad or "")
            if (
                p_id_str == prof_raw.lower()
                or p_id_str.startswith(prof_raw.lower())
                or p_user_id_str == prof_raw.lower()
                or prof_norm in p_name_norm
                or prof_norm in p_esp_norm
                or (p_esp_norm and p_esp_norm in prof_norm)
            ):
                perfil = p
                break
        if not perfil and len(perfiles) == 1:
            # Si solo hay un médico activo y el LLM envió un identificador ambiguo, usar el único activo
            try:
                UUID(prof_raw)
            except ValueError:
                perfil = perfiles[0]
    else:
        perfil = perfiles[0] if perfiles else None

    if not perfil:
        raise EntityNotFoundError("Perfil profesional no encontrado")

    slot_str = hora.strftime("%H:%M")
    slot_start = datetime.combine(fecha, hora, tzinfo=timezone.utc)
    slot_end = slot_start + timedelta(minutes=perfil.duracion_cita_minutos)

    # 1.5 Validar jornada laboral (HorarioBase) si el profesional tiene horarios configurados
    stmt_hb = select(HorarioBase).where(
        HorarioBase.profesional_id == perfil.id,
        HorarioBase.activo.is_(True),
    )
    horarios_activos = list((await db.execute(stmt_hb)).scalars().all())
    if horarios_activos:
        dia_semana_spec = (fecha.weekday() + 1) % 7
        cubre_horario = any(
            hb.dia_semana == dia_semana_spec
            and slot_end.date() == fecha
            and hb.hora_inicio <= hora
            and slot_end.time() <= hb.hora_fin
            for hb in horarios_activos
        )
        if not cubre_horario:
            resumen_horario = _formatear_horario_semanal(horarios_activos)
            raise ValidationError(
                f"El médico no atiende en el día u horario solicitado. "
                f"Su horario general de atención es: {resumen_horario}. "
                f"Infórmale este horario completo al paciente y pregúntale qué día desea consultar "
                f"(no consultes ni sugieras un día específico por tu cuenta)."
            )

    # 2. Bloqueo atómico en Redis (SET NX EX 300)
    slot_key = f"{fecha.isoformat()}_{slot_str}"
    acquired = await acquire_slot_lock(
        redis_client,
        prof_id=perfil.id,
        slot_str=slot_key,
        user_id=paciente_id,
        ttl=300,
    )
    if not acquired:
        raise SlotTakenError()

    # 3. Validar solapamiento con citas existentes no canceladas
    stmt_conflicto = select(Cita).where(
        Cita.profesional_id == perfil.id,
        Cita.estado != "CANCELADA",
        Cita.fecha_hora_inicio < slot_end,
        Cita.fecha_hora_fin > slot_start,
    )
    res_conflicto = await db.execute(stmt_conflicto)
    if res_conflicto.scalars().first():
        raise SlotTakenError()

    # 4. Validar solapamiento con bloqueos de agenda
    stmt_bloqueo = select(BloqueoAgenda).where(
        BloqueoAgenda.profesional_id == perfil.id,
        BloqueoAgenda.fecha_hora_inicio < slot_end,
        BloqueoAgenda.fecha_hora_fin > slot_start,
    )
    res_bloqueo = await db.execute(stmt_bloqueo)
    if res_bloqueo.scalars().first():
        raise SlotTakenError()

    # 5. Actualizar nombre completo del paciente si viene en la solicitud
    stmt_user = select(Usuario).where(Usuario.id == paciente_id)
    res_user = await db.execute(stmt_user)
    usuario_paciente = res_user.scalars().first()
    if usuario_paciente and nombre_completo_paciente:
        usuario_paciente.nombre_completo = nombre_completo_paciente

    # 6. Crear la cita
    nueva_cita = Cita(
        profesional_id=perfil.id,
        paciente_id=paciente_id,
        fecha_hora_inicio=slot_start,
        fecha_hora_fin=slot_end,
        estado="CONFIRMADA",
        monto_acordado=perfil.tarifa_base_consulta,
        estado_financiero="PENDIENTE",
        origen_creacion=origen_creacion,
        motivo_consulta=motivo_consulta,
    )
    db.add(nueva_cita)

    try:
        await db.commit()
        await db.refresh(nueva_cita)
    except IntegrityError:
        await db.rollback()
        raise SlotTakenError()

    return nueva_cita


async def listar_citas_activas_paciente(
    db: AsyncSession,
    paciente_id: UUID,
) -> list[Cita]:
    """
    Lista todas las citas futuras del paciente en estado CONFIRMADA o CONFIRMADA_PACIENTE.
    """
    stmt = (
        select(Cita)
        .options(selectinload(Cita.profesional).selectinload(PerfilProfesional.usuario))
        .where(
            Cita.paciente_id == paciente_id,
            Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]),
            Cita.fecha_hora_inicio >= now_tz(),
        )
        .order_by(Cita.fecha_hora_inicio.asc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def cancelar_o_reagendar_cita_paciente(
    db: AsyncSession,
    redis_client: Any,
    paciente_id: UUID,
    cita_id: UUID | str | None = None,
    accion: Literal["CANCELAR", "REAGENDAR"] = "CANCELAR",
    nueva_fecha: date | None = None,
    nueva_hora: time | None = None,
) -> Cita:
    """
    Cancela o reagenda una cita propia del paciente respetando la ventana de anticipación del médico.
    Si cita_id es None o no es UUID, resuelve automáticamente la próxima cita activa del paciente
    (opcionalmente filtrando por fecha YYYY-MM-DD si viene en cita_id).
    """
    cita: Cita | None = None
    parsed_uuid = _parse_uuid_safe(cita_id)
    if parsed_uuid is not None:
        stmt = (
            select(Cita)
            .options(selectinload(Cita.profesional), selectinload(Cita.paciente))
            .where(Cita.id == parsed_uuid, Cita.paciente_id == paciente_id)
        )
        cita = (await db.execute(stmt)).scalars().first()
    else:
        stmt_activas = (
            select(Cita)
            .options(selectinload(Cita.profesional), selectinload(Cita.paciente))
            .where(
                Cita.paciente_id == paciente_id,
                Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]),
                Cita.fecha_hora_inicio >= now_tz(),
            )
            .order_by(Cita.fecha_hora_inicio.asc())
        )
        citas_activas = list((await db.execute(stmt_activas)).scalars().all())
        explicit_date_requested = False
        if isinstance(cita_id, str) and cita_id.strip():
            try:
                target_date = date.fromisoformat(cita_id.strip()[:10])
                explicit_date_requested = True
                cita = next(
                    (c for c in citas_activas if c.fecha_hora_inicio.date() == target_date),
                    None,
                )
            except ValueError:
                cita = None
        if cita is None and not explicit_date_requested and citas_activas:
            cita = citas_activas[0]

    if not cita:
        raise EntityNotFoundError("Cita médica")

    # Validar ventana de anticipación
    horas_anticipacion = cita.profesional.min_horas_anticipacion_cancelar
    inicio_tz = cita.fecha_hora_inicio
    if inicio_tz.tzinfo is None:
        inicio_tz = inicio_tz.replace(tzinfo=now_tz().tzinfo)
    limite_cancelacion = inicio_tz - timedelta(hours=horas_anticipacion)

    if now_tz() > limite_cancelacion:
        raise CancellationWindowExpiredError()

    if accion == "CANCELAR":
        cita.estado = "CANCELADA"
        cita.estado_financiero = "EXONERADO"
        # Liberar lock en Redis si existiera
        slot_str = cita.fecha_hora_inicio.strftime("%Y-%m-%d_%H:%M")
        await release_slot_lock(redis_client, cita.profesional_id, slot_str, paciente_id)
        await db.commit()
        await db.refresh(cita)
        return cita

    elif accion == "REAGENDAR":
        if not (nueva_fecha and nueva_hora):
            raise AppError("Debe proporcionar nueva_fecha y nueva_hora para reagendar", code="INVALID_PARAMS")

        # Cancelar cita actual y liberar lock en Redis del horario anterior
        slot_str_viejo = cita.fecha_hora_inicio.strftime("%Y-%m-%d_%H:%M")
        nombre_paciente_actual = (cita.paciente.nombre_completo if cita.paciente else None) or "Paciente"
        cita.estado = "CANCELADA"
        cita.estado_financiero = "EXONERADO"
        await release_slot_lock(redis_client, cita.profesional_id, slot_str_viejo, paciente_id)
        await db.flush()

        # Agendar en el nuevo horario conservando el nombre completo del paciente
        nueva_cita = await agendar_cita_paciente(
            db=db,
            redis_client=redis_client,
            paciente_id=paciente_id,
            fecha=nueva_fecha,
            hora=nueva_hora,
            nombre_completo_paciente=nombre_paciente_actual,
            motivo_consulta=cita.motivo_consulta,
            profesional_id=cita.profesional_id,
            origen_creacion="IA_TEXTO",
        )
        return nueva_cita

    raise AppError(f"Acción no soportada: {accion}", code="INVALID_ACTION")


async def bloquear_horario_agenda(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    fecha_hora_inicio: datetime,
    fecha_hora_fin: datetime,
    motivo: str | None = None,
    forzar_con_citas: bool = False,
) -> dict[str, Any]:
    """
    Registra una excepción en BLOQUEOS_AGENDA. Si existen citas confirmadas en el rango,
    advierte al profesional con la lista de pacientes afectados a menos que se fuerce.
    """
    stmt_prof = select(PerfilProfesional).where(PerfilProfesional.usuario_id == profesional_usuario_id)
    res_prof = await db.execute(stmt_prof)
    perfil = res_prof.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    if fecha_hora_inicio.tzinfo is None:
        fecha_hora_inicio = fecha_hora_inicio.replace(tzinfo=timezone.utc)
    if fecha_hora_fin.tzinfo is None:
        fecha_hora_fin = fecha_hora_fin.replace(tzinfo=timezone.utc)

    # Verificar si existen citas afectadas
    stmt_citas = (
        select(Cita)
        .options(selectinload(Cita.paciente))
        .where(
            Cita.profesional_id == perfil.id,
            Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]),
            Cita.fecha_hora_inicio < fecha_hora_fin,
            Cita.fecha_hora_fin > fecha_hora_inicio,
        )
    )
    res_citas = await db.execute(stmt_citas)
    citas_afectadas = res_citas.scalars().all()

    if citas_afectadas and not forzar_con_citas:
        return {
            "success": False,
            "code": "AFFECTED_APPOINTMENTS",
            "mensaje": f"Existen {len(citas_afectadas)} cita(s) agendada(s) en ese rango.",
            "citas_afectadas": [
                {
                    "cita_id": str(c.id),
                    "paciente_nombre": c.paciente.nombre_completo or "Paciente",
                    "fecha_hora_inicio": c.fecha_hora_inicio.isoformat(),
                }
                for c in citas_afectadas
            ],
        }

    bloqueo = BloqueoAgenda(
        profesional_id=perfil.id,
        fecha_hora_inicio=fecha_hora_inicio,
        fecha_hora_fin=fecha_hora_fin,
        motivo=motivo,
    )
    db.add(bloqueo)
    await db.commit()
    await db.refresh(bloqueo)

    return {
        "success": True,
        "code": "SLOT_BLOCKED",
        "bloqueo_id": str(bloqueo.id),
        "motivo": motivo,
    }


async def ver_agenda_profesional(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    fecha_inicio: date,
    fecha_fin: date | None = None,
    filtro_estado: Literal["TODAS", "PENDIENTES_HOY", "COMPLETADAS", "IMPAGAS"] = "TODAS",
) -> list[dict[str, Any]]:
    """
    Obtiene la lista cronológica de citas del médico para un día o rango de fechas.
    """
    stmt_prof = select(PerfilProfesional).where(PerfilProfesional.usuario_id == profesional_usuario_id)
    res_prof = await db.execute(stmt_prof)
    perfil = res_prof.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    fin_fecha = fecha_fin or fecha_inicio
    inicio_dt = datetime.combine(fecha_inicio, time.min, tzinfo=timezone.utc)
    fin_dt = datetime.combine(fin_fecha, time.max, tzinfo=timezone.utc)

    stmt = (
        select(Cita)
        .options(selectinload(Cita.paciente), selectinload(Cita.pagos))
        .where(
            Cita.profesional_id == perfil.id,
            Cita.fecha_hora_inicio >= inicio_dt,
            Cita.fecha_hora_inicio <= fin_dt,
        )
        .order_by(Cita.fecha_hora_inicio.asc())
    )

    if filtro_estado == "PENDIENTES_HOY":
        stmt = stmt.where(Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]))
    elif filtro_estado == "COMPLETADAS":
        stmt = stmt.where(Cita.estado == "COMPLETADA")
    elif filtro_estado == "IMPAGAS":
        stmt = stmt.where(Cita.estado_financiero.in_(["PENDIENTE", "PARCIAL"]))

    res = await db.execute(stmt)
    citas = res.scalars().all()

    agenda: list[dict[str, Any]] = []
    for c in citas:
        monto_pagado = sum(
            p.monto_pagado for p in c.pagos if p.estado_transaccion == "COMPLETADO"
        )
        agenda.append({
            "cita_id": str(c.id),
            "hora_inicio": c.fecha_hora_inicio.strftime("%H:%M"),
            "fecha": c.fecha_hora_inicio.strftime("%Y-%m-%d"),
            "nombre_paciente": c.paciente.nombre_completo or "Paciente",
            "telefono": c.paciente.telefono,
            "motivo_consulta": c.motivo_consulta,
            "estado": c.estado,
            "respuesta_recordatorio": c.respuesta_recordatorio,
            "monto_acordado": float(c.monto_acordado),
            "monto_pagado": float(monto_pagado),
            "estado_financiero": c.estado_financiero,
        })
    return agenda


async def agendar_o_mover_cita_manual_medico(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    accion: Literal["CREAR", "MOVER", "CANCELAR"],
    fecha_destino: date | None = None,
    hora_destino: time | None = None,
    nombre_paciente: str | None = None,
    telefono_paciente: str | None = None,
    cita_id: UUID | str | None = None,
    motivo_consulta: str | None = None,
    monto_personalizado: Decimal | None = None,
) -> Cita:
    """
    Permite al médico agendar una cita manual para un paciente externo ('CREAR'),
    mover una existente ('MOVER') o cancelarla ('CANCELAR'), resolviendo citas
    por UUID o nombre del paciente y previniendo colisiones en su agenda.
    """
    stmt_prof = select(PerfilProfesional).where(PerfilProfesional.usuario_id == profesional_usuario_id)
    res_prof = await db.execute(stmt_prof)
    perfil = res_prof.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    if accion == "CANCELAR":
        cita = await resolver_cita_medico(db, perfil.id, cita_id, nombre_paciente)
        cita.estado = "CANCELADA"
        cita.estado_financiero = "EXONERADO"
        await db.commit()
        await db.refresh(cita)
        return cita

    if not (fecha_destino and hora_destino):
        raise AppError("fecha_destino y hora_destino son requeridas para crear o mover una cita.", code="INVALID_PARAMS")

    slot_start = datetime.combine(fecha_destino, hora_destino, tzinfo=timezone.utc)
    slot_end = slot_start + timedelta(minutes=perfil.duracion_cita_minutos)

    if accion == "MOVER":
        cita = await resolver_cita_medico(db, perfil.id, cita_id, nombre_paciente)
        await _check_manual_slot_collision(db, perfil.id, slot_start, slot_end, exclude_cita_id=cita.id)

        cita.fecha_hora_inicio = slot_start
        cita.fecha_hora_fin = slot_end
        if motivo_consulta:
            cita.motivo_consulta = motivo_consulta
        if monto_personalizado is not None:
            cita.monto_acordado = monto_personalizado

        try:
            await db.commit()
            await db.refresh(cita)
        except IntegrityError:
            await db.rollback()
            raise SlotTakenError("El horario seleccionado ya está ocupado por otro paciente en su agenda.")
        return cita

    elif accion == "CREAR":
        await _check_manual_slot_collision(db, perfil.id, slot_start, slot_end)

        # Buscar o crear usuario paciente manual
        external_id = telefono_paciente or f"manual_{uuid.uuid4().hex[:8]}"
        stmt_u = select(Usuario).where(Usuario.canal_origen == "TELEGRAM", Usuario.external_chat_id == external_id)
        res_u = await db.execute(stmt_u)
        paciente_user = res_u.scalars().first()
        if not paciente_user:
            paciente_user = Usuario(
                canal_origen="TELEGRAM",
                external_chat_id=external_id,
                nombre_completo=nombre_paciente or "Paciente Manual",
                telefono=telefono_paciente,
                rol_sistema="PACIENTE",
                modo_vista_actual="PACIENTE",
            )
            db.add(paciente_user)
            await db.flush()

        monto = monto_personalizado if monto_personalizado is not None else perfil.tarifa_base_consulta

        nueva_cita = Cita(
            profesional_id=perfil.id,
            paciente_id=paciente_user.id,
            fecha_hora_inicio=slot_start,
            fecha_hora_fin=slot_end,
            estado="CONFIRMADA",
            monto_acordado=monto,
            estado_financiero="PENDIENTE",
            origen_creacion="MANUAL_MEDICO",
            motivo_consulta=motivo_consulta,
        )
        db.add(nueva_cita)
        try:
            await db.commit()
            await db.refresh(nueva_cita)
        except IntegrityError:
            await db.rollback()
            raise SlotTakenError("El horario seleccionado ya está ocupado por otro paciente en su agenda.")
        return nueva_cita

    raise AppError(f"Acción manual inválida: {accion}", code="INVALID_ACTION")


async def confirmar_asistencia_recordatorio(
    db: AsyncSession,
    paciente_id: UUID,
    cita_id: UUID,
) -> Cita:
    """
    Confirma la asistencia del paciente tras el recordatorio diario (R_OK).
    """
    stmt = select(Cita).where(Cita.id == cita_id, Cita.paciente_id == paciente_id)
    res = await db.execute(stmt)
    cita = res.scalars().first()
    if not cita:
        raise EntityNotFoundError("Cita médica")

    cita.estado = "CONFIRMADA_PACIENTE"
    cita.respuesta_recordatorio = "CONFIRMO"
    await db.commit()
    await db.refresh(cita)
    return cita


async def marcar_inasistencia_medico(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    cita_id: UUID,
) -> Cita:
    """
    Marca la cita como inasistencia del paciente (NO_SHOW) y exonera el cobro.
    """
    stmt_prof = select(PerfilProfesional).where(PerfilProfesional.usuario_id == profesional_usuario_id)
    res_prof = await db.execute(stmt_prof)
    perfil = res_prof.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    stmt_cita = select(Cita).where(Cita.id == cita_id, Cita.profesional_id == perfil.id)
    res_cita = await db.execute(stmt_cita)
    cita = res_cita.scalars().first()
    if not cita:
        raise EntityNotFoundError("Cita médica")

    cita.estado = "NO_ASISTIO"
    cita.estado_financiero = "EXONERADO"
    await db.commit()
    await db.refresh(cita)
    return cita

