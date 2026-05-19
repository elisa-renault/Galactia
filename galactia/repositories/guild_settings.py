from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galactia.db import get_session_factory
from galactia.models import GuildSettings


def _settings_to_dict(row: GuildSettings) -> dict[str, Any]:
    return {
        "guild_id": row.guild_id,
        "twitch_check_interval": row.twitch_check_interval,
        "twitch_announce_channel_id": row.twitch_announce_channel_id,
        "youtube_check_interval": row.youtube_check_interval,
        "youtube_announce_channel_id": row.youtube_announce_channel_id,
    }


class GuildSettingsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._session_factory = session_factory or get_session_factory()

    async def get_or_create(
        self,
        guild_id: int,
        *,
        twitch_check_interval: int,
        twitch_announce_channel_id: int | None,
        youtube_check_interval: int,
        youtube_announce_channel_id: int | None,
    ) -> dict[str, Any]:
        defaults = {
            "guild_id": guild_id,
            "twitch_check_interval": twitch_check_interval,
            "twitch_announce_channel_id": twitch_announce_channel_id,
            "youtube_check_interval": youtube_check_interval,
            "youtube_announce_channel_id": youtube_announce_channel_id,
        }
        async with self._session_factory() as session:
            stmt = (
                insert(GuildSettings)
                .values(**defaults)
                .on_conflict_do_nothing(index_elements=["guild_id"])
            )
            await session.execute(stmt)
            await session.commit()
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not found after upsert: {guild_id}")
            return _settings_to_dict(row)

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(GuildSettings))).scalars().all()
            return [_settings_to_dict(row) for row in rows]

    async def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        stmt = (
            insert(GuildSettings)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["guild_id"],
                set_={
                    "twitch_check_interval": data["twitch_check_interval"],
                    "twitch_announce_channel_id": data.get("twitch_announce_channel_id"),
                    "youtube_check_interval": data["youtube_check_interval"],
                    "youtube_announce_channel_id": data.get("youtube_announce_channel_id"),
                    "updated_at": func.now(),
                },
            )
            .returning(GuildSettings)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one()
            await session.commit()
            return _settings_to_dict(row)

    async def update_twitch_interval(self, guild_id: int, seconds: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not initialized: {guild_id}")
            row.twitch_check_interval = seconds
            await session.commit()

    async def update_twitch_channel(self, guild_id: int, channel_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not initialized: {guild_id}")
            row.twitch_announce_channel_id = channel_id
            await session.commit()

    async def update_youtube_interval(self, guild_id: int, seconds: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not initialized: {guild_id}")
            row.youtube_check_interval = seconds
            await session.commit()

    async def update_youtube_channel(self, guild_id: int, channel_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not initialized: {guild_id}")
            row.youtube_announce_channel_id = channel_id
            await session.commit()
