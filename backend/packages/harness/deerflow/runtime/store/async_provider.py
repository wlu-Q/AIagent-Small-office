"""Asynchronous LangGraph Store factory driven by ``database``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from langgraph.store.base import BaseStore

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.runtime.store.provider import (
    POSTGRES_CONN_REQUIRED,
    POSTGRES_STORE_INSTALL,
    SQLITE_STORE_INSTALL,
)
from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _async_store(config: DatabaseConfig) -> AsyncIterator[BaseStore]:
    """Create and close an asynchronous Store for ``config``."""
    if config.backend == "memory":
        from langgraph.store.memory import InMemoryStore

        logger.info("Store: using InMemoryStore (in-process, not persistent)")
        yield InMemoryStore()
        return

    if config.backend == "sqlite":
        try:
            from langgraph.store.sqlite.aio import AsyncSqliteStore
        except ImportError as exc:
            raise ImportError(SQLITE_STORE_INSTALL) from exc

        conn_str = config.sqlite_path
        await asyncio.to_thread(ensure_sqlite_parent_dir, conn_str)
        async with AsyncSqliteStore.from_conn_string(conn_str) as store:
            await store.setup()
            logger.info("Store: using AsyncSqliteStore (%s)", conn_str)
            yield store
        return

    if config.backend == "postgres":
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore
        except ImportError as exc:
            raise ImportError(POSTGRES_STORE_INSTALL) from exc

        if not config.postgres_url:
            raise ValueError(POSTGRES_CONN_REQUIRED)
        async with AsyncPostgresStore.from_conn_string(config.postgres_url) as store:
            await store.setup()
            logger.info("Store: using AsyncPostgresStore")
            yield store
        return

    raise ValueError(f"Unknown database backend: {config.backend!r}")


@contextlib.asynccontextmanager
async def make_store(app_config: AppConfig | None = None) -> AsyncIterator[BaseStore]:
    """Yield a Store selected by the unified database configuration."""
    config = (app_config or get_app_config()).database
    async with _async_store(config) as store:
        yield store
