from scripts.migrate_json_to_supabase import (
    build_guild_settings_row,
    build_twitch_row,
    build_youtube_row,
)


def test_build_guild_settings_row_prefers_json_twitch_config():
    row = build_guild_settings_row(
        123,
        {"check_interval": 45, "announce_channel_id": 999},
        twitch_check_interval=60,
        twitch_announce_channel_id=111,
        youtube_check_interval=300,
        youtube_announce_channel_id=222,
    )

    assert row == {
        "guild_id": 123,
        "twitch_check_interval": 45,
        "twitch_announce_channel_id": 999,
        "youtube_check_interval": 300,
        "youtube_announce_channel_id": 222,
    }


def test_build_twitch_row_maps_legacy_json_shape():
    row = build_twitch_row(
        {
            "login": "Streamer",
            "channel_id": "456",
            "role_id": None,
            "live": True,
            "peak_viewers": "12",
        },
        123,
    )

    assert row["guild_id"] == 123
    assert row["login"] == "streamer"
    assert row["channel_id"] == 456
    assert row["live"] is True
    assert row["peak_viewers"] == 12


def test_build_youtube_row_maps_legacy_json_shape():
    row = build_youtube_row(
        {
            "channel_id": "UC123",
            "channel_title": "Channel",
            "announce_channel_id": "456",
            "last_video_published_at": "2025-01-01T00:00:00Z",
        },
        123,
    )

    assert row["guild_id"] == 123
    assert row["channel_id"] == "UC123"
    assert row["announce_channel_id"] == 456
    assert row["last_video_published_at"] == "2025-01-01T00:00:00Z"

