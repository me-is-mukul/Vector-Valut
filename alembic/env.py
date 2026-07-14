"""Alembic environment.

The database URL comes from ``osdc.config.paths``, not from ``alembic.ini`` — there is
exactly one place that knows where the database lives, and it honours ``OSDC_DATA_DIR``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from osdc.config import paths
from osdc.storage.schema import Base

config = context.config
# SQLite cannot create a database inside a directory that does not exist yet — and on a
# fresh checkout (or a CI runner) OSDC_DATA_DIR points at exactly such a directory.
paths.ensure_dirs()
config.set_main_option("sqlalchemy.url", paths.db_url())

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER a column; batch mode rebuilds the table instead.
        # Without this, the first schema change you make in Phase 1 fails.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
