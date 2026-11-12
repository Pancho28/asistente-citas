"""Seed inicial para usuario administrador desde variables de entorno

Revision ID: 0002_seed_initial_admin
Revises: 0001_initial_schema
Create Date: 2026-11-06 16:30:00.000000

"""
from typing import Sequence, Union
import uuid
from alembic import op
import sqlalchemy as sa
from src.core.config import get_settings

revision: str = "0002_seed_initial_admin"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    settings = get_settings()
    bind = op.get_bind()

    # 1. Seed para Administrador de Telegram si está configurado
    if settings.INITIAL_ADMIN_TELEGRAM_CHAT_ID:
        stmt = sa.text("""
            INSERT INTO usuarios (id, canal_origen, external_chat_id, nombre_completo, rol_sistema, modo_vista_actual, activo, created_at)
            VALUES (:id, 'TELEGRAM', :external_chat_id, :nombre, 'ADMIN', 'ADMIN', true, CURRENT_TIMESTAMP)
            ON CONFLICT (canal_origen, external_chat_id)
            DO UPDATE SET rol_sistema = 'ADMIN', modo_vista_actual = 'ADMIN', activo = true;
        """)
        bind.execute(
            stmt,
            {
                "id": uuid.uuid4(),
                "external_chat_id": str(settings.INITIAL_ADMIN_TELEGRAM_CHAT_ID).strip(),
                "nombre": settings.INITIAL_ADMIN_NOMBRE,
            }
        )

    # 2. Seed para Administrador de WhatsApp si está configurado
    if settings.INITIAL_ADMIN_WHATSAPP_PHONE:
        stmt = sa.text("""
            INSERT INTO usuarios (id, canal_origen, external_chat_id, nombre_completo, rol_sistema, modo_vista_actual, activo, created_at)
            VALUES (:id, 'WHATSAPP', :external_chat_id, :nombre, 'ADMIN', 'ADMIN', true, CURRENT_TIMESTAMP)
            ON CONFLICT (canal_origen, external_chat_id)
            DO UPDATE SET rol_sistema = 'ADMIN', modo_vista_actual = 'ADMIN', activo = true;
        """)
        bind.execute(
            stmt,
            {
                "id": uuid.uuid4(),
                "external_chat_id": str(settings.INITIAL_ADMIN_WHATSAPP_PHONE).strip(),
                "nombre": settings.INITIAL_ADMIN_NOMBRE,
            }
        )


def downgrade() -> None:
    # Se preservan los datos de usuarios por seguridad en caso de rollback
    pass

