"""Agrega zona_horaria por usuario y seguimiento de configuracion onboarding medico

Revision ID: 0003_timezone_and_onboarding
Revises: 0002_seed_initial_admin
Create Date: 2026-11-25 16:05:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_timezone_and_onboarding"
down_revision: Union[str, None] = "0002_seed_initial_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usuarios",
        sa.Column(
            "zona_horaria",
            sa.String(length=64),
            server_default="America/Caracas",
            nullable=False,
        ),
    )
    op.add_column(
        "perfiles_profesionales",
        sa.Column(
            "onboarding_campos_configurados",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("perfiles_profesionales", "onboarding_campos_configurados")
    op.drop_column("usuarios", "zona_horaria")
