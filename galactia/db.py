from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote, urlparse, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def normalize_async_database_url(database_url: str) -> str:
    """Return a SQLAlchemy asyncpg URL from a Postgres URL."""
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        parsed = urlparse(database_url)
        return urlunparse(parsed._replace(scheme="postgresql+asyncpg"))
    return database_url


def build_supabase_database_url(
    *,
    database_url: str | None = None,
    project_id: str | None = None,
    password: str | None = None,
) -> str:
    """Build the asyncpg URL used by SQLAlchemy for Supabase Postgres."""
    if database_url:
        return normalize_async_database_url(database_url)
    if not project_id or not password:
        raise ValueError(
            "SUPABASE_DATABASE_URL or VITE_SUPABASE_PROJECT_ID + "
            "VITE_SUPABASE_PASSWORD must be configured."
        )
    encoded_password = quote(password, safe="")
    return (
        "postgresql+asyncpg://postgres:"
        f"{encoded_password}@db.{project_id}.supabase.co:5432/postgres"
    )


def get_database_url() -> str:
    from galactia.settings import settings

    return build_supabase_database_url(
        database_url=settings.supabase_database_url,
        project_id=settings.vite_supabase_project_id,
        password=settings.vite_supabase_password,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return create_async_engine(get_database_url(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        class_=AsyncSession,
    )

