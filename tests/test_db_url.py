from galactia.db import build_supabase_database_url, normalize_async_database_url


def test_normalize_async_database_url_accepts_postgres_alias():
    assert normalize_async_database_url("postgres://u:p@example.com/db") == (
        "postgresql+asyncpg://u:p@example.com/db"
    )


def test_build_supabase_database_url_prefers_explicit_url():
    assert build_supabase_database_url(
        database_url="postgresql://u:p@example.com/db",
        project_id="ignored",
        password="ignored",
    ) == "postgresql+asyncpg://u:p@example.com/db"


def test_build_supabase_database_url_from_project_id_and_password():
    assert build_supabase_database_url(project_id="abc123", password="p@ ss") == (
        "postgresql+asyncpg://postgres:p%40%20ss@db.abc123.supabase.co:5432/postgres"
    )

