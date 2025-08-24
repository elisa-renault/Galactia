# /home/Galactia/core/models.py
from sqlalchemy import Column, Integer, String, BigInteger, ForeignKey, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import relationship, Mapped, mapped_column
from datetime import datetime
from core.db import Base

# core/models.py
from sqlalchemy import Boolean, Text

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_site_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # <- NEW (admin Galactia)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class Feature(Base):
    __tablename__ = "features"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # "twitch" | "youtube" | "ai"
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

class GuildPremium(Base):
    __tablename__ = "guild_premium"
    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guilds.id"), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(32), default="premium")  # extensible
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    __table_args__ = (UniqueConstraint("guild_id", name="uq_guild_premium"), )

class GuildFeatureFlag(Base):
    __tablename__ = "guild_feature_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guilds.id"), index=True)
    feature_id: Mapped[int] = mapped_column(ForeignKey("features.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("guild_id", "feature_id", name="uq_guild_feature"), )

class Guild(Base):
    __tablename__ = "guilds"
    id: Mapped[int] = mapped_column(primary_key=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    icon: Mapped[str | None] = mapped_column(String(512))

class GuildMember(Base):
    __tablename__ = "guild_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    guild_id: Mapped[int] = mapped_column(ForeignKey("guilds.id"))
    role: Mapped[str] = mapped_column(String(32))  # owner, admin, officer, member, viewer

    __table_args__ = (UniqueConstraint("user_id", "guild_id", name="uq_user_guild"), )

class GuildSetting(Base):
    __tablename__ = "guild_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guilds.id"))
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(2000))
    __table_args__ = (UniqueConstraint("guild_id", "key", name="uq_guild_key"), )

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guilds.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime)
