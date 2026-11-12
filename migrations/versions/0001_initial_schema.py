"""Initial schema with all 7 tables and partial indexes

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-11-06 13:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tabla usuarios
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canal_origen", sa.String(length=20), nullable=False),
        sa.Column("external_chat_id", sa.String(length=100), nullable=False),
        sa.Column("nombre_completo", sa.String(length=255), nullable=True),
        sa.Column("telefono", sa.String(length=50), nullable=True),
        sa.Column("rol_sistema", sa.String(length=20), server_default="PACIENTE", nullable=False),
        sa.Column("modo_vista_actual", sa.String(length=20), server_default="PACIENTE", nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_usuario_canal_external_id",
        "usuarios",
        ["canal_origen", "external_chat_id"],
        unique=True
    )

    # 2. Tabla codigos_invitacion
    op.create_table(
        "codigos_invitacion",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("frase_codigo", sa.String(length=100), nullable=False),
        sa.Column("creado_por_admin_id", sa.Uuid(), nullable=False),
        sa.Column("especialidad_asignada", sa.String(length=100), nullable=False),
        sa.Column("usado", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("usado_por_usuario_id", sa.Uuid(), nullable=True),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canjeado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["creado_por_admin_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["usado_por_usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("frase_codigo"),
    )
    op.create_index(
        "ix_codigos_invitacion_frase_codigo",
        "codigos_invitacion",
        ["frase_codigo"],
        unique=True
    )

    # 3. Tabla perfiles_profesionales
    op.create_table(
        "perfiles_profesionales",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), nullable=False),
        sa.Column("especialidad", sa.String(length=100), nullable=False),
        sa.Column("duracion_cita_minutos", sa.Integer(), server_default="30", nullable=False),
        sa.Column("tarifa_base_consulta", sa.Numeric(precision=10, scale=2), server_default="0.00", nullable=False),
        sa.Column("moneda", sa.String(length=10), server_default="USD", nullable=False),
        sa.Column("min_horas_anticipacion_cancelar", sa.Integer(), server_default="2", nullable=False),
        sa.Column("direccion_consultorio", sa.String(length=255), nullable=True),
        sa.Column("hora_resumen_diario", sa.Time(), server_default="12:00:00", nullable=False),
        sa.Column("alcance_resumen_diario", sa.String(length=20), server_default="AGENDA_HOY", nullable=False),
        sa.Column("ultimo_resumen_enviado_en", sa.Date(), nullable=True),
        sa.Column("acepta_nuevas_citas", sa.Boolean(), server_default="true", nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("usuario_id"),
    )

    # 4. Tabla horarios_base
    op.create_table(
        "horarios_base",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profesional_id", sa.Uuid(), nullable=False),
        sa.Column("dia_semana", sa.SmallInteger(), nullable=False),
        sa.Column("hora_inicio", sa.Time(), nullable=False),
        sa.Column("hora_fin", sa.Time(), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default="true", nullable=False),
        sa.ForeignKeyConstraint(["profesional_id"], ["perfiles_profesionales.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. Tabla bloqueos_agenda
    op.create_table(
        "bloqueos_agenda",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profesional_id", sa.Uuid(), nullable=False),
        sa.Column("fecha_hora_inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_hora_fin", sa.DateTime(timezone=True), nullable=False),
        sa.Column("motivo", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["profesional_id"], ["perfiles_profesionales.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 6. Tabla citas
    op.create_table(
        "citas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profesional_id", sa.Uuid(), nullable=False),
        sa.Column("paciente_id", sa.Uuid(), nullable=False),
        sa.Column("fecha_hora_inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecha_hora_fin", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(length=30), server_default="CONFIRMADA", nullable=False),
        sa.Column("monto_acordado", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("estado_financiero", sa.String(length=20), server_default="PENDIENTE", nullable=False),
        sa.Column("origen_creacion", sa.String(length=20), server_default="IA_TEXTO", nullable=False),
        sa.Column("motivo_consulta", sa.Text(), nullable=True),
        sa.Column("recordatorio_paciente_enviado", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("recordatorio_enviado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("respuesta_recordatorio", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["paciente_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["profesional_id"], ["perfiles_profesionales.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_cita_unica_activa",
        "citas",
        ["profesional_id", "fecha_hora_inicio"],
        unique=True,
        postgresql_where=sa.text("estado NOT IN ('CANCELADA')"),
        sqlite_where=sa.text("estado NOT IN ('CANCELADA')")
    )

    # 7. Tabla pagos
    op.create_table(
        "pagos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cita_id", sa.Uuid(), nullable=False),
        sa.Column("monto_pagado", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("moneda", sa.String(length=10), server_default="USD", nullable=False),
        sa.Column("tipo_pago", sa.String(length=30), nullable=False),
        sa.Column("metodo_pago", sa.String(length=30), nullable=False),
        sa.Column("referencia_transaccion", sa.String(length=100), nullable=True),
        sa.Column("estado_transaccion", sa.String(length=20), server_default="COMPLETADO", nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("fecha_pago", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["cita_id"], ["citas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("pagos")
    op.drop_index("idx_cita_unica_activa", table_name="citas")
    op.drop_table("citas")
    op.drop_table("bloqueos_agenda")
    op.drop_table("horarios_base")
    op.drop_table("perfiles_profesionales")
    op.drop_index("ix_codigos_invitacion_frase_codigo", table_name="codigos_invitacion")
    op.drop_table("codigos_invitacion")
    op.drop_index("idx_usuario_canal_external_id", table_name="usuarios")
    op.drop_table("usuarios")

