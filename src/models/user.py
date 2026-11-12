import uuid
from datetime import datetime, time, date
from decimal import Decimal
from sqlalchemy import (
    String, Boolean, DateTime, Time, Date, Numeric, Integer, ForeignKey, Index, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.models.base import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    canal_origen: Mapped[str] = mapped_column(String(20), nullable=False)
    external_chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    nombre_completo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rol_sistema: Mapped[str] = mapped_column(String(20), default="PACIENTE", nullable=False)
    modo_vista_actual: Mapped[str] = mapped_column(String(20), default="PACIENTE", nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    perfil_profesional: Mapped["PerfilProfesional | None"] = relationship(
        "PerfilProfesional",
        back_populates="usuario",
        uselist=False
    )

    __table_args__ = (
        Index("idx_usuario_canal_external_id", "canal_origen", "external_chat_id", unique=True),
    )


class CodigoInvitacion(Base):
    __tablename__ = "codigos_invitacion"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    frase_codigo: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    creado_por_admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), nullable=False)
    especialidad_asignada: Mapped[str] = mapped_column(String(100), nullable=False)
    usado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    usado_por_usuario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canjeado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )


class PerfilProfesional(Base):
    __tablename__ = "perfiles_profesionales"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), unique=True, nullable=False)
    especialidad: Mapped[str] = mapped_column(String(100), nullable=False)
    duracion_cita_minutos: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    tarifa_base_consulta: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    moneda: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)
    min_horas_anticipacion_cancelar: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    direccion_consultorio: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hora_resumen_diario: Mapped[time] = mapped_column(Time, default=time(12, 0), nullable=False)
    alcance_resumen_diario: Mapped[str] = mapped_column(String(20), default="AGENDA_HOY", nullable=False)
    ultimo_resumen_enviado_en: Mapped[date | None] = mapped_column(Date, nullable=True)
    acepta_nuevas_citas: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    usuario: Mapped["Usuario"] = relationship("Usuario", back_populates="perfil_profesional")
    horarios_base: Mapped[list["HorarioBase"]] = relationship("HorarioBase", back_populates="profesional")
    bloqueos: Mapped[list["BloqueoAgenda"]] = relationship("BloqueoAgenda", back_populates="profesional")
    citas: Mapped[list["Cita"]] = relationship("Cita", back_populates="profesional")

