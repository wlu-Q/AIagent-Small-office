"""Asynchronous LangGraph checkpointer factory driven by ``database``."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from langgraph.types import Checkpointer

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.runtime.checkpointer.provider import (
    POSTGRES_CONN_REQUIRED,
    POSTGRES_INSTALL,
    SQLITE_INSTALL,
)
from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir


def _prepare_sqlite_checkpointer_path(config: DatabaseConfig) -> str:
    conn_str = config.sqlite_path
    ensure_sqlite_parent_dir(conn_str)
    return conn_str


def _build_postgres_pool(conn_string: str):
    """Build an AsyncConnectionPool with keepalive and connection checks."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    return AsyncConnectionPool(
        conn_string,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "keepalives": 1,
            "keepalives_idle": 60,
            "keepalives_interval": 10,
            "keepalives_count": 6,
        },
        check=AsyncConnectionPool.check_connection,
    )


def _ensure_postgres_imports():
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        raise ImportError(POSTGRES_INSTALL) from exc
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise ImportError(POSTGRES_INSTALL) from exc
    return AsyncPostgresSaver, AsyncConnectionPool


@contextlib.asynccontextmanager
async def _async_checkpointer(config: DatabaseConfig) -> AsyncIterator[Checkpointer]:
    """Create and close an asynchronous checkpointer for ``config``."""
    if config.backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    if config.backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        conn_str = await asyncio.to_thread(_prepare_sqlite_checkpointer_path, config)
        async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            yield saver
        return

    if config.backend == "postgres":
        if not config.postgres_url:
            raise ValueError(POSTGRES_CONN_REQUIRED)
        AsyncPostgresSaver, _ = _ensure_postgres_imports()
        pool = _build_postgres_pool(config.postgres_url)
        async with pool:
            saver = AsyncPostgresSaver(conn=pool)
            await saver.setup()
            yield saver
        return

    raise ValueError(f"Unknown database backend: {config.backend!r}")


@contextlib.asynccontextmanager
async def make_checkpointer(app_config: AppConfig | None = None) -> AsyncIterator[Checkpointer]:
    """Yield a checkpointer selected by the unified database configuration."""
    config = (app_config or get_app_config()).database
    async with _async_checkpointer(config) as saver:
        yield saver
