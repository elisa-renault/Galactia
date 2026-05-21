from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galactia.db import get_session_factory
from galactia.models import GuildSettings


DEFAULT_SUMMARY_SETTINGS = {
    "timezone": "Europe/Paris",
    "language": "fr",
    "summary_allowed_channel_ids": [],
    "summary_allowed_role_ids": [],
    "summary_max_messages": 500,
    "summary_max_scan_messages": 5000,
    "summary_quota_guild_daily": 100,
    "summary_quota_user_daily": 20,
    "summary_quota_channel_daily": 50,
    "summary_quota_tokens_daily": 500000,
}


def _settings_to_dict(row: GuildSettings) -> dict[str, Any]:
    return {
        "guild_id": row.guild_id,
        "twitch_check_interval": row.twitch_check_interval,
        "twitch_announce_channel_id": row.twitch_announce_channel_id,
        "youtube_check_interval": row.youtube_check_interval,
        "youtube_announce_channel_id": row.youtube_announce_channel_id,
        "timezone": row.timezone,
        "language": row.language,
        "summary_allowed_channel_ids": list(row.summary_allowed_channel_ids or []),
        "summary_allowed_role_ids": list(row.summary_allowed_role_ids or []),
        "summary_max_messages": row.summary_max_messages,
        "summary_max_scan_messages": row.summary_max_scan_messages,
        "summary_quota_guild_daily": row.summary_quota_guild_daily,
        "summary_quota_user_daily": row.summary_quota_user_daily,
        "summary_quota_channel_daily": row.summary_quota_channel_daily,
        "summary_quota_tokens_daily": row.summary_quota_tokens_daily,
    }


def normalize_settings_payload(data: dict[str, Any]) -> dict[str, Any]:
    normalized = {**DEFAULT_SUMMARY_SETTINGS, **data}
    normalized["summary_allowed_channel_ids"] = [
        int(channel_id) for channel_id in normalized.get("summary_allowed_channel_ids") or []
    ]
    normalized["summary_allowed_role_ids"] = [
        int(role_id) for role_id in normalized.get("summary_allowed_role_ids") or []
    ]
    normalized["summary_max_messages"] = min(max(int(normalized["summary_max_messages"]), 1), 2000)
    normalized["summary_max_scan_messages"] = min(
        max(int(normalized["summary_max_scan_messages"]), normalized["summary_max_messages"]),
        5000,
    )
    return normalized


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
        defaults = normalize_settings_payload(defaults)
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
        data = normalize_settings_payload(data)
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
                    "timezone": data["timezone"],
                    "language": data["language"],
                    "summary_allowed_channel_ids": data["summary_allowed_channel_ids"],
                    "summary_allowed_role_ids": data["summary_allowed_role_ids"],
                    "summary_max_messages": data["summary_max_messages"],
                    "summary_max_scan_messages": data["summary_max_scan_messages"],
                    "summary_quota_guild_daily": data["summary_quota_guild_daily"],
                    "summary_quota_user_daily": data["summary_quota_user_daily"],
                    "summary_quota_channel_daily": data["summary_quota_channel_daily"],
                    "summary_quota_tokens_daily": data["summary_quota_tokens_daily"],
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

    async def update_summary_field(self, guild_id: int, field: str, value: Any) -> dict[str, Any]:
        if field not in DEFAULT_SUMMARY_SETTINGS:
            raise ValueError(f"Unsupported summary setting: {field}")
        async with self._session_factory() as session:
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not initialized: {guild_id}")
            setattr(row, field, value)
            await session.commit()
            await session.refresh(row)
            return _settings_to_dict(row)

    async def update_timezone(self, guild_id: int, timezone: str) -> dict[str, Any]:
        return await self.update_summary_field(guild_id, "timezone", timezone)

    async def update_language(self, guild_id: int, language: str) -> dict[str, Any]:
        return await self.update_summary_field(guild_id, "language", language)

    async def update_summary_max_messages(self, guild_id: int, max_messages: int) -> dict[str, Any]:
        max_messages = min(max(int(max_messages), 1), 2000)
        async with self._session_factory() as session:
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not initialized: {guild_id}")
            row.summary_max_messages = max_messages
            if row.summary_max_scan_messages < max_messages:
                row.summary_max_scan_messages = max_messages
            await session.commit()
            await session.refresh(row)
            return _settings_to_dict(row)

    async def mutate_summary_id_list(
        self,
        guild_id: int,
        field: str,
        action: str,
        value: int | None = None,
    ) -> dict[str, Any]:
        if field not in {"summary_allowed_channel_ids", "summary_allowed_role_ids"}:
            raise ValueError(f"Unsupported id-list setting: {field}")
        async with self._session_factory() as session:
            row = await session.get(GuildSettings, guild_id)
            if row is None:
                raise RuntimeError(f"Guild settings not initialized: {guild_id}")
            current = list(getattr(row, field) or [])
            if action == "add" and value is not None:
                current = sorted(set(current) | {int(value)})
            elif action == "remove" and value is not None:
                current = [item for item in current if item != int(value)]
            elif action == "clear":
                current = []
            elif action != "list":
                raise ValueError(f"Unsupported id-list action: {action}")
            setattr(row, field, current)
            await session.commit()
            await session.refresh(row)
            return _settings_to_dict(row)
