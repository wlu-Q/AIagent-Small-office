"""Synchronous LangGraph checkpointer factory driven by ``database``."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator

from langgraph.types import Checkpointer

from deerflow.config.app_config import get_app_config
from deerflow.config.database_config import DatabaseConfig
from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir, resolve_sqlite_conn_str

logger = logging.getLogger(__name__)

SQLITE_INSTALL = "langgraph-checkpoint-sqlite is required for the SQLite checkpointer. Install it with: uv add langgraph-checkpoint-sqlite"
POSTGRES_INSTALL = (
    "langgraph-checkpoint-postgres is required for the PostgreSQL checkpointer. "
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
def _sync_checkpointer_cm(config: DatabaseConfig) -> Iterator[Checkpointer]:
    """Create and close a synchronous checkpointer for ``config``."""
    if config.backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("Checkpointer: using InMemorySaver (in-process, not persistent)")
        yield InMemorySaver()
        return

    if config.backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        conn_str = resolve_sqlite_conn_str(config.sqlite_path)
        ensure_sqlite_parent_dir(conn_str)
        with SqliteSaver.from_conn_string(conn_str) as saver:
            saver.setup()
            logger.info("Checkpointer: using SqliteSaver (%s)", conn_str)
            yield saver
        return

    if config.backend == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            raise ImportError(POSTGRES_INSTALL) from exc

        if not config.postgres_url:
            raise ValueError(POSTGRES_CONN_REQUIRED)
        with PostgresSaver.from_conn_string(config.postgres_url) as saver:
            saver.setup()
            logger.info("Checkpointer: using PostgresSaver")
            yield saver
        return

    raise ValueError(f"Unknown database backend: {config.backend!r}")


_checkpointer: Checkpointer | None = None
_checkpointer_ctx = None
_checkpointer_lock = threading.Lock()


def get_checkpointer() -> Checkpointer:
    """Return the process-wide synchronous checkpointer."""
    global _checkpointer, _checkpointer_ctx
    if _checkpointer is not None:
        return _checkpointer

    config = _get_database_config()
    with _checkpointer_lock:
        if _checkpointer is not None:
            return _checkpointer
        checkpointer_ctx = _sync_checkpointer_cm(config)
        checkpointer = checkpointer_ctx.__enter__()
        _checkpointer_ctx = checkpointer_ctx
        _checkpointer = checkpointer
    return _checkpointer


def reset_checkpointer() -> None:
    """Close and clear the synchronous checkpointer singleton."""
    global _checkpointer, _checkpointer_ctx
    with _checkpointer_lock:
        if _checkpointer_ctx is not None:
            try:
                _checkpointer_ctx.__exit__(None, None, None)
            except Exception:
                logger.warning("Error during checkpointer cleanup", exc_info=True)
            _checkpointer_ctx = None
        _checkpointer = None


@contextlib.contextmanager
def checkpointer_context() -> Iterator[Checkpointer]:
    """Yield a fresh checkpointer and close it on context exit."""
    with _sync_checkpointer_cm(_get_database_config()) as saver:
        yield saver
