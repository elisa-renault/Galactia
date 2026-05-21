"""add ai summary observability and guild config

Revision ID: 20260521_0002
Revises: 20260519_0001
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260521_0002"
down_revision = "20260519_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guild_settings",
        sa.Column("timezone", sa.Text(), server_default="Europe/Paris", nullable=False),
    )
    op.add_column(
        "guild_settings",
        sa.Column("language", sa.Text(), server_default="fr", nullable=False),
    )
    op.add_column(
        "guild_settings",
        sa.Column(
            "summary_allowed_channel_ids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'::bigint[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "guild_settings",
        sa.Column(
            "summary_allowed_role_ids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'::bigint[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "guild_settings",
        sa.Column("summary_max_messages", sa.Integer(), server_default="500", nullable=False),
    )
    op.add_column(
        "guild_settings",
        sa.Column("summary_max_scan_messages", sa.Integer(), server_default="5000", nullable=False),
    )
    op.add_column(
        "guild_settings",
        sa.Column("summary_quota_guild_daily", sa.Integer(), server_default="100", nullable=False),
    )
    op.add_column(
        "guild_settings",
        sa.Column("summary_quota_user_daily", sa.Integer(), server_default="20", nullable=False),
    )
    op.add_column(
        "guild_settings",
        sa.Column("summary_quota_channel_daily", sa.Integer(), server_default="50", nullable=False),
    )
    op.add_column(
        "guild_settings",
        sa.Column("summary_quota_tokens_daily", sa.Integer(), server_default="500000", nullable=False),
    )

    op.create_table(
        "ai_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("request_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("preset", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("messages_scanned", sa.Integer(), nullable=False),
        sa.Column("messages_selected", sa.Integer(), nullable=False),
        sa.Column("messages_ignored", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_requests_guild_id", "ai_requests", ["guild_id"])
    op.create_index("ix_ai_requests_channel_id", "ai_requests", ["channel_id"])
    op.create_index("ix_ai_requests_user_id", "ai_requests", ["user_id"])
    op.create_index("ix_ai_requests_created_at", "ai_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_requests_created_at", table_name="ai_requests")
    op.drop_index("ix_ai_requests_user_id", table_name="ai_requests")
    op.drop_index("ix_ai_requests_channel_id", table_name="ai_requests")
    op.drop_index("ix_ai_requests_guild_id", table_name="ai_requests")
    op.drop_table("ai_requests")

    op.drop_column("guild_settings", "summary_quota_tokens_daily")
    op.drop_column("guild_settings", "summary_quota_channel_daily")
    op.drop_column("guild_settings", "summary_quota_user_daily")
    op.drop_column("guild_settings", "summary_quota_guild_daily")
    op.drop_column("guild_settings", "summary_max_scan_messages")
    op.drop_column("guild_settings", "summary_max_messages")
    op.drop_column("guild_settings", "summary_allowed_role_ids")
    op.drop_column("guild_settings", "summary_allowed_channel_ids")
    op.drop_column("guild_settings", "language")
    op.drop_column("guild_settings", "timezone")
