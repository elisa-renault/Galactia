"""add galactia manager roles

Revision ID: 20260521_0004
Revises: 20260521_0003
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260521_0004"
down_revision = "20260521_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guild_settings",
        sa.Column(
            "galactia_manager_role_ids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'::bigint[]"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("guild_settings", "galactia_manager_role_ids")
