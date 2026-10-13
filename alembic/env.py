from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, event
from sqlalchemy.engine import Connection

from app.core.config import get_settings

# Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None and os.path.exists(config.config_file_name):
    fileConfig(config.config_file_name)

# Model metadata can be assigned here when models are defined
target_metadata = None


def get_database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    
    # Check environment override
    env_url = os.getenv("GRANTLOOM_DB_URL")
    if env_url:
        return env_url

    # Fallback to application settings
    settings = get_settings()
    sqlite_path = Path(settings.database.sqlite_path)
    # Ensure parent directory exists
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{sqlite_path.as_posix()}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Ensure foreign keys are enabled for SQLite
    if connection.dialect.name == "sqlite":
        cursor = connection.connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Check if a connection was provided programmatically
    provided_connection = config.attributes.get("connection", None)
    if provided_connection is not None:
        do_run_migrations(provided_connection)
        return

    url = get_database_url()
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
