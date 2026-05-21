from galactia.db import (
    build_database_url,
    build_supabase_database_url,
    normalize_async_database_url,
)


def test_normalize_async_database_url_accepts_postgres_alias():
    assert normalize_async_database_url("postgres://u:p@example.com/db") == (
        "postgresql+asyncpg://u:p@example.com/db"
    )


def test_normalize_async_database_url_accepts_postgresql_url():
    assert normalize_async_database_url("postgresql://u:p@example.com/db") == (
        "postgresql+asyncpg://u:p@example.com/db"
    )


def test_normalize_async_database_url_keeps_asyncpg_url():
    assert normalize_async_database_url("postgresql+asyncpg://u:p@example.com/db") == (
        "postgresql+asyncpg://u:p@example.com/db"
    )


def test_build_database_url_prefers_database_url():
    assert build_database_url(
        database_url="postgresql://u:p@example.com/db",
        supabase_database_url="postgresql://legacy:legacy@example.com/db",
        supabase_project_id="ignored",
        supabase_password="ignored",
    ) == "postgresql+asyncpg://u:p@example.com/db"


def test_build_database_url_uses_supabase_database_url_fallback():
    assert build_database_url(
        supabase_database_url="postgres://u:p@example.com/db",
        supabase_project_id="ignored",
        supabase_password="ignored",
    ) == "postgresql+asyncpg://u:p@example.com/db"


def test_build_database_url_from_supabase_project_id_and_password():
    assert build_database_url(supabase_project_id="abc123", supabase_password="p@ ss") == (
        "postgresql+asyncpg://postgres:p%40%20ss@db.abc123.supabase.co:5432/postgres"
    )


def test_build_supabase_database_url_keeps_compatibility():
    assert build_supabase_database_url(
        database_url="postgresql://u:p@example.com/db",
        project_id="ignored",
        password="ignored",
    ) == "postgresql+asyncpg://u:p@example.com/db"
