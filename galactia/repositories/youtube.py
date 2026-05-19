from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galactia.db import get_session_factory
from galactia.models import YouTubeFollow


YOUTUBE_COLUMNS = [
    "guild_id",
    "channel_id",
    "channel_title",
    "channel_handle",
    "uploads_playlist_id",
    "announce_channel_id",
    "role_id",
    "last_video_id",
    "last_video_published_at",
    "last_message_id",
    "channel_thumb_url",
]


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _dt_to_api(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _follow_to_dict(row: YouTubeFollow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "guild_id": row.guild_id,
        "channel_id": row.channel_id,
        "channel_title": row.channel_title,
        "channel_handle": row.channel_handle,
        "uploads_playlist_id": row.uploads_playlist_id,
        "announce_channel_id": row.announce_channel_id,
        "role_id": row.role_id,
        "last_video_id": row.last_video_id,
        "last_video_published_at": _dt_to_api(row.last_video_published_at),
        "last_message_id": row.last_message_id,
        "channel_thumb_url": row.channel_thumb_url,
    }


def normalize_youtube_follow(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "guild_id": int(data["guild_id"]),
        "channel_id": str(data["channel_id"]),
        "channel_title": data.get("channel_title"),
        "channel_handle": data.get("channel_handle"),
        "uploads_playlist_id": data.get("uploads_playlist_id"),
        "announce_channel_id": int(data["announce_channel_id"]),
        "role_id": _optional_int(data.get("role_id")),
        "last_video_id": data.get("last_video_id"),
        "last_video_published_at": _parse_dt(data.get("last_video_published_at")),
        "last_message_id": _optional_int(data.get("last_message_id")),
        "channel_thumb_url": data.get("channel_thumb_url"),
    }


class YouTubeFollowRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._session_factory = session_factory or get_session_factory()

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(YouTubeFollow))).scalars().all()
            return [_follow_to_dict(row) for row in rows]

    async def list_by_guild(self, guild_id: int) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(YouTubeFollow)
                    .where(YouTubeFollow.guild_id == guild_id)
                    .order_by(YouTubeFollow.channel_title, YouTubeFollow.channel_id)
                )
            ).scalars().all()
            return [_follow_to_dict(row) for row in rows]

    async def exists(self, guild_id: int, channel_id: str, announce_channel_id: int) -> bool:
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(YouTubeFollow)
                .where(
                    YouTubeFollow.guild_id == guild_id,
                    YouTubeFollow.channel_id == channel_id,
                    YouTubeFollow.announce_channel_id == announce_channel_id,
                )
            )
            return bool(count)

    async def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        rows = await self.upsert_many([data])
        return rows[0]

    async def upsert_many(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        normalized = [normalize_youtube_follow(row) for row in rows]
        base_insert = insert(YouTubeFollow)
        update_cols = {
            col: getattr(base_insert.excluded, col)
            for col in YOUTUBE_COLUMNS
            if col not in {"guild_id", "channel_id", "announce_channel_id"}
        }
        update_cols["updated_at"] = func.now()
        stmt = (
            base_insert
            .values(normalized)
            .on_conflict_do_update(
                constraint="uq_youtube_guild_channel_announce",
                set_=update_cols,
            )
            .returning(YouTubeFollow)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            await session.commit()
            return [_follow_to_dict(row) for row in result.scalars().all()]

    async def remove_by_channel_id(self, guild_id: int, channel_id: str) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(YouTubeFollow).where(
                    YouTubeFollow.guild_id == guild_id,
                    YouTubeFollow.channel_id == channel_id,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)
