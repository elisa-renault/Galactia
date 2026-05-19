from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
sys.path.insert(0, str(ROOT))


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def optional_int(value: Any) -> int | None:
    return int(value) if value is not None and value != "" else None


def build_guild_settings_row(
    guild_id: int,
    twitch_config: dict[str, Any],
    *,
    twitch_check_interval: int,
    twitch_announce_channel_id: int | None,
    youtube_check_interval: int,
    youtube_announce_channel_id: int | None,
) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "twitch_check_interval": int(
            twitch_config.get("check_interval") or twitch_check_interval
        ),
        "twitch_announce_channel_id": optional_int(
            twitch_config.get("announce_channel_id") or twitch_announce_channel_id
        ),
        "youtube_check_interval": youtube_check_interval,
        "youtube_announce_channel_id": optional_int(youtube_announce_channel_id),
    }


def build_twitch_row(raw: dict[str, Any], guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "login": str(raw["login"]).strip().lower(),
        "channel_id": int(raw["channel_id"]),
        "role_id": optional_int(raw.get("role_id")),
        "live": bool(raw.get("live", False)),
        "last_started_at": raw.get("last_started_at"),
        "last_message_id": optional_int(raw.get("last_message_id")),
        "peak_viewers": int(raw.get("peak_viewers") or 0),
        "last_game_id": raw.get("last_game_id"),
        "last_box_art_url": raw.get("last_box_art_url"),
        "last_display_name": raw.get("last_display_name"),
        "last_stream_title": raw.get("last_stream_title"),
        "last_game_name": raw.get("last_game_name"),
        "profile_image_url": raw.get("profile_image_url"),
        "last_user_id": raw.get("last_user_id"),
    }


def build_youtube_row(raw: dict[str, Any], guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "channel_id": str(raw["channel_id"]),
        "channel_title": raw.get("channel_title"),
        "channel_handle": raw.get("channel_handle"),
        "uploads_playlist_id": raw.get("uploads_playlist_id"),
        "announce_channel_id": int(raw["announce_channel_id"]),
        "role_id": optional_int(raw.get("role_id")),
        "last_video_id": raw.get("last_video_id"),
        "last_video_published_at": raw.get("last_video_published_at"),
        "last_message_id": optional_int(raw.get("last_message_id")),
        "channel_thumb_url": raw.get("channel_thumb_url"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import local Galactia JSON storage into Supabase/Postgres."
    )
    parser.add_argument("--guild-id", type=int, default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    env_file = args.env_file or os.getenv("ENV_FILE")
    if not env_file:
        env_file = ".env.dev" if (ROOT / ".env.dev").exists() else ".env"
    load_dotenv(ROOT / env_file)

    from galactia.repositories.guild_settings import GuildSettingsRepository
    from galactia.repositories.twitch import TwitchFollowRepository
    from galactia.repositories.youtube import YouTubeFollowRepository
    from galactia.settings import settings

    guild_id = args.guild_id or settings.discord_guild_id
    if not guild_id:
        raise SystemExit("A guild id is required via --guild-id or DISCORD_GUILD_ID.")

    twitch_config = load_json(DATA_DIR / "twitch_config.json", {})
    twitch_rows = [
        build_twitch_row(row, guild_id)
        for row in load_json(DATA_DIR / "twitch.json", [])
    ]
    youtube_rows = [
        build_youtube_row(row, guild_id)
        for row in load_json(DATA_DIR / "youtube.json", [])
    ]
    settings_row = build_guild_settings_row(
        guild_id,
        twitch_config,
        twitch_check_interval=settings.twitch_check_interval,
        twitch_announce_channel_id=settings.twitch_announce_channel_id,
        youtube_check_interval=settings.youtube_check_interval,
        youtube_announce_channel_id=settings.youtube_announce_channel_id,
    )

    print(
        "Prepared import: "
        f"guild_settings=1, twitch_follows={len(twitch_rows)}, "
        f"youtube_follows={len(youtube_rows)}"
    )
    if args.dry_run:
        return

    guild_repo = GuildSettingsRepository()
    twitch_repo = TwitchFollowRepository()
    youtube_repo = YouTubeFollowRepository()

    await guild_repo.upsert(settings_row)
    if twitch_rows:
        await twitch_repo.upsert_many(twitch_rows)
    if youtube_rows:
        await youtube_repo.upsert_many(youtube_rows)

    print("Import completed.")


if __name__ == "__main__":
    asyncio.run(main())
