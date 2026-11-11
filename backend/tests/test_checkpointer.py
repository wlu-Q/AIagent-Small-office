"""Tests for database-driven LangGraph checkpointer and Store providers."""

from __future__ import annotations

import sys
import tomllib
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

import deerflow.config.app_config as app_config_module
import deerflow.runtime.checkpointer.async_provider as async_checkpointer_provider
import deerflow.runtime.checkpointer.provider as checkpointer_provider
import deerflow.runtime.store.async_provider as async_store_provider
import deerflow.runtime.store.provider as store_provider
from deerflow.config.database_config import DatabaseConfig
from deerflow.runtime.checkpointer import get_checkpointer, reset_checkpointer
from deerflow.runtime.checkpointer.provider import POSTGRES_INSTALL
from deerflow.runtime.store import get_store, reset_store
from deerflow.runtime.store.provider import POSTGRES_STORE_INSTALL


@pytest.fixture(autouse=True)
def reset_provider_state():
    app_config_module._app_config = None
    reset_checkpointer()
    reset_store()
    yield
    reset_checkpointer()
    reset_store()
    app_config_module._app_config = None


class TestHarnessPackaging:
    def test_pyproject_declares_postgres_extra(self):
        pyproject_path = Path(__file__).resolve().parents[1] / "packages" / "harness" / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        assert data["project"]["optional-dependencies"]["postgres"] == [
            "asyncpg>=0.29",
            "langgraph-checkpoint-postgres>=3.0.5",
            "psycopg[binary]>=3.3.3",
            "psycopg-pool>=3.3.0",
        ]

    def test_workspace_pyproject_forwards_postgres_extra_to_harness(self):
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        assert data["project"]["optional-dependencies"]["postgres"] == ["deerflow-harness[postgres]"]

    def test_postgres_missing_dependency_messages_recommend_package_extra(self):
        assert "deerflow-harness[postgres]" in POSTGRES_INSTALL
        assert "deerflow-harness[postgres]" in POSTGRES_STORE_INSTALL
        assert "uv sync --all-packages --extra postgres" in POSTGRES_INSTALL
        assert "uv sync --all-packages --extra postgres" in POSTGRES_STORE_INSTALL


class TestSyncCheckpointer:
    def test_memory_backend_returns_singleton(self):
        app_config = SimpleNamespace(database=DatabaseConfig(backend="memory"))
        with patch.object(checkpointer_provider, "get_app_config", return_value=app_config):
            first = get_checkpointer()
            second = get_checkpointer()

        assert isinstance(first, InMemorySaver)
        assert second is first

    def test_reset_recreates_memory_checkpointer(self):
        app_config = SimpleNamespace(database=DatabaseConfig(backend="memory"))
        with patch.object(checkpointer_provider, "get_app_config", return_value=app_config):
            first = get_checkpointer()
            reset_checkpointer()
            second = get_checkpointer()

        assert second is not first

    def test_missing_config_file_uses_in_memory_checkpointer(self):
        with patch.object(checkpointer_provider, "get_app_config", side_effect=FileNotFoundError):
            assert isinstance(get_checkpointer(), InMemorySaver)

    def test_sqlite_backend_uses_database_sqlite_path(self, tmp_path: Path):
        config = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "state"))
        saver = MagicMock()
        manager = MagicMock()
        manager.__enter__.return_value = saver
        manager.__exit__.return_value = False
        saver_class = MagicMock()
        saver_class.from_conn_string.return_value = manager
        sqlite_module = MagicMock(SqliteSaver=saver_class)

        with patch.dict(sys.modules, {"langgraph.checkpoint.sqlite": sqlite_module}):
            with checkpointer_provider._sync_checkpointer_cm(config) as actual:
                assert actual is saver

        expected = str((tmp_path / "state" / "deerflow.db").resolve())
        saver_class.from_conn_string.assert_called_once_with(expected)
        saver.setup.assert_called_once_with()
        assert Path(expected).parent.is_dir()

    def test_postgres_backend_requires_database_url(self):
        postgres_module = MagicMock(PostgresSaver=MagicMock())
        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": postgres_module}):
            with pytest.raises(ValueError, match="database.postgres_url"):
                with checkpointer_provider._sync_checkpointer_cm(DatabaseConfig(backend="postgres")):
                    pass

    def test_postgres_backend_uses_database_url(self):
        saver = MagicMock()
        manager = MagicMock()
        manager.__enter__.return_value = saver
        manager.__exit__.return_value = False
        saver_class = MagicMock()
        saver_class.from_conn_string.return_value = manager
        postgres_module = MagicMock(PostgresSaver=saver_class)
        config = DatabaseConfig(backend="postgres", postgres_url="postgresql://localhost/deerflow")

        with patch.dict(sys.modules, {"langgraph.checkpoint.postgres": postgres_module}):
            with checkpointer_provider._sync_checkpointer_cm(config) as actual:
                assert actual is saver

        saver_class.from_conn_string.assert_called_once_with(config.postgres_url)
        saver.setup.assert_called_once_with()


class TestSyncStore:
    def test_memory_backend_returns_singleton(self):
        app_config = SimpleNamespace(database=DatabaseConfig(backend="memory"))
        with patch.object(store_provider, "get_app_config", return_value=app_config):
            first = get_store()
            second = get_store()

        assert isinstance(first, InMemoryStore)
        assert second is first

    def test_reset_recreates_memory_store(self):
        app_config = SimpleNamespace(database=DatabaseConfig(backend="memory"))
        with patch.object(store_provider, "get_app_config", return_value=app_config):
            first = get_store()
            reset_store()
            second = get_store()

        assert second is not first

    def test_sqlite_backend_uses_database_sqlite_path(self, tmp_path: Path):
        config = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "state"))
        store = MagicMock()
        manager = MagicMock()
        manager.__enter__.return_value = store
        manager.__exit__.return_value = False
        store_class = MagicMock()
        store_class.from_conn_string.return_value = manager
        sqlite_module = MagicMock(SqliteStore=store_class)

        with patch.dict(sys.modules, {"langgraph.store.sqlite": sqlite_module}):
            with store_provider._sync_store_cm(config) as actual:
                assert actual is store

        expected = str((tmp_path / "state" / "deerflow.db").resolve())
        store_class.from_conn_string.assert_called_once_with(expected)
        store.setup.assert_called_once_with()
        assert Path(expected).parent.is_dir()

    def test_postgres_backend_requires_database_url(self):
        postgres_module = MagicMock(PostgresStore=MagicMock())
        with patch.dict(sys.modules, {"langgraph.store.postgres": postgres_module}):
            with pytest.raises(ValueError, match="database.postgres_url"):
                with store_provider._sync_store_cm(DatabaseConfig(backend="postgres")):
                    pass


class TestAsyncProviders:
    @pytest.mark.anyio
    async def test_memory_checkpointer_uses_database_backend(self):
        async with async_checkpointer_provider._async_checkpointer(DatabaseConfig(backend="memory")) as saver:
            assert isinstance(saver, InMemorySaver)

    @pytest.mark.anyio
    async def test_memory_store_uses_database_backend(self):
        async with async_store_provider._async_store(DatabaseConfig(backend="memory")) as store:
            assert isinstance(store, InMemoryStore)

    @pytest.mark.anyio
    async def test_sqlite_checkpointer_uses_database_sqlite_path(self, tmp_path: Path):
        config = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "state"))
        saver = AsyncMock()
        manager = AsyncMock()
        manager.__aenter__.return_value = saver
        manager.__aexit__.return_value = False
        saver_class = MagicMock()
        saver_class.from_conn_string.return_value = manager
        sqlite_module = MagicMock(AsyncSqliteSaver=saver_class)

        with patch.dict(sys.modules, {"langgraph.checkpoint.sqlite.aio": sqlite_module}):
            async with async_checkpointer_provider._async_checkpointer(config) as actual:
                assert actual is saver

        expected = str((tmp_path / "state" / "deerflow.db").resolve())
        saver_class.from_conn_string.assert_called_once_with(expected)
        saver.setup.assert_awaited_once_with()

    @pytest.mark.anyio
    async def test_make_checkpointer_uses_supplied_app_database(self):
        expected = object()
        app_config = SimpleNamespace(database=DatabaseConfig(backend="memory"))
        received: list[DatabaseConfig] = []

        @asynccontextmanager
        async def fake_factory(config: DatabaseConfig):
            received.append(config)
            yield expected

        with patch.object(async_checkpointer_provider, "_async_checkpointer", fake_factory):
            async with async_checkpointer_provider.make_checkpointer(app_config) as actual:
                assert actual is expected

        assert received == [app_config.database]

    @pytest.mark.anyio
    async def test_make_store_uses_supplied_app_database(self):
        expected = object()
        app_config = SimpleNamespace(database=DatabaseConfig(backend="memory"))
        received: list[DatabaseConfig] = []

        @asynccontextmanager
        async def fake_factory(config: DatabaseConfig):
            received.append(config)
            yield expected

        with patch.object(async_store_provider, "_async_store", fake_factory):
            async with async_store_provider.make_store(app_config) as actual:
                assert actual is expected

        assert received == [app_config.database]


def test_database_config_drives_both_sync_contexts():
    config = DatabaseConfig(backend="memory")
    app_config = SimpleNamespace(database=config)

    with (
        patch.object(checkpointer_provider, "get_app_config", return_value=app_config),
        patch.object(store_provider, "get_app_config", return_value=app_config),
        checkpointer_provider.checkpointer_context() as saver,
        store_provider.store_context() as store,
    ):
        assert isinstance(saver, InMemorySaver)
        assert isinstance(store, InMemoryStore)
