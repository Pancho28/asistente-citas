from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class FinancialReportSchema(BaseModel):
    success: bool = True
    periodo: str
    moneda: str = "USD"
    total_cobrado_en_caja: Decimal
    desglose_por_metodo: dict[str, Decimal]
    pendiente_por_cobrar_completadas: Decimal
    proyectado_citas_futuras: Decimal
    total_citas_atendidas: int
    total_inasistencias: int

    model_config = ConfigDict(from_attributes=True)


class PaymentResponseSchema(BaseModel):
    success: bool = True
    cita_id: UUID
    pago_id: UUID | None = None
    monto_pagado: Decimal
    nuevo_estado_financiero: str
    saldo_restante: Decimal

    model_config = ConfigDict(from_attributes=True)

