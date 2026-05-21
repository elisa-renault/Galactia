import os
import logging
from datetime import datetime
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load environment variables from a .env file if present
ENV_FILE = os.getenv("ENV_FILE", ".env")
load_dotenv(dotenv_path=ENV_FILE)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(extra="ignore")

    discord_token: str = Field(env="DISCORD_TOKEN")
    discord_guild_id: int | None = Field(default=None, env="DISCORD_GUILD_ID")
    discord_command_scope: str = Field(default="global", env="DISCORD_COMMAND_SCOPE")
    twitch_client_id: str = Field(env="TWITCH_CLIENT_ID")
    twitch_client_secret: str = Field(env="TWITCH_CLIENT_SECRET")
    twitch_check_interval: int = Field(default=60, env="TWITCH_CHECK_INTERVAL")
    twitch_announce_channel_id: int | None = Field(default=None, env="TWITCH_ANNOUNCE_CHANNEL_ID")
    youtube_api_key: str | None = Field(default=None, env="YOUTUBE_API_KEY")
    youtube_check_interval: int = Field(default=300, env="YOUTUBE_CHECK_INTERVAL")
    youtube_announce_channel_id: int | None = Field(default=None, env="YOUTUBE_ANNOUNCE_CHANNEL_ID")
    openai_api_key: str = Field(env="OPENAI_API_KEY")
    database_url: str | None = Field(default=None, env="DATABASE_URL")
    supabase_database_url: str | None = Field(default=None, env="SUPABASE_DATABASE_URL")
    vite_supabase_project_id: str | None = Field(default=None, env="VITE_SUPABASE_PROJECT_ID")
    vite_supabase_url: str | None = Field(default=None, env="VITE_SUPABASE_URL")
    vite_supabase_publishable_key: str | None = Field(default=None, env="VITE_SUPABASE_PUBLISHABLE_KEY")
    vite_supabase_password: str | None = Field(default=None, env="VITE_SUPABASE_PASSWORD")
    env_mode: str = Field(default="production", env="ENV_MODE")


settings = Settings()


def configure_logging() -> None:
    """Configure application logging."""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file_path = os.path.join(log_dir, f"Galactia_{today}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    logging.info("📦 Loading env from %s", ENV_FILE)
    logging.info("🚀 Starting Galactia in %s mode...", settings.env_mode)
