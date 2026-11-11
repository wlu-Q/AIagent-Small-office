"""Alembic environment for DeerFlow application tables.

ONLY manages DeerFlow's application tables (runs, threads_meta, feedback,
users, and run_events).

LangGraph's checkpointer tables (``checkpoints``, ``checkpoint_blobs``,
``checkpoint_writes``, ``checkpoint_migrations``) are managed by LangGraph
itself -- they have their own schema lifecycle and must not be touched by
Alembic. The ``include_object`` filter below explicitly excludes them so a
future ``alembic revision --autogenerate`` will not emit ``drop_table`` for
tables it does not own.
"""

from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.migrations._env_filters import (
    LANGGRAPH_OWNED_TABLES,
    include_object,
)

# Re-export under the module namespace for any consumer that addresses them
# via ``env.LANGGRAPH_OWNED_TABLES`` / ``env.include_object``.
__all__ = ["LANGGRAPH_OWNED_TABLES", "include_object"]

# Import all models so metadata is populated.
try:
    import deerflow.persistence.models as models  # register ORM models with Base.metadata

    _ = models
except ImportError:
    # Models not available — migration will work with existing metadata only.
    logging.getLogger(__name__).warning("Could not import deerflow.persistence.models; Alembic may not detect all tables")

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _enable_sqlite_busy_timeout(engine: Engine) -> None:
    """Apply the production SQLite lock-wait policy to Alembic connections."""

    @event.listens_for(engine, "connect")
    def _alembic_sqlite_busy_timeout(dbapi_conn, _record):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000;")
        finally:
            cursor.close()


def run_sqlite_migrations_online(database_url: URL) -> None:
    """Run local SQLite migrations without asyncio or an aiosqlite thread."""
    connectable = create_engine(database_url.set(drivername="sqlite"))
    _enable_sqlite_busy_timeout(connectable)
    try:
        with connectable.connect() as connection:
            do_run_migrations(connection)
    finally:
        connectable.dispose()


async def run_migrations_online(database_url: URL) -> None:
    """Run migrations for async database drivers such as PostgreSQL."""
    connectable = create_async_engine(database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    database_url = make_url(config.get_main_option("sqlalchemy.url"))
    if database_url.drivername.startswith("sqlite"):
        run_sqlite_migrations_online(database_url)
    else:
        asyncio.run(run_migrations_online(database_url))
