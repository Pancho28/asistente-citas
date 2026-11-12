import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    String, Boolean, DateTime, Numeric, Text, ForeignKey, Index, text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.models.base import Base


class Cita(Base):
    __tablename__ = "citas"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profesional_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_profesionales.id"), nullable=False)
    paciente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), nullable=False)
    fecha_hora_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_hora_fin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estado: Mapped[str] = mapped_column(String(30), default="CONFIRMADA", nullable=False)
    monto_acordado: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    estado_financiero: Mapped[str] = mapped_column(String(20), default="PENDIENTE", nullable=False)
    origen_creacion: Mapped[str] = mapped_column(String(20), default="IA_TEXTO", nullable=False)
    motivo_consulta: Mapped[str | None] = mapped_column(Text, nullable=True)
    recordatorio_paciente_enviado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recordatorio_enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    respuesta_recordatorio: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    profesional: Mapped["PerfilProfesional"] = relationship("PerfilProfesional", back_populates="citas")
    paciente: Mapped["Usuario"] = relationship("Usuario")
    pagos: Mapped[list["Pago"]] = relationship("Pago", back_populates="cita")

    __table_args__ = (
        Index(
            "idx_cita_unica_activa",
            "profesional_id",
            "fecha_hora_inicio",
            unique=True,
            postgresql_where=text("estado NOT IN ('CANCELADA')"),
            sqlite_where=text("estado NOT IN ('CANCELADA')"),
        ),
    )


class Pago(Base):
    __tablename__ = "pagos"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cita_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("citas.id"), nullable=False)
    monto_pagado: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)
    tipo_pago: Mapped[str] = mapped_column(String(30), nullable=False)  # ABONO_RESERVA | PAGO_FINAL | PAGO_COMPLETO
    metodo_pago: Mapped[str] = mapped_column(String(30), nullable=False)  # EFECTIVO | TRANSFERENCIA | PAGO_MOVIL | ZELLE | TARJETA
    referencia_transaccion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estado_transaccion: Mapped[str] = mapped_column(String(20), default="COMPLETADO", nullable=False)  # COMPLETADO | ANULADO | REEMBOLSADO
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha_pago: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    cita: Mapped["Cita"] = relationship("Cita", back_populates="pagos")

