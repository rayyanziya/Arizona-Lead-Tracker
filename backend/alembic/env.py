"""Alembic migration environment.

``target_metadata`` is the app's ORM metadata, so ``--autogenerate`` stays in
sync with the models. The database URL resolves from ``ALEMBIC_DATABASE_URL``
when set (used for offline autogenerate/verification against SQLite, and for
CI), otherwise from the app's sync DSN. The settings import is deferred so the
override path needs neither pydantic-settings nor a Postgres driver installed.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    override = os.getenv("ALEMBIC_DATABASE_URL")
    if override:
        return override
    from app.core.config import settings

    return settings.database_url_sync


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
