"""add public guild onboarding fields

Revision ID: 20260521_0003
Revises: 20260521_0002
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa


revision = "20260521_0003"
down_revision = "20260521_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guild_settings", sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("guild_settings", sa.Column("setup_completed_by_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_settings", sa.Column("setup_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_settings", sa.Column("summary_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column(
        "guild_settings",
        sa.Column("summary_access_mode", sa.Text(), server_default="admins_only", nullable=False),
    )
    op.add_column("guild_settings", sa.Column("twitch_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("guild_settings", sa.Column("youtube_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False))

    op.execute(
        """
        UPDATE guild_settings
        SET
            setup_completed_at = COALESCE(setup_completed_at, now()),
            summary_enabled = true,
            twitch_enabled = true,
            youtube_enabled = true,
            summary_access_mode = CASE
                WHEN cardinality(summary_allowed_role_ids) > 0 THEN 'allowed_roles'
                ELSE 'everyone'
            END
        """
    )


def downgrade() -> None:
    op.drop_column("guild_settings", "youtube_enabled")
    op.drop_column("guild_settings", "twitch_enabled")
    op.drop_column("guild_settings", "summary_access_mode")
    op.drop_column("guild_settings", "summary_enabled")
    op.drop_column("guild_settings", "setup_channel_id")
    op.drop_column("guild_settings", "setup_completed_by_id")
    op.drop_column("guild_settings", "setup_completed_at")
