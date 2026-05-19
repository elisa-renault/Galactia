from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class GuildSettings(TimestampMixin, Base):
    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    twitch_check_interval: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    twitch_announce_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    youtube_check_interval: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    youtube_announce_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TwitchFollow(TimestampMixin, Base):
    __tablename__ = "twitch_follows"
    __table_args__ = (
        UniqueConstraint("guild_id", "login", "channel_id", name="uq_twitch_guild_login_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    peak_viewers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_game_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_box_art_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_stream_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_game_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class YouTubeFollow(TimestampMixin, Base):
    __tablename__ = "youtube_follows"
    __table_args__ = (
        UniqueConstraint(
            "guild_id",
            "channel_id",
            "announce_channel_id",
            name="uq_youtube_guild_channel_announce",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_handle: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploads_playlist_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    announce_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_video_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_video_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_thumb_url: Mapped[str | None] = mapped_column(Text, nullable=True)

