"""Blocking-I/O regression test for asynchronous SQLite checkpointer setup."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.config.database_config import DatabaseConfig

pytestmark = pytest.mark.asyncio


async def test_async_checkpointer_sqlite_setup_does_not_block_event_loop(tmp_path: Path) -> None:
    from deerflow.runtime.checkpointer.async_provider import _async_checkpointer

    sqlite_dir = tmp_path / "subdir"
    db_file = sqlite_dir / "deerflow.db"
    config = DatabaseConfig(backend="sqlite", sqlite_dir=str(sqlite_dir))

    mock_saver = AsyncMock()
    mock_context_manager = AsyncMock()
    mock_context_manager.__aenter__.return_value = mock_saver
    mock_context_manager.__aexit__.return_value = False

    mock_saver_cls = MagicMock()
    mock_saver_cls.from_conn_string.return_value = mock_context_manager

    mock_module = MagicMock()
    mock_module.AsyncSqliteSaver = mock_saver_cls

    with patch.dict(sys.modules, {"langgraph.checkpoint.sqlite.aio": mock_module}):
        async with _async_checkpointer(config) as saver:
            assert saver is mock_saver

    assert db_file.parent.exists()
    mock_saver_cls.from_conn_string.assert_called_once_with(str(db_file.resolve()))
    mock_saver.setup.assert_awaited_once()
