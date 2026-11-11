# 本文件属于 Gateway 生命周期测试模块：只验证启动清理和关闭钩子的顺序、超时与错误隔离。
# 不负责 Office Run 恢复、消息路由、员工排队、模型执行或业务状态投影测试。
# 关联：gateway/app.py 编排应用生命周期，deps.py 管理 DeerFlow 与 Office 进程级资源。
"""Regression tests for bounded Gateway shutdown and startup cleanup."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


@asynccontextmanager
async def _noop_langgraph_runtime(_app, _startup_config):
    yield


@asynccontextmanager
async def _value_context(value):
    yield value


@asynccontextmanager
async def _failing_context(error: Exception):
    raise error
    yield  # pragma: no cover - required to keep this an async context manager


@pytest.mark.asyncio
async def test_scheduler_shutdown_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import app as gateway_app

    async def hang_forever() -> None:
        await asyncio.sleep(3600)

    application = FastAPI()
    application.state.scheduled_task_service = SimpleNamespace(stop=hang_forever)
    monkeypatch.setattr(gateway_app, "_SHUTDOWN_HOOK_TIMEOUT_SECONDS", 0.05)

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    await gateway_app._stop_scheduler(application)
    elapsed = loop.time() - started_at

    assert 0.04 <= elapsed < 0.5


async def _run_lifespan_with_upload_staging_cleanup():
    from app.gateway.app import lifespan

    application = FastAPI()
    startup_config = SimpleNamespace(
        log_level="INFO",
        memory=SimpleNamespace(token_counting="char"),
        scheduler=SimpleNamespace(enabled=False),
    )
    cleanup_upload_staging_files = MagicMock(return_value=2)
    shutdown_sandbox_provider = MagicMock()
    close_oidc_service = AsyncMock()
    start_scheduler = AsyncMock(return_value=None)

    with (
        patch("app.gateway.app.get_app_config", return_value=startup_config),
        patch("app.gateway.app.get_gateway_config", return_value=MagicMock(host="x", port=0)),
        patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
        patch("app.gateway.app.cleanup_stale_upload_staging_files", cleanup_upload_staging_files),
        patch("deerflow.sandbox.sandbox_provider.shutdown_sandbox_provider", shutdown_sandbox_provider),
        patch("app.gateway.app.auth.close_oidc_service", close_oidc_service),
        patch("app.gateway.app._start_scheduler_if_enabled", start_scheduler),
        patch("deerflow.agents.memory.get_memory_manager", return_value=SimpleNamespace()),
    ):
        async with lifespan(application):
            pass

    return cleanup_upload_staging_files, shutdown_sandbox_provider, close_oidc_service, start_scheduler


def test_lifespan_sweeps_upload_staging_files_on_startup() -> None:
    cleanup_upload_staging_files, shutdown_sandbox_provider, close_oidc_service, start_scheduler = asyncio.run(
        _run_lifespan_with_upload_staging_cleanup()
    )

    cleanup_upload_staging_files.assert_called_once_with()
    shutdown_sandbox_provider.assert_called_once_with()
    close_oidc_service.assert_awaited_once()
    start_scheduler.assert_awaited_once()

@pytest.mark.asyncio
async def test_runtime_closes_engine_when_startup_fails_after_initialization() -> None:
    from app.gateway.deps import langgraph_runtime

    application = FastAPI()
    startup_config = SimpleNamespace(database=SimpleNamespace(backend="sqlite"))
    init_engine = AsyncMock()
    close_engine = AsyncMock()
    startup_error = RuntimeError("checkpointer failed to start")

    with (
        patch("deerflow.persistence.engine.init_engine_from_config", init_engine),
        patch("deerflow.persistence.engine.close_engine", close_engine),
        patch("deerflow.runtime.make_stream_bridge", return_value=_value_context(object())),
        patch(
            "deerflow.runtime.checkpointer.async_provider.make_checkpointer",
            return_value=_failing_context(startup_error),
        ),
    ):
        with pytest.raises(RuntimeError, match="checkpointer failed to start"):
            async with langgraph_runtime(application, startup_config):
                pass

    init_engine.assert_awaited_once_with(startup_config.database)
    close_engine.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_lifespan_shuts_down_sandbox_after_runtime_has_drained() -> None:
    from app.gateway.app import lifespan

    events: list[str] = []

    @asynccontextmanager
    async def tracked_runtime(_app, _startup_config):
        try:
            yield
        finally:
            events.append("runtime-stopped")

    async def tracked_sandbox_shutdown() -> None:
        events.append("sandbox-stopped")

    application = FastAPI()
    startup_config = SimpleNamespace(
        log_level="INFO",
        memory=SimpleNamespace(token_counting="char"),
        scheduler=SimpleNamespace(enabled=False),
    )

    with (
        patch("app.gateway.app.get_app_config", return_value=startup_config),
        patch("app.gateway.app.get_gateway_config", return_value=MagicMock(host="x", port=0)),
        patch("app.gateway.app.langgraph_runtime", tracked_runtime),
        patch("app.gateway.app.cleanup_stale_upload_staging_files", return_value=0),
        patch("app.gateway.app._shutdown_sandbox_provider", tracked_sandbox_shutdown),
        patch("app.gateway.app.auth.close_oidc_service", new=AsyncMock()),
        patch("app.gateway.app._start_scheduler_if_enabled", new=AsyncMock(return_value=None)),
        patch("app.gateway.app._stop_scheduler", new=AsyncMock()),
        patch("deerflow.agents.memory.get_memory_manager", return_value=SimpleNamespace()),
    ):
        async with lifespan(application):
            events.append("serving")

    assert events == ["serving", "runtime-stopped", "sandbox-stopped"]
