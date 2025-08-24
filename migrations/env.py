from logging.config import fileConfig
from sqlalchemy import pool, create_engine
from alembic import context

# ➜ importe ta config et tes modèles
from core.settings import settings
from core.db import Base  # Base declarative
import core.models  # noqa: F401  (assure l'import des tables)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline():
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
