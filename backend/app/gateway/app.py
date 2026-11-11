# 本文件属于 Gateway 应用装配模块：只创建 FastAPI 应用并协调进程启动、关闭和路由挂载。
# 不负责 Office 消息语义、员工排队、Run/Thread/Event 持久化或 Agent Loop。
# 关联：deps.py 提供生命周期资源，各 Gateway Router 提供 HTTP 边界，DeerFlow Runtime 负责执行事实。
import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.gateway.auth_disabled import is_auth_disabled, warn_if_auth_disabled_enabled
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.config import get_gateway_config
from app.gateway.csrf_middleware import CSRFMiddleware, get_configured_cors_origins
from app.gateway.deps import langgraph_runtime
from app.gateway.routers import (
    auth,
    input_polish,
    mcp,
    memory,
    models,
    office,
    scheduled_tasks,
    skills,
)
from app.gateway.trace_middleware import TraceMiddleware
from deerflow.config import app_config as deerflow_app_config
from deerflow.logging_config import DEFAULT_LOG_DATE_FORMAT, DEFAULT_LOG_FORMAT, configure_logging
from deerflow.tracing.monocle import setup_monocle_tracing_if_enabled
from deerflow.uploads.manager import cleanup_stale_upload_staging_files

AppConfig = deerflow_app_config.AppConfig
get_app_config = deerflow_app_config.get_app_config

# Default logging; lifespan overrides from config.yaml log_level.
logging.basicConfig(
    level=logging.INFO,
    format=DEFAULT_LOG_FORMAT,
    datefmt=DEFAULT_LOG_DATE_FORMAT,
)

logger = logging.getLogger(__name__)

# Upper bound (seconds) each lifespan shutdown hook is allowed to run.
# Bounds worker exit time so uvicorn's reload supervisor does not keep
# firing signals into a worker that is stuck waiting for shutdown cleanup.
_SHUTDOWN_HOOK_TIMEOUT_SECONDS = 5.0


async def _ensure_admin_user(app: FastAPI) -> None:
    """Verify whether the installation already has an administrator."""
    from app.gateway.deps import get_local_provider
    try:
        provider = get_local_provider()
    except RuntimeError:
        # Auth persistence may not be initialized in some test/boot paths.
        # Skip the bootstrap check rather than failing gateway startup.
        logger.warning("Auth persistence not ready; skipping admin bootstrap check")
        return


    admin_count = await provider.count_admin_users()

    if admin_count == 0:
        logger.info("=" * 60)
        logger.info("  First boot detected — no admin account exists.")
        logger.info("  Visit /setup to complete admin account creation.")
        logger.info("=" * 60)
        return





async def _start_scheduler_if_enabled(app: FastAPI, startup_config: AppConfig):
    """Start the scheduler only when the local operator explicitly enables it."""
    if not startup_config.scheduler.enabled:
        logger.info("Scheduled task polling is disabled")
        return None

    if getattr(app.state, "scheduled_task_repo", None) is None or getattr(app.state, "scheduled_task_run_repo", None) is None:
        logger.warning("Scheduled task polling requested but its repositories are unavailable")
        return None

    from app.gateway.services import launch_scheduled_thread_run
    from app.scheduler import ScheduledTaskService

    service = ScheduledTaskService(
        task_repo=app.state.scheduled_task_repo,
        task_run_repo=app.state.scheduled_task_run_repo,
        launch_run=lambda **kwargs: launch_scheduled_thread_run(app=app, **kwargs),
        poll_interval_seconds=startup_config.scheduler.poll_interval_seconds,
        lease_seconds=startup_config.scheduler.lease_seconds,
        max_concurrent_runs=startup_config.scheduler.max_concurrent_runs,
    )
    app.state.scheduled_task_service = service
    await service.start()
    return service


async def _stop_scheduler(app: FastAPI) -> None:
    """Stop the optional scheduler without allowing shutdown to hang forever."""
    service = getattr(app.state, "scheduled_task_service", None)
    if service is None:
        return

    try:
        await asyncio.wait_for(service.stop(), timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "Timed out after %.1f seconds while stopping scheduled task service",
            _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception("Failed to stop scheduled task service")
    finally:
        app.state.scheduled_task_service = None


async def _shutdown_sandbox_provider() -> None:
    """Release the process-wide sandbox provider before Gateway exit."""
    from deerflow.sandbox.sandbox_provider import shutdown_sandbox_provider

    try:
        await asyncio.to_thread(shutdown_sandbox_provider)
    except Exception:
        logger.exception("Failed to shut down sandbox provider")


async def _warm_memory_backend(startup_config: AppConfig) -> None:
    """Warm the configured memory backend without loading it for model-less startup."""
    if not getattr(startup_config, "models", None):
        logger.info("No models configured; skipping memory backend warm-up")
        return

    try:
        from deerflow.agents.memory import get_memory_manager

        manager = get_memory_manager()
        warm = getattr(manager, "warm", None)
        if not callable(warm):
            logger.info("Memory backend %s has no warm-up hook; skipping tiktoken warm-up", type(manager).__name__)
        else:
            warmed = await asyncio.wait_for(
                asyncio.to_thread(warm),
                timeout=5,
            )
            if warmed:
                logger.info("tiktoken encoding cache warmed successfully")
            else:
                logger.warning("tiktoken encoding cache warm-up failed; token counting will use character-based fallback until tiktoken loads successfully")
    except TimeoutError:
        logger.warning("tiktoken encoding cache warm-up timed out; token counting will use character-based fallback until tiktoken loads successfully")
    except Exception:
        logger.warning("tiktoken warm-up skipped", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""

    # Load config and check necessary environment variables at startup.
    # `startup_config` is a local snapshot used only for one-shot bootstrap
    # work (logging level and langgraph_runtime engines). Request-time
    # config resolution always routes through `get_app_config()` in
    # `app/gateway/deps.py::get_config()` so `config.yaml` edits become
    # visible without a process restart. We deliberately do NOT cache this
    # snapshot on `app.state` to keep that contract enforceable.
    try:
        startup_config = get_app_config()
        configure_logging(startup_config)
        logger.info("Configuration loaded successfully")
        warn_if_auth_disabled_enabled()
    except Exception as e:
        error_msg = f"Failed to load configuration during gateway startup: {e}"
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e
    config = get_gateway_config()
    logger.info(f"Starting API Gateway on {config.host}:{config.port}")

    # Agent observability (Monocle). Off by default; enabled with
    # MONOCLE_TRACING. Initialized here at startup — not at import time — so a
    # plain `import deerflow.agents` never installs a process-global tracer.
    # Unlike LangSmith/Langfuse, whose validation failures abort the agent run,
    # a bad Monocle config only logs: the Gateway keeps serving without tracing.
    try:
        setup_monocle_tracing_if_enabled()
    except Exception:  # observability must never break startup
        logger.exception("Monocle tracing setup failed; continuing without it")

    # Pre-warm tiktoken encoding cache so the first memory-injection request
    # never blocks on the BPE data download (which hits an OpenAI/Azure URL
    # that may be unreachable in restricted networks — see issue #3402).
    # Warm-up runs via the manager's `warm` capability (getattr-probed, so
    # non-DeerMem backends skip it). DeerMem.warm re-checks token_counting==
    # "char" and returns early, so char-mode backends never touch tiktoken
    # (avoids even the 5s probe in
    # network-restricted deployments — see issue #3429).
    await _warm_memory_backend(startup_config)

    try:
        removed_upload_staging_files = await asyncio.to_thread(cleanup_stale_upload_staging_files)
        if removed_upload_staging_files:
            logger.info("Removed %d stale upload staging file(s)", removed_upload_staging_files)
    except Exception:
        logger.warning("Upload staging file cleanup skipped", exc_info=True)

    # Initialize LangGraph runtime components (StreamBridge, RunManager, checkpointer, store)
    try:
        async with langgraph_runtime(app, startup_config):
            logger.info("LangGraph runtime initialised")

            # Auth persistence is only needed when the operator explicitly opts in.
            if not is_auth_disabled():
                await _ensure_admin_user(app)

            try:
                await _start_scheduler_if_enabled(app, startup_config)
            except Exception:
                logger.exception("Failed to initialize scheduled task service")

            try:
                yield
            finally:
                try:
                    await auth.close_oidc_service()
                except Exception:
                    logger.exception("Failed to close OIDC service")

                from deerflow.office.library_adapter import close_libraries
                await asyncio.to_thread(close_libraries)
                await _stop_scheduler(app)
    finally:
        from deerflow.office.terminal import close_terminals
        await asyncio.to_thread(close_terminals)
        await _shutdown_sandbox_provider()

    logger.info("Shutting down API Gateway")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    config = get_gateway_config()
    docs_url = "/docs" if config.enable_docs else None
    redoc_url = "/redoc" if config.enable_docs else None
    openapi_url = "/openapi.json" if config.enable_docs else None

    app = FastAPI(
        title="DeerFlow API Gateway",
        description="""
## 小小办公室 DeerFlow Gateway

The desktop client connects to one local Gateway. Employee creation, capability
routing, project binding, and run lifecycle are exposed only through `/api/office`.
DeerFlow execution remains an internal runtime behind the Office interface.
        """,
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        openapi_tags=[
            {"name": "office", "description": "Small Office employees, projects, routing, runs, events, and artifacts"},
            {"name": "models", "description": "Configured model profiles"},
            {"name": "mcp", "description": "Model Context Protocol configuration"},
            {"name": "memory", "description": "DeerFlow memory configuration"},
            {"name": "skills", "description": "Skill configuration"},
            {"name": "input-polish", "description": "Input polishing"},
            {"name": "health", "description": "Gateway health"},
        ],
    )

    # Auth: reject unauthenticated requests to non-public paths (fail-closed safety net)
    app.add_middleware(AuthMiddleware)

    # CSRF: Double Submit Cookie pattern for state-changing requests
    app.add_middleware(CSRFMiddleware)

    # CORS: the desktop renderer may use a separate local origin during
    # development. Explicit origins keep CORS and CSRF checks aligned.
    cors_origins = sorted(get_configured_cors_origins())
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Request trace correlation: when logging.enhance.enabled=true, bind one
    # trace id per Gateway HTTP request and write it to response start headers.
    # `logging` is registered as restart-required (see reload_boundary.py) so we
    # snapshot the flag from the startup AppConfig instead of reading live; a
    # runtime toggle would otherwise leave the log formatter (installed once by
    # configure_logging() at lifespan startup) out of sync with the middleware.
    app.add_middleware(TraceMiddleware, enabled=_resolve_trace_enabled_for_app_construction())

    # Current product surfaces. DeerFlow RunManager and ThreadStore remain
    # internal implementation modules behind the Office interface.
    app.include_router(models.router)
    app.include_router(mcp.router)
    app.include_router(memory.router)
    app.include_router(skills.router)
    app.include_router(scheduled_tasks.router)
    from app.gateway.routers import office_library
    app.include_router(office_library.router)
    app.include_router(office.router)
    app.include_router(input_polish.router)
    app.include_router(auth.router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint.

        Returns:
            Service health status information.
        """
        return {"status": "healthy", "service": "deer-flow-gateway"}

    return app


def _resolve_trace_enabled_for_app_construction() -> bool:
    """Resolve the trace middleware flag without making imports require config.yaml."""
    try:
        return deerflow_app_config.is_trace_correlation_enabled(get_app_config())
    except FileNotFoundError:
        # Startup lifespan still performs strict config loading before serving.
        logger.debug("config.yaml not found while constructing Gateway app; TraceMiddleware disabled for this app instance")
        return False


# Create app instance for uvicorn
app = create_app()
