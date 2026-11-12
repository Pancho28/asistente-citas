import uuid
from datetime import datetime, time
from sqlalchemy import (
    String, Boolean, DateTime, Time, SmallInteger, ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.models.base import Base


class HorarioBase(Base):
    __tablename__ = "horarios_base"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profesional_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_profesionales.id"), nullable=False)
    dia_semana: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=Dom, 1=Lun ... 6=Sab
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    profesional: Mapped["PerfilProfesional"] = relationship("PerfilProfesional", back_populates="horarios_base")


class BloqueoAgenda(Base):
    __tablename__ = "bloqueos_agenda"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profesional_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("perfiles_profesionales.id"), nullable=False)
    fecha_hora_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_hora_fin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    profesional: Mapped["PerfilProfesional"] = relationship("PerfilProfesional", back_populates="bloqueos")

