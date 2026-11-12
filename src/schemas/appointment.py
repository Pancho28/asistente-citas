from uuid import UUID
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class AvailabilityResult(BaseModel):
    success: bool = True
    fecha: str
    profesional_id: UUID
    nombre_profesional: str
    especialidad: str
    tarifa_consulta: Decimal
    moneda: str
    horarios_libres: list[str]
    ui_hint: str = "SHOW_BOOKING_BUTTONS"

    model_config = ConfigDict(from_attributes=True)


class AppointmentDetailSchema(BaseModel):
    cita_id: UUID
    profesional_id: UUID
    nombre_profesional: str
    paciente_id: UUID
    nombre_paciente: str
    fecha_hora_inicio: datetime
    fecha_hora_fin: datetime
    estado: str
    monto_acordado: Decimal
    estado_financiero: str
    motivo_consulta: str | None = None
    direccion_consultorio: str | None = None

    model_config = ConfigDict(from_attributes=True)

