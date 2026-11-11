"""Synchronous LangGraph Store factory driven by ``database``."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator

from langgraph.store.base import BaseStore

from deerflow.config.app_config import get_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir, resolve_sqlite_conn_str

logger = logging.getLogger(__name__)

SQLITE_STORE_INSTALL = "langgraph-checkpoint-sqlite is required for the SQLite store. Install it with: uv add langgraph-checkpoint-sqlite"
POSTGRES_STORE_INSTALL = (
    "langgraph-checkpoint-postgres is required for the PostgreSQL store. "
    "Install the package extra with: pip install 'deerflow-harness[postgres]' "
    "(or use: uv sync --all-packages --extra postgres when developing locally)"
)
POSTGRES_CONN_REQUIRED = "database.postgres_url is required for the postgres backend"


def _get_database_config() -> DatabaseConfig:
    """Load persistence config without holding the provider singleton lock."""
    try:
        return get_app_config().database
    except FileNotFoundError:
        return DatabaseConfig(backend="memory")


@contextlib.contextmanager
def _sync_store_cm(config: DatabaseConfig) -> Iterator[BaseStore]:
    """Create and close a synchronous Store for ``config``."""
    if config.backend == "memory":
        from langgraph.store.memory import InMemoryStore

        logger.info("Store: using InMemoryStore (in-process, not persistent)")
        yield InMemoryStore()
        return

    if config.backend == "sqlite":
        try:
            from langgraph.store.sqlite import SqliteStore
        except ImportError as exc:
            raise ImportError(SQLITE_STORE_INSTALL) from exc

        conn_str = resolve_sqlite_conn_str(config.sqlite_path)
        ensure_sqlite_parent_dir(conn_str)
        with SqliteStore.from_conn_string(conn_str) as store:
            store.setup()
            logger.info("Store: using SqliteStore (%s)", conn_str)
            yield store
        return

    if config.backend == "postgres":
        try:
            from langgraph.store.postgres import PostgresStore
        except ImportError as exc:
            raise ImportError(POSTGRES_STORE_INSTALL) from exc

        if not config.postgres_url:
            raise ValueError(POSTGRES_CONN_REQUIRED)
        with PostgresStore.from_conn_string(config.postgres_url) as store:
            store.setup()
            logger.info("Store: using PostgresStore")
            yield store
        return

    raise ValueError(f"Unknown database backend: {config.backend!r}")


_store: BaseStore | None = None
_store_ctx = None
_store_lock = threading.Lock()


def get_store() -> BaseStore:
    """Return the process-wide synchronous Store."""
    global _store, _store_ctx
    if _store is not None:
        return _store

    config = _get_database_config()
    with _store_lock:
        if _store is not None:
            return _store
        store_ctx = _sync_store_cm(config)
        store = store_ctx.__enter__()
        _store_ctx = store_ctx
        _store = store
    return _store


def reset_store() -> None:
    """Close and clear the synchronous Store singleton."""
    global _store, _store_ctx
    with _store_lock:
        if _store_ctx is not None:
            try:
                _store_ctx.__exit__(None, None, None)
            except Exception:
                logger.warning("Error during store cleanup", exc_info=True)
            _store_ctx = None
        _store = None


@contextlib.contextmanager
def store_context() -> Iterator[BaseStore]:
    """Yield a fresh Store and close it on context exit."""
    with _sync_store_cm(_get_database_config()) as store:
        yield store
