from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galactia.db import get_session_factory
from galactia.models import TwitchFollow


TWITCH_COLUMNS = [
    "guild_id",
    "login",
    "channel_id",
    "role_id",
    "live",
    "last_started_at",
    "last_message_id",
    "peak_viewers",
    "last_game_id",
    "last_box_art_url",
    "last_display_name",
    "last_stream_title",
    "last_game_name",
    "profile_image_url",
    "last_user_id",
]


def _follow_to_dict(row: TwitchFollow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "guild_id": row.guild_id,
        "login": row.login,
        "channel_id": row.channel_id,
        "role_id": row.role_id,
        "live": row.live,
        "last_started_at": row.last_started_at,
        "last_message_id": row.last_message_id,
        "peak_viewers": row.peak_viewers,
        "last_game_id": row.last_game_id,
        "last_box_art_url": row.last_box_art_url,
        "last_display_name": row.last_display_name,
        "last_stream_title": row.last_stream_title,
        "last_game_name": row.last_game_name,
        "profile_image_url": row.profile_image_url,
        "last_user_id": row.last_user_id,
    }


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def normalize_twitch_follow(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "guild_id": int(data["guild_id"]),
        "login": str(data["login"]).strip().lower(),
        "channel_id": int(data["channel_id"]),
        "role_id": _optional_int(data.get("role_id")),
        "live": bool(data.get("live", False)),
        "last_started_at": data.get("last_started_at"),
        "last_message_id": _optional_int(data.get("last_message_id")),
        "peak_viewers": int(data.get("peak_viewers") or 0),
        "last_game_id": data.get("last_game_id"),
        "last_box_art_url": data.get("last_box_art_url"),
        "last_display_name": data.get("last_display_name"),
        "last_stream_title": data.get("last_stream_title"),
        "last_game_name": data.get("last_game_name"),
        "profile_image_url": data.get("profile_image_url"),
        "last_user_id": data.get("last_user_id"),
    }


class TwitchFollowRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._session_factory = session_factory or get_session_factory()

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(TwitchFollow))).scalars().all()
            return [_follow_to_dict(row) for row in rows]

    async def list_by_guild(self, guild_id: int) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TwitchFollow)
                    .where(TwitchFollow.guild_id == guild_id)
                    .order_by(TwitchFollow.login, TwitchFollow.channel_id)
                )
            ).scalars().all()
            return [_follow_to_dict(row) for row in rows]

    async def exists(self, guild_id: int, login: str, channel_id: int) -> bool:
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(TwitchFollow)
                .where(
                    TwitchFollow.guild_id == guild_id,
                    TwitchFollow.login == login.strip().lower(),
                    TwitchFollow.channel_id == channel_id,
                )
            )
            return bool(count)

    async def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        rows = await self.upsert_many([data])
        return rows[0]

    async def upsert_many(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        normalized = [normalize_twitch_follow(row) for row in rows]
        base_insert = insert(TwitchFollow)
        update_cols = {
            col: getattr(base_insert.excluded, col)
            for col in TWITCH_COLUMNS
            if col not in {"guild_id", "login", "channel_id"}
        }
        update_cols["updated_at"] = func.now()
        stmt = (
            base_insert
            .values(normalized)
            .on_conflict_do_update(
                constraint="uq_twitch_guild_login_channel",
                set_=update_cols,
            )
            .returning(TwitchFollow)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            await session.commit()
            return [_follow_to_dict(row) for row in result.scalars().all()]

    async def remove_by_login(self, guild_id: int, login: str) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(TwitchFollow).where(
                    TwitchFollow.guild_id == guild_id,
                    TwitchFollow.login == login.strip().lower(),
                )
            )
            await session.commit()
            return int(result.rowcount or 0)
