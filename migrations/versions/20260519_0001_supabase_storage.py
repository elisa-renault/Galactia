"""create postgres storage tables

Revision ID: 20260519_0001
Revises:
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260519_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "guild_settings",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("twitch_check_interval", sa.Integer(), nullable=False),
        sa.Column("twitch_announce_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("youtube_check_interval", sa.Integer(), nullable=False),
        sa.Column("youtube_announce_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id"),
    )

    op.create_table(
        "twitch_follows",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=True),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("last_started_at", sa.Text(), nullable=True),
        sa.Column("last_message_id", sa.BigInteger(), nullable=True),
        sa.Column("peak_viewers", sa.Integer(), nullable=False),
        sa.Column("last_game_id", sa.Text(), nullable=True),
        sa.Column("last_box_art_url", sa.Text(), nullable=True),
        sa.Column("last_display_name", sa.Text(), nullable=True),
        sa.Column("last_stream_title", sa.Text(), nullable=True),
        sa.Column("last_game_name", sa.Text(), nullable=True),
        sa.Column("profile_image_url", sa.Text(), nullable=True),
        sa.Column("last_user_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guild_id", "login", "channel_id", name="uq_twitch_guild_login_channel"),
    )
    op.create_index("ix_twitch_follows_guild_id", "twitch_follows", ["guild_id"])

    op.create_table(
        "youtube_follows",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.String(length=255), nullable=False),
        sa.Column("channel_title", sa.Text(), nullable=True),
        sa.Column("channel_handle", sa.Text(), nullable=True),
        sa.Column("uploads_playlist_id", sa.Text(), nullable=True),
        sa.Column("announce_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=True),
        sa.Column("last_video_id", sa.Text(), nullable=True),
        sa.Column("last_video_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_thumb_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "guild_id",
            "channel_id",
            "announce_channel_id",
            name="uq_youtube_guild_channel_announce",
        ),
    )
    op.create_index("ix_youtube_follows_guild_id", "youtube_follows", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_youtube_follows_guild_id", table_name="youtube_follows")
    op.drop_table("youtube_follows")
    op.drop_index("ix_twitch_follows_guild_id", table_name="twitch_follows")
    op.drop_table("twitch_follows")
    op.drop_table("guild_settings")
