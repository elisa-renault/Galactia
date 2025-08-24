# /home/Galactia/core/settings.py
from pydantic_settings import BaseSettings
from pydantic import AnyUrl

class Settings(BaseSettings):
    ENV: str = "prod"

    # Discord OAuth
    DISCORD_CLIENT_ID: str
    DISCORD_CLIENT_SECRET: str
    DISCORD_REDIRECT_URI: str  # ex: https://admin.ton-domaine/auth/callback

    # Sessions / CSRF
    SESSION_SECRET: str

    # DB
    DATABASE_URL: str  # postgresql+psycopg2://... OU sqlite:////...

    # App
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 35801
    APP_TITLE: str = "Galactia Admin Panel"

    class Config:
        env_file = ".env.panel"
        extra = "ignore"

settings = Settings()
