from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galactia.db import get_session_factory
from galactia.models import AIRequest


AI_REQUEST_COLUMNS = {
    "guild_id",
    "channel_id",
    "user_id",
    "source",
    "request_type",
    "status",
    "model",
    "preset",
    "prompt_version",
    "messages_scanned",
    "messages_selected",
    "messages_ignored",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "latency_ms",
    "attempts",
    "error_type",
}


def _today_start_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def normalize_ai_request(data: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: data.get(key) for key in AI_REQUEST_COLUMNS}
    normalized["request_type"] = normalized.get("request_type") or "summary"
    normalized["status"] = normalized.get("status") or "unknown"
    for key in [
        "messages_scanned",
        "messages_selected",
        "messages_ignored",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "latency_ms",
        "attempts",
    ]:
        normalized[key] = int(normalized.get(key) or 0)
    return normalized


class AIRequestRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._session_factory = session_factory or get_session_factory()

    async def insert(self, data: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            session.add(AIRequest(**normalize_ai_request(data)))
            await session.commit()

    async def usage_today(
        self,
        guild_id: int | None,
        *,
        user_id: int | None = None,
        channel_id: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, int]:
        start = _today_start_utc(now)
        stmt = select(
            func.count(AIRequest.id),
            func.coalesce(func.sum(AIRequest.total_tokens), 0),
        ).where(AIRequest.created_at >= start)
        if guild_id is not None:
            stmt = stmt.where(AIRequest.guild_id == guild_id)
        if user_id is not None:
            stmt = stmt.where(AIRequest.user_id == user_id)
        if channel_id is not None:
            stmt = stmt.where(AIRequest.channel_id == channel_id)
        async with self._session_factory() as session:
            count, tokens = (await session.execute(stmt)).one()
            return {"requests": int(count or 0), "tokens": int(tokens or 0)}

    async def summary_usage_today(
        self,
        guild_id: int | None,
        *,
        user_id: int | None,
        channel_id: int | None,
    ) -> dict[str, dict[str, int]]:
        guild_usage = await self.usage_today(guild_id)
        user_usage = await self.usage_today(guild_id, user_id=user_id)
        channel_usage = await self.usage_today(guild_id, channel_id=channel_id)
        return {
            "guild": guild_usage,
            "user": user_usage,
            "channel": channel_usage,
        }
