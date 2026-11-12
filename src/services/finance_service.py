from uuid import UUID
from datetime import date, time, datetime, timezone
from decimal import Decimal
from typing import Any
from collections import defaultdict
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.user import PerfilProfesional
from src.models.appointment import Cita, Pago
from src.schemas.finance import FinancialReportSchema
from src.core.exceptions import EntityNotFoundError, AppError
from src.services.appointment_service import resolver_cita_medico


async def recalcular_estado_financiero_cita(
    db: AsyncSession,
    cita: Cita,
) -> str:
    """
    Recalcula transaccionalmente el estado financiero de una cita según la suma de pagos completados:
    - monto_acordado == 0 -> EXONERADO
    - suma == 0 -> PENDIENTE
    - 0 < suma < monto_acordado -> PARCIAL
    - suma >= monto_acordado -> PAGADO
    """
    stmt = select(func.coalesce(func.sum(Pago.monto_pagado), Decimal("0"))).where(
        Pago.cita_id == cita.id,
        Pago.estado_transaccion == "COMPLETADO",
    )
    res = await db.execute(stmt)
    suma_pagada = Decimal(str(res.scalar_one()))

    if cita.monto_acordado == Decimal("0.00"):
        cita.estado_financiero = "EXONERADO"
    elif suma_pagada == Decimal("0.00"):
        cita.estado_financiero = "PENDIENTE"
    elif suma_pagada < cita.monto_acordado:
        cita.estado_financiero = "PARCIAL"
    else:
        cita.estado_financiero = "PAGADO"

    await db.flush()
    return cita.estado_financiero


async def registrar_pago_o_descuento(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    cita_id: UUID | str,
    monto_pagado: Decimal = Decimal("0.00"),
    metodo_pago: str = "EFECTIVO",
    nuevo_monto_acordado: Decimal | None = None,
    referencia: str | None = None,
) -> dict[str, Any]:
    """
    Registra un abono, un pago total o aplica un descuento modificando monto_acordado,
    recalculando de inmediato el estado_financiero de la cita.
    Acepta cita_id como UUID o nombre del paciente.
    """
    stmt_prof = select(PerfilProfesional).where(PerfilProfesional.usuario_id == profesional_usuario_id)
    res_prof = await db.execute(stmt_prof)
    perfil = res_prof.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    cita = await resolver_cita_medico(db, perfil.id, cita_id)

    # 1. Aplicar descuento sobre monto_acordado si fue especificado
    if nuevo_monto_acordado is not None:
        if nuevo_monto_acordado < Decimal("0.00"):
            raise AppError("El monto acordado no puede ser negativo", code="INVALID_AMOUNT")
        cita.monto_acordado = nuevo_monto_acordado

    pago_creado = None
    # 2. Registrar transacción de pago si el monto es mayor a 0
    if monto_pagado > Decimal("0.00"):
        tipo_pago = "PAGO_FINAL"
        # Calcular si es abono o pago completo
        pagado_previo = sum(
            p.monto_pagado for p in cita.pagos if p.estado_transaccion == "COMPLETADO"
        )
        if (pagado_previo + monto_pagado) < cita.monto_acordado:
            tipo_pago = "ABONO_RESERVA"
        elif pagado_previo == Decimal("0.00") and monto_pagado >= cita.monto_acordado:
            tipo_pago = "PAGO_COMPLETO"

        pago_creado = Pago(
            cita_id=cita.id,
            monto_pagado=monto_pagado,
            moneda=perfil.moneda,
            tipo_pago=tipo_pago,
            metodo_pago=(metodo_pago or "EFECTIVO").upper(),
            referencia_transaccion=referencia,
            estado_transaccion="COMPLETADO",
        )
        db.add(pago_creado)
        await db.flush()

    # 3. Recalcular estado financiero
    nuevo_estado = await recalcular_estado_financiero_cita(db, cita)
    await db.commit()
    await db.refresh(cita)

    # Calcular saldo restante
    stmt_sum = select(func.coalesce(func.sum(Pago.monto_pagado), Decimal("0"))).where(
        Pago.cita_id == cita.id,
        Pago.estado_transaccion == "COMPLETADO",
    )
    total_pagado = Decimal(str((await db.execute(stmt_sum)).scalar_one()))
    saldo_restante = max(Decimal("0.00"), cita.monto_acordado - total_pagado)

    return {
        "success": True,
        "cita_id": str(cita.id),
        "pago_id": str(pago_creado.id) if pago_creado else None,
        "monto_pagado": float(monto_pagado),
        "nuevo_monto_acordado": float(cita.monto_acordado),
        "nuevo_estado_financiero": nuevo_estado,
        "saldo_restante": float(saldo_restante),
    }


async def marcar_pago_completo_rapido(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    cita_id: UUID,
) -> dict[str, Any]:
    """
    Acción directa de botón rápido (PAY_FULL|{cita_id}) ejecutada por el médico.
    Calcula el saldo restante, registra el pago y actualiza la cita a PAGADO.
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

    # Obtener lo que ya ha sido pagado
    stmt_sum = select(func.coalesce(func.sum(Pago.monto_pagado), Decimal("0"))).where(
        Pago.cita_id == cita.id,
        Pago.estado_transaccion == "COMPLETADO",
    )
    pagado_previo = Decimal(str((await db.execute(stmt_sum)).scalar_one()))
    saldo_restante = max(Decimal("0.00"), cita.monto_acordado - pagado_previo)

    if saldo_restante > Decimal("0.00"):
        pago = Pago(
            cita_id=cita.id,
            monto_pagado=saldo_restante,
            moneda=perfil.moneda,
            tipo_pago="PAGO_COMPLETO",
            metodo_pago="EFECTIVO",
            estado_transaccion="COMPLETADO",
        )
        db.add(pago)
        await db.flush()

    cita.estado_financiero = "PAGADO"
    await db.commit()
    await db.refresh(cita)

    return {
        "success": True,
        "cita_id": str(cita.id),
        "monto_completado": float(saldo_restante),
        "nuevo_estado_financiero": "PAGADO",
    }


async def consultar_resumen_ganancias(
    db: AsyncSession,
    profesional_usuario_id: UUID,
    fecha_inicio: date,
    fecha_fin: date,
) -> FinancialReportSchema:
    """
    Calcula el reporte financiero del médico en un periodo: dinero real ingresado en caja,
    saldo pendiente por cobrar de citas completadas e ingresos proyectados de citas futuras.
    """
    stmt_prof = select(PerfilProfesional).where(PerfilProfesional.usuario_id == profesional_usuario_id)
    res_prof = await db.execute(stmt_prof)
    perfil = res_prof.scalars().first()
    if not perfil:
        raise EntityNotFoundError("Perfil profesional")

    inicio_dt = datetime.combine(fecha_inicio, time.min, tzinfo=timezone.utc)
    fin_dt = datetime.combine(fecha_fin, time.max, tzinfo=timezone.utc)

    # 1. Total cobrado en caja en el periodo desglosado por método
    stmt_pagos = (
        select(Pago.metodo_pago, func.sum(Pago.monto_pagado))
        .join(Cita)
        .where(
            Cita.profesional_id == perfil.id,
            Pago.estado_transaccion == "COMPLETADO",
            Pago.fecha_pago >= inicio_dt,
            Pago.fecha_pago <= fin_dt,
        )
        .group_by(Pago.metodo_pago)
    )
    res_pagos = await db.execute(stmt_pagos)
    desglose_por_metodo: dict[str, Decimal] = defaultdict(Decimal)
    total_cobrado = Decimal("0.00")
    for metodo, total in res_pagos.fetchall():
        m_decimal = Decimal(str(total or 0))
        desglose_por_metodo[metodo] = m_decimal
        total_cobrado += m_decimal

    # 2. Citas completadas en el periodo para calcular pendientes
    stmt_completadas = (
        select(Cita)
        .options(selectinload(Cita.pagos))
        .where(
            Cita.profesional_id == perfil.id,
            Cita.estado == "COMPLETADA",
            Cita.fecha_hora_inicio >= inicio_dt,
            Cita.fecha_hora_inicio <= fin_dt,
        )
    )
    res_completadas = await db.execute(stmt_completadas)
    citas_completadas = res_completadas.scalars().all()

    pendiente_completadas = Decimal("0.00")
    for c in citas_completadas:
        pagado = sum(p.monto_pagado for p in c.pagos if p.estado_transaccion == "COMPLETADO")
        if pagado < c.monto_acordado:
            pendiente_completadas += (c.monto_acordado - pagado)

    # 3. Proyectado de citas futuras (CONFIRMADA / CONFIRMADA_PACIENTE)
    stmt_futuras = select(func.coalesce(func.sum(Cita.monto_acordado), Decimal("0"))).where(
        Cita.profesional_id == perfil.id,
        Cita.estado.in_(["CONFIRMADA", "CONFIRMADA_PACIENTE"]),
        Cita.fecha_hora_inicio >= inicio_dt,
        Cita.fecha_hora_inicio <= fin_dt,
    )
    proyectado_futuras = Decimal(str((await db.execute(stmt_futuras)).scalar_one()))

    # 4. Total de inasistencias en el periodo
    stmt_inasistencias = select(func.count(Cita.id)).where(
        Cita.profesional_id == perfil.id,
        Cita.estado == "NO_ASISTIO",
        Cita.fecha_hora_inicio >= inicio_dt,
        Cita.fecha_hora_inicio <= fin_dt,
    )
    total_inasistencias = int((await db.execute(stmt_inasistencias)).scalar_one())

    return FinancialReportSchema(
        success=True,
        periodo=f"{fecha_inicio.isoformat()} a {fecha_fin.isoformat()}",
        moneda=perfil.moneda,
        total_cobrado_en_caja=total_cobrado,
        desglose_por_metodo=dict(desglose_por_metodo),
        pendiente_por_cobrar_completadas=pendiente_completadas,
        proyectado_citas_futuras=proyectado_futuras,
        total_citas_atendidas=len(citas_completadas),
        total_inasistencias=total_inasistencias,
    )

