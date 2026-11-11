# 本文件属于 Gateway 依赖与生命周期装配模块：只创建进程级服务、提供依赖并连接启动与关闭钩子。
# 不负责 Office 消息语义、员工路由、Run 业务编排、对话存储或 Agent Loop。
# 关联：Gateway 各 router 读取本模块装配的窄服务，DeerFlow runtime 仍负责 Run、恢复和事件事实。
"""Centralized accessors for singleton objects stored on ``app.state``.

**Getters** (used by routers): raise 503 when a required dependency is
missing, except ``get_store`` which returns ``None``.

``AppConfig`` is intentionally *not* cached on ``app.state``. Routers and the
run path resolve it through :func:`deerflow.config.app_config.get_app_config`,
which performs mtime-based hot reload, so edits to ``config.yaml`` take
effect on the next request without a process restart. The engines created in
:func:`langgraph_runtime` (stream bridge, persistence, checkpointer, store,
run-event store) accept a ``startup_config`` snapshot — they are
restart-required by design and stay bound to that snapshot to keep the live
process consistent with itself.

Initialization is handled directly in ``app.py`` via :class:`AsyncExitStack`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypeVar, cast

from fastapi import FastAPI, HTTPException, Request
from langgraph.types import Checkpointer

from app.gateway.model_bindings import model_profile_exists
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.persistence.feedback import FeedbackRepository
from deerflow.runtime import RunContext, RunManager, StreamBridge
from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)

# Upper bound (seconds) for draining in-flight runs during shutdown, before the
# AsyncExitStack tears down the checkpointer (and its connection pool). Kept
# local to avoid an app -> deps -> app import cycle. This is a *separate* budget
# from ``app.gateway.app._SHUTDOWN_HOOK_TIMEOUT_SECONDS`` (currently also 5.0s,
# which bounds channel-service stop): the two govern independent teardown steps
# and may diverge, but both count toward the lifespan shutdown window — revisit
# them together if their sum must stay within the server's graceful-shutdown
# timeout.
_RUN_DRAIN_TIMEOUT_SECONDS = 5.0


async def _drain_inflight_runs(run_manager: RunManager) -> None:
    """Drain in-flight runs before the checkpointer is torn down (issue #3373).

    Shields the (internally-bounded) drain so that even if the lifespan
    coroutine is itself cancelled mid-shutdown — a second SIGINT or the server's
    graceful-shutdown timeout, i.e. the same signal storm behind #3373 — the
    checkpointer pool is not closed while run tasks are still writing
    checkpoints. On such a cancellation we let the already-running drain finish
    (it is bounded by ``RunManager.shutdown``'s own timeout) and then propagate
    the cancellation.
    """
    drain = asyncio.create_task(run_manager.shutdown(timeout=_RUN_DRAIN_TIMEOUT_SECONDS))
    try:
        await asyncio.shield(drain)
    except asyncio.CancelledError:
        # Re-shield so this second wait does not abandon the in-flight drain;
        # it is bounded, so this cannot hang. Then re-raise to honour shutdown.
        try:
            await asyncio.shield(drain)
        except Exception:
            logger.exception("In-flight run drain failed after shutdown cancellation")
        raise
    except Exception:
        logger.exception("Failed to drain in-flight runs during shutdown")


if TYPE_CHECKING:
    from app.gateway.auth.local_provider import LocalAuthProvider
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from deerflow.persistence.thread_meta.base import ThreadMetaStore
    from deerflow.runtime import RunRecord


T = TypeVar("T")

RunLifecycleHook = Callable[[object], Awaitable[None]]


def _compose_run_lifecycle_hooks(
    *named_hooks: tuple[str, RunLifecycleHook | None],
) -> RunLifecycleHook | None:
    """Run every registered hook without letting one projection block another."""
    hooks = [(name, hook) for name, hook in named_hooks if hook is not None]
    if not hooks:
        return None

    async def dispatch(record: object) -> None:
        for name, hook in hooks:
            try:
                await hook(record)
            except Exception:
                logger.exception(
                    "%s run lifecycle hook failed for run %s",
                    name,
                    getattr(record, "run_id", "unknown"),
                )

    return dispatch


async def _initialize_office_runtime_services(
    app: FastAPI,
    config: AppConfig,
) -> None:
    """Create the process-wide office services before durable runs recover."""
    from deerflow.config.paths import get_paths
    from deerflow.office.bootstrap import OfficeBootstrapService
    from deerflow.office.employees import OfficeEmployeeService
    from deerflow.office.group_messages import GroupMessageStore
    from deerflow.office.knowledge_compactor import ProjectKnowledgeCompactor, summarize_facts
    from deerflow.office.message_pipeline import OfficeMessagePipeline
    from deerflow.office.project_knowledge import ProjectKnowledgeStore
    from deerflow.office.project_knowledge_service import ProjectKnowledgeService
    from deerflow.office.projects import OfficeProjectService
    from deerflow.office.run_gate import EmployeeRunGate, install_process_employee_run_gate
    from deerflow.office.run_launch import OfficeRunLauncher
    from deerflow.office.run_projection import OfficeRunProjection
    from deerflow.office.run_view import OfficeRunView
    from deerflow.office.workflow_knowledge import WorkflowKnowledgeSource
    from deerflow.office.workflows import WorkflowStore

    user_id = os.getenv("SMALL_OFFICE_USER_ID", "default")
    employee_run_gate = EmployeeRunGate()
    install_process_employee_run_gate(employee_run_gate)
    app.state.office_employee_run_gate = employee_run_gate
    paths = get_paths()
    from deerflow.office.library_adapter import library_for_user
    await asyncio.to_thread(library_for_user, user_id)
    from deerflow.office.execution_facts import ExecutionReceiptStore
    ExecutionReceiptStore(paths=paths, user_id=user_id).mark_unresolved_unknown()
    employee_service = OfficeEmployeeService(
        paths=paths,
        thread_store=app.state.thread_store,
        checkpointer=app.state.checkpointer,
        run_manager=app.state.run_manager,
        user_id=user_id,
        model_profile_exists=lambda name: model_profile_exists(
            get_app_config(),
            name,
        ),
    )
    app.state.office_project_service = OfficeProjectService(paths=paths, user_id=user_id)
    app.state.office_bootstrap_service = OfficeBootstrapService(
        paths=paths,
        user_id=user_id,
        config_path=AppConfig.resolve_config_path(),
        employee_service=employee_service,
        project_service=app.state.office_project_service,
    )
    app.state.office_employee_service = employee_service
    group_store = GroupMessageStore(paths=employee_service.paths, user_id=user_id)
    app.state.office_group_message_store = group_store
    workflow_store = WorkflowStore(paths=paths, user_id=user_id)
    knowledge_store = ProjectKnowledgeStore(paths=paths, user_id=user_id)
    project_knowledge = ProjectKnowledgeService(knowledge_store, WorkflowKnowledgeSource(workflow_store))
    app.state.office_workflow_store = workflow_store
    app.state.office_project_knowledge_store = knowledge_store
    app.state.office_project_knowledge = project_knowledge
    app.state.office_run_projection = OfficeRunProjection(
        employee_service=employee_service,
        user_id=user_id,
        employee_run_gate=employee_run_gate,
        group_store=group_store,
        checkpointer=app.state.checkpointer,
    )
    app.state.office_run_view = OfficeRunView(
        employee_service=employee_service,
        run_manager=app.state.run_manager,
        event_store=app.state.run_event_store,
        checkpointer=app.state.checkpointer,
        user_id=user_id,
    )
    app.state.office_run_launcher = OfficeRunLauncher(
        employee_service=employee_service,
        checkpointer=app.state.checkpointer,
        project_knowledge=project_knowledge,
    )
    app.state.office_message_pipeline = OfficeMessagePipeline(
        user_id=user_id,
        employee_service=employee_service,
        group_store=group_store,
        employee_run_gate=employee_run_gate,
        run_launcher=app.state.office_run_launcher,
        run_view=app.state.office_run_view,
        project_service=app.state.office_project_service,
    )
    from deerflow.office.workflow_service import WorkflowService

    async def summarize_project_knowledge(candidates):
        pm = employee_service.get_employee_summary('pm')
        model_name = pm.get('modelProfileId') or employee_service.default_model_profile_id
        if not model_name:
            raise RuntimeError('Project knowledge compaction requires the configured PM model')
        return await summarize_facts(model_name, candidates)

    knowledge_compactor = ProjectKnowledgeCompactor(
        knowledge_store,
        summarize_project_knowledge,
    )
    app.state.office_knowledge_compactor = knowledge_compactor
    app.state.office_run_projection.knowledge_compactor = knowledge_compactor

    app.state.office_workflow_service = WorkflowService(store=workflow_store,
        project_service=app.state.office_project_service)

    from deerflow.office.group_reset import OfficeGroupReset

    app.state.office_group_reset = OfficeGroupReset(
        pipeline=app.state.office_message_pipeline,
        workflow_service=app.state.office_workflow_service,
        group_store=group_store,
        project_service=app.state.office_project_service,
        run_manager=app.state.run_manager,
        run_event_store=app.state.run_event_store,
        user_id=user_id,
    )


async def _resume_persisted_runs(app: FastAPI, recovered_runs: list[RunRecord]) -> None:
    """Prepare every durable run before launching any recovery worker."""
    if not recovered_runs:
        return

    from app.gateway.auth_disabled import (
        AUTH_DISABLED_USER_ID,
        AUTH_SOURCE_AUTH_DISABLED,
        AUTH_SOURCE_INTERNAL,
        get_auth_disabled_user,
        is_auth_disabled,
    )
    from app.gateway.services import launch_prepared_run, prepare_persisted_run

    prepared_runs = []
    local_provider = None
    for record in recovered_runs:
        if is_auth_disabled() and str(record.user_id) == AUTH_DISABLED_USER_ID:
            owner_user = get_auth_disabled_user()
            auth_source = AUTH_SOURCE_AUTH_DISABLED
        else:
            if local_provider is None:
                local_provider = get_local_provider()
            owner_user = await local_provider.get_user(str(record.user_id))
            if owner_user is None:
                raise RuntimeError(
                    f"Run {record.run_id} owner {record.user_id!r} no longer exists"
                )
            auth_source = AUTH_SOURCE_INTERNAL

        request = SimpleNamespace(
            app=app,
            state=SimpleNamespace(user=owner_user, auth_source=auth_source),
            cookies={},
            headers={},
        )
        prepared_runs.append(
            await prepare_persisted_run(record, request, recovery=True)
        )

    for prepared in prepared_runs:
        launch_prepared_run(prepared)


async def _restore_office_execution_leases(
    app: FastAPI,
    recovered_runs: list[RunRecord],
) -> set[str]:
    """Restore Office admission facts before DeerFlow recovery workers start."""
    from deerflow.office.run_gate import QueuedOfficeRun

    employee_gate = None
    active_employee_ids: set[str] = set()
    for record in recovered_runs:
        metadata = getattr(record, "metadata", None)
        if not isinstance(metadata, Mapping) or "office_employee_id" not in metadata:
            continue

        if employee_gate is None:
            employee_gate = app.state.office_employee_run_gate

        run_id = str(record.run_id)
        employee_id = str(metadata.get("office_employee_id") or "").strip()
        lease_id = str(metadata.get("office_lease_id") or "").strip()
        conversation_kind = metadata.get("office_conversation_kind")
        if (
            not employee_id
            or not lease_id
            or conversation_kind not in {"private", "group"}
        ):
            raise RuntimeError(f"Run {run_id} has invalid Office recovery metadata")

        queue_id = str(metadata.get("office_queue_id") or "").strip() or f"run:{run_id}"
        task_summary = str(metadata.get("office_task_summary") or "").strip()
        item = QueuedOfficeRun(
            queue_id=queue_id,
            target_employee_id=employee_id,
            source_kind="direct",
            text=task_summary or f"Recover Office run {run_id}",
            enqueued_at=0,
            conversation_kind=conversation_kind,
        )
        await employee_gate.restore_active(item, lease_id)
        active_employee_ids.add(employee_id)
    return active_employee_ids


def get_config() -> AppConfig:
    """Return the freshest ``AppConfig`` for the current request.

    Routes through :func:`deerflow.config.app_config.get_app_config`, which
    honours runtime ``ContextVar`` overrides and reloads ``config.yaml`` from
    disk when its mtime changes. ``AppConfig`` is not cached on ``app.state``
    at all — the only startup-time snapshot lives as a local
    ``startup_config`` variable inside ``lifespan()`` and is passed
    explicitly into :func:`langgraph_runtime` for the engines that are
    restart-required by design. Routing every request through
    :func:`get_app_config` closes the bytedance/deer-flow issue #3107 BUG-001
    split-brain where the worker / lead-agent thread saw a stale startup
    snapshot.

    Hot-reload boundary: fields backed by startup-time singletons
    (engines, sandbox provider, scheduler, logging handler) require a
    process restart to change at runtime. The authoritative list lives in
    :mod:`deerflow.config.reload_boundary` and is mirrored by the
    standardised ``"startup-only:"`` prefix on the matching
    ``Field(description=...)`` in :class:`AppConfig` — IDE hover on those
    fields will surface the boundary inline. See
    ``backend/CLAUDE.md`` "Config Hot-Reload Boundary" for the operator
    summary.

    Any failure to materialise the config (missing file, permission denied,
    YAML parse error, validation error) is reported as 503 — semantically
    "the gateway cannot serve requests without a usable configuration" — and
    logged with the original exception so operators have something to debug.
    """
    try:
        return get_app_config()
    except Exception as exc:  # noqa: BLE001 - request boundary: log and degrade gracefully
        logger.exception("Failed to load AppConfig at request time")
        raise HTTPException(status_code=503, detail="Configuration not available") from exc


@asynccontextmanager
async def langgraph_runtime(app: FastAPI, startup_config: AppConfig) -> AsyncGenerator[None, None]:
    """Bootstrap and tear down all LangGraph runtime singletons.

    ``startup_config`` is the ``AppConfig`` snapshot taken once during
    ``lifespan()`` for one-shot infrastructure bootstrap. The engines and
    stores constructed here (stream bridge, persistence engine, checkpointer,
    store, run-event store) are restart-required by design — they hold live
    connections, file handles, or singleton providers — so they bind to this
    snapshot and survive across `config.yaml` edits. Request-time consumers
    must still go through :func:`get_config` for any field that should be
    hot-reloadable. See ``backend/CLAUDE.md`` "Config Hot-Reload Boundary".

    The matching ``run_events_config`` is frozen onto ``app.state`` so
    :func:`get_run_context` pairs a freshly-loaded ``AppConfig`` with the
    *startup-time* run-events configuration the underlying ``event_store``
    was built from — otherwise the runtime could end up combining a live
    new ``run_events_config`` with an event store still bound to the
    previous backend.

    Usage in ``app.py``::

        async with langgraph_runtime(app, startup_config):
            yield
    """
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
    from deerflow.runtime import make_store, make_stream_bridge
    from deerflow.runtime.checkpointer.async_provider import make_checkpointer
    from deerflow.runtime.events.store import make_run_event_store

    async with AsyncExitStack() as stack:
        config = startup_config

        app.state.stream_bridge = await stack.enter_async_context(make_stream_bridge(config))

        # Initialize persistence engine BEFORE checkpointer so that
        # auto-create-database logic runs first (postgres backend).
        try:
            await init_engine_from_config(config.database)
        except Exception:
            await close_engine()
            raise
        stack.push_async_callback(close_engine)

        app.state.checkpointer = await stack.enter_async_context(make_checkpointer(config))
        app.state.store = await stack.enter_async_context(make_store(config))

        # Initialize repositories — one get_session_factory() call for all.
        sf = get_session_factory()
        if sf is not None:
            from deerflow.persistence.feedback import FeedbackRepository
            from deerflow.persistence.run import RunRepository

            app.state.run_store = RunRepository(sf)
            app.state.feedback_repo = FeedbackRepository(sf)
        else:
            from deerflow.runtime.runs.store.memory import MemoryRunStore

            app.state.run_store = MemoryRunStore()
            app.state.feedback_repo = None

        from deerflow.persistence.thread_meta import make_thread_store

        app.state.thread_store = make_thread_store(sf, app.state.store)
        if sf is not None:
            from deerflow.persistence.scheduled_task_runs import (
                ScheduledTaskRunRepository,
            )
            from deerflow.persistence.scheduled_tasks import ScheduledTaskRepository

            app.state.scheduled_task_repo = ScheduledTaskRepository(sf)
            app.state.scheduled_task_run_repo = ScheduledTaskRunRepository(sf)
        else:
            app.state.scheduled_task_repo = None
            app.state.scheduled_task_run_repo = None

        # Run event store. The store and the matching ``run_events_config`` are
        # both frozen at startup so ``get_run_context`` does not combine a
        # freshly-reloaded ``AppConfig.run_events`` with a store still bound to
        # the previous backend.
        run_events_config = getattr(config, "run_events", None)
        app.state.run_events_config = run_events_config
        app.state.run_event_store = make_run_event_store(run_events_config)

        # Restore the exact durable run identity and continue it from the owned
        # checkpoint. Preparation is all-or-nothing: no worker starts until every
        # persisted row has passed owner, launch-contract, agent, and checkpoint
        # validation.
        app.state.run_manager = RunManager(store=app.state.run_store)
        await _initialize_office_runtime_services(app, config)
        from deerflow.utils.time import now_iso

        recovered_runs = await app.state.run_manager.restore_inflight_runs(
            before=now_iso(),
        )
        active_office_employee_ids = await _restore_office_execution_leases(
            app,
            recovered_runs,
        )
        await app.state.office_run_projection.reconcile_startup(
            active_office_employee_ids,
        )
        # Thread ownership rows have no other self-heal path: losing one 404s every
        # future run on that thread, permanently. Repair additively before native recovery, and never let a repair failure block startup.
        try:
            if app.state.office_bootstrap_service.get_status().get("initialized"):
                repaired_threads = await app.state.office_employee_service.repair_threads()
                if repaired_threads:
                    logger.warning(
                        "Repaired %d missing Office thread(s) at startup: %s",
                        len(repaired_threads),
                        repaired_threads,
                    )
        except Exception:
            logger.exception("Failed to repair Office threads during startup")
        await app.state.office_message_pipeline.restore_waits()
        await _resume_persisted_runs(app, recovered_runs)
        try:
            yield
        finally:
            message_pipeline = getattr(app.state, "office_message_pipeline", None)
            if message_pipeline is not None:
                try:
                    await message_pipeline.shutdown()
                except Exception:
                    logger.exception("Failed to drain Office message tasks during shutdown")
            # Drain in-flight run tasks BEFORE the AsyncExitStack tears down the
            # checkpointer (and its connection pool). A run still mid-graph would
            # otherwise leak into asyncio.run() shutdown, where langgraph's
            # _checkpointer_put_after_previous aput races the closed pool and
            # raises PoolClosed (issue #3373).
            run_manager = getattr(app.state, "run_manager", None)
            if run_manager is not None:
                await _drain_inflight_runs(run_manager)


# ---------------------------------------------------------------------------
# Getters – called by routers per-request
# ---------------------------------------------------------------------------


def _require(attr: str, label: str) -> Callable[[Request], T]:
    """Create a FastAPI dependency that returns ``app.state.<attr>`` or 503."""

    def dep(request: Request) -> T:
        val = getattr(request.app.state, attr, None)
        if val is None:
            raise HTTPException(status_code=503, detail=f"{label} not available")
        return cast(T, val)

    dep.__name__ = dep.__qualname__ = f"get_{attr}"
    return dep


get_stream_bridge: Callable[[Request], StreamBridge] = _require("stream_bridge", "Stream bridge")
get_run_manager: Callable[[Request], RunManager] = _require("run_manager", "Run manager")
get_checkpointer: Callable[[Request], Checkpointer] = _require("checkpointer", "Checkpointer")
get_run_event_store: Callable[[Request], RunEventStore] = _require("run_event_store", "Run event store")
get_feedback_repo: Callable[[Request], FeedbackRepository] = _require("feedback_repo", "Feedback")
get_run_store: Callable[[Request], RunStore] = _require("run_store", "Run store")


def get_store(request: Request):
    """Return the global store (may be ``None`` if not configured)."""
    return getattr(request.app.state, "store", None)


def get_thread_store(request: Request) -> ThreadMetaStore:
    """Return the thread metadata store (SQL or memory-backed)."""
    val = getattr(request.app.state, "thread_store", None)
    if val is None:
        raise HTTPException(status_code=503, detail="Thread metadata store not available")
    return val


def get_scheduled_task_repo(request: Request):
    val = getattr(request.app.state, "scheduled_task_repo", None)
    if val is None:
        raise HTTPException(status_code=503, detail="Scheduled task repo not available")
    return val


def get_scheduled_task_run_repo(request: Request):
    val = getattr(request.app.state, "scheduled_task_run_repo", None)
    if val is None:
        raise HTTPException(status_code=503, detail="Scheduled task run repo not available")
    return val


def get_scheduled_task_service(request: Request):
    val = getattr(request.app.state, "scheduled_task_service", None)
    if val is None:
        raise HTTPException(status_code=503, detail="Scheduled task service not available")
    return val


def get_run_context(request: Request) -> RunContext:
    """Build a :class:`RunContext` from ``app.state`` singletons.

    Returns a *base* context with infrastructure dependencies. The
    ``app_config`` field is resolved live so per-run fields (e.g.
    ``models[*].max_tokens``) follow ``config.yaml`` edits; the
    ``event_store`` / ``run_events_config`` pair stays frozen to the snapshot
    captured in :func:`langgraph_runtime` so callers never see a store bound
    to one backend paired with a config pointing at another.
    """
    office_projection = getattr(request.app.state, "office_run_projection", None)
    scheduler = getattr(request.app.state, "scheduled_task_service", None)
    return RunContext(
        checkpointer=get_checkpointer(request),
        store=get_store(request),
        event_store=get_run_event_store(request),
        run_events_config=getattr(request.app.state, "run_events_config", None),
        thread_store=get_thread_store(request),
        app_config=get_config(),
        on_run_started=_compose_run_lifecycle_hooks(
            (
                "office projection started",
                getattr(office_projection, "handle_run_started", None),
            ),
        ),
        on_run_input_checkpointed=_compose_run_lifecycle_hooks(
            (
                "office projection input checkpointed",
                getattr(
                    office_projection,
                    "handle_run_input_checkpointed",
                    None,
                ),
            ),
        ),
        on_run_completed=_compose_run_lifecycle_hooks(
            (
                "office projection completed",
                getattr(office_projection, "handle_run_completed", None),
            ),
            (
                "scheduled task completed",
                getattr(scheduler, "handle_run_completion", None),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Auth helpers (used by authz.py and auth middleware)
# ---------------------------------------------------------------------------

# Cached singletons to avoid repeated instantiation per request
_cached_local_provider: LocalAuthProvider | None = None
_cached_repo: SQLiteUserRepository | None = None


def get_local_provider() -> LocalAuthProvider:
    """Get or create the cached LocalAuthProvider singleton.

    Must be called after ``init_engine_from_config()`` — the shared
    session factory is required to construct the user repository.
    """
    global _cached_local_provider, _cached_repo
    if _cached_repo is None:
        from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        if sf is None:
            raise RuntimeError("get_local_provider() called before init_engine_from_config(); cannot access users table")
        _cached_repo = SQLiteUserRepository(sf)
    if _cached_local_provider is None:
        from app.gateway.auth.local_provider import LocalAuthProvider

        _cached_local_provider = LocalAuthProvider(repository=_cached_repo)
    return _cached_local_provider


async def get_current_user_from_request(request: Request):
    """Get the current authenticated user from the request cookie.

    Raises HTTPException 401 if not authenticated.
    """
    state = getattr(request, "state", None)
    state_user = getattr(state, "user", None)
    from app.gateway.auth_disabled import AUTH_SOURCE_AUTH_DISABLED, AUTH_SOURCE_INTERNAL, AUTH_SOURCE_SESSION

    if state_user is not None and getattr(state, "auth_source", None) in {
        AUTH_SOURCE_SESSION,
        AUTH_SOURCE_AUTH_DISABLED,
        AUTH_SOURCE_INTERNAL,
    }:
        return state_user

    from app.gateway.auth import decode_token
    from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse, TokenError, token_error_to_code

    access_token = request.cookies.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.NOT_AUTHENTICATED, message="Not authenticated").model_dump(),
        )

    payload = decode_token(access_token)
    if isinstance(payload, TokenError):
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=token_error_to_code(payload), message=f"Token error: {payload.value}").model_dump(),
        )

    provider = get_local_provider()
    user = await provider.get_user(payload.sub)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.USER_NOT_FOUND, message="User not found").model_dump(),
        )

    # Token version mismatch → password was changed, token is stale
    if user.token_version != payload.ver:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.TOKEN_INVALID, message="Token revoked (password changed)").model_dump(),
        )

    return user


async def require_admin_user(request: Request, *, detail: str) -> None:
    """Require the authenticated caller to be an admin user.

    ``AuthMiddleware`` normally stamps ``request.state.user`` before the request
    reaches a router. Falling back to the strict dependency keeps the route safe
    in tests or alternative ASGI compositions that mount a router without the
    global middleware. ``detail`` is the route-specific 403 message.

    Centralising this here means a future change to the admin definition (e.g.
    allowing an internal system role, adding audit logging, or switching to a
    permission-based check) lands in one place instead of drifting across the
    per-router copies that previously existed in multiple administration routes.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        user = await get_current_user_from_request(request)

    if getattr(user, "system_role", None) != "admin":
        raise HTTPException(status_code=403, detail=detail)


async def get_optional_user_from_request(request: Request):
    """Get optional authenticated user from request.

    Returns None if not authenticated.
    """
    try:
        return await get_current_user_from_request(request)
    except HTTPException:
        return None


async def get_current_user(request: Request) -> str | None:
    """Extract user_id from request cookie, or None if not authenticated.

    Thin adapter that returns the string id for callers that only need
    identification (e.g., ``feedback.py``). Full-user callers should use
    ``get_current_user_from_request`` or ``get_optional_user_from_request``.
    """
    user = await get_optional_user_from_request(request)
    return str(user.id) if user else None

