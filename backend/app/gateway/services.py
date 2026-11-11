# 本文件属于 DeerFlow Gateway Run 服务模块：只把启动、恢复和分支请求交给原生 RunManager 与 Agent 执行链。
# 不负责 Office Mention 路由、员工 FIFO、群聊保存、员工状态投影或另一套 Run 生命周期。
# 关联：office/run_launch.py 复用本模块入口，runtime/runs/manager.py 保存唯一 Run 运行事实。
"""Internal DeerFlow run lifecycle used by Office and the scheduler."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import convert_to_messages

from app.gateway.auth_disabled import (
    AUTH_DISABLED_USER_ID,
    AUTH_SOURCE_INTERNAL,
    get_auth_disabled_user,
    is_auth_disabled,
)
from app.gateway.deps import get_checkpointer, get_local_provider, get_run_context, get_run_manager, get_stream_bridge
from app.gateway.model_bindings import (
    SELECTED_MODEL_NAME,
    ModelBindingError,
    resolve_run_app_config,
    resolve_runtime_model_id,
)
from app.gateway.utils import sanitize_log_param
from deerflow.config.app_config import get_app_config
from deerflow.runtime import (
    ConflictError,
    DisconnectMode,
    RunManager,
    RunRecord,
    StreamBridge,
    UnsupportedStrategyError,
    run_agent,
)
from deerflow.runtime.context_keys import (
    OFFICE_PROJECT_ID_CONTEXT_KEY,
    OFFICE_PROJECT_KNOWLEDGE_CONTEXT_KEY,
    OFFICE_PROJECT_PATH_CONTEXT_KEY,
    OFFICE_WORK_CONTEXT_KEY,
    OFFICE_WORK_IDENTITY_CONTEXT_KEY,
    RUN_EXECUTION_MODE_CONTEXT_KEY,
    RUNTIME_MODEL_ID_METADATA_KEY,
)
from deerflow.runtime.goal import goal_thread_lock
from deerflow.runtime.runs.execution import (
    CHECKPOINT_RUN_ID_METADATA_KEY,
    RunExecutionSpecV1,
    RunExecutionSpecValidationError,
    RunLaunchContractV1,
    RunLaunchContractValidationError,
    checkpoint_run_id,
    find_owned_checkpoint_config,
)
from deerflow.runtime.runs.naming import resolve_root_run_name
from deerflow.runtime.secret_context import redact_config_secrets
from deerflow.runtime.user_context import reset_current_user, set_current_user
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input / config helpers
# ---------------------------------------------------------------------------


def normalize_stream_modes(raw: list[str] | str | None) -> list[str]:
    """Normalize the stream_mode parameter to a list.

    Default matches what ``useStream`` expects: values + messages-tuple.
    """
    if raw is None:
        return ["values"]
    if isinstance(raw, str):
        return [raw]
    return raw if raw else ["values"]


def _office_employee_model_profile_ids(
    metadata: Mapping[str, Any] | None,
) -> list[str]:
    snapshot = (metadata or {}).get('office_employee_registry_snapshot')
    if not isinstance(snapshot, list):
        return []
    return list(
        dict.fromkeys(
            str(entry.get('model_profile_id') or '').strip()
            for entry in snapshot
            if isinstance(entry, Mapping)
            and str(entry.get('model_profile_id') or '').strip()
        )
    )


def _resolve_run_model_config(
    base_config: Any,
    model_profile_id: str,
    metadata: Mapping[str, Any] | None,
):
    employee_profile_ids = _office_employee_model_profile_ids(metadata)
    if employee_profile_ids:
        return resolve_run_app_config(
            base_config,
            model_profile_id,
            additional_profile_ids=employee_profile_ids,
        )
    return resolve_run_app_config(base_config, model_profile_id)


def _strip_external_message_metadata(message: Any) -> Any:
    """Remove server-owned metadata from an untrusted input message."""
    if not isinstance(message, BaseMessage) or ORIGINAL_USER_CONTENT_KEY not in message.additional_kwargs:
        return message
    additional_kwargs = dict(message.additional_kwargs)
    additional_kwargs.pop(ORIGINAL_USER_CONTENT_KEY, None)
    return message.model_copy(update={"additional_kwargs": additional_kwargs})


def normalize_input(raw_input: dict[str, Any] | None, *, trusted_internal: bool = False) -> dict[str, Any]:
    """Convert LangGraph Platform input format to LangChain state dict.

    Delegates dict→message coercion to ``langchain_core.messages.utils.convert_to_messages``
    so that ``additional_kwargs`` (e.g. uploaded-file metadata — gh #3132), ``id``,
    ``name``, and non-human roles (ai/system/tool) survive unchanged.  An earlier
    hand-rolled version only forwarded ``content`` and collapsed every role to
    ``HumanMessage``, which silently stripped frontend-supplied attachments.

    Malformed message dicts (missing ``role``/``type``/``content``, unsupported
    role, etc.) raise ``HTTPException(400)`` with the offending index, instead
    of bubbling up as a 500.  The gateway is a system boundary, so per-entry
    validation errors are the right shape for clients to retry against.

    ``original_user_content`` is server-owned provenance used to undo model-only
    sanitization at persistence time. External callers cannot supply it; trusted
    in-process callers may preserve a value produced by server-side preprocessing.
    """
    if raw_input is None:
        return {}
    messages = raw_input.get("messages")
    if messages and isinstance(messages, list):
        converted: list[Any] = []
        for index, msg in enumerate(messages):
            if isinstance(msg, BaseMessage):
                converted.append(msg)
            elif isinstance(msg, dict):
                try:
                    converted.extend(convert_to_messages([msg]))
                except (ValueError, TypeError, NotImplementedError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid message at input.messages[{index}]: {exc}",
                    ) from exc
            else:
                converted.append(msg)
        if not trusted_internal:
            converted = [_strip_external_message_metadata(message) for message in converted]
        return {**raw_input, "messages": converted}
    return raw_input


_DEFAULT_ASSISTANT_ID = "lead_agent"
_PREPARED_VALUE_MISSING = object()


# Runtime-domain values accepted from the current run context.
_RUNTIME_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "model_name",
        "mode",
        "thinking_enabled",
        "reasoning_effort",
        "is_plan_mode",
        "subagent_enabled",
        "max_concurrent_subagents",
        "max_total_subagents",
        "agent_name",
        "is_bootstrap",
    }
)

# Keys honored only for trusted in-process callers. Scheduler autonomy and
# verified Office workspace bindings must never be accepted from arbitrary HTTP
# run requests.
_CONTEXT_INTERNAL_CALLER_KEYS: frozenset[str] = frozenset({"non_interactive"})

_CONTEXT_TRUSTED_OFFICE_KEYS: frozenset[str] = frozenset(
    {
        OFFICE_PROJECT_ID_CONTEXT_KEY,
        OFFICE_PROJECT_PATH_CONTEXT_KEY,
        "office_workflow",
        "office_tool_contract",
        "office_execution_workspace",
        OFFICE_PROJECT_KNOWLEDGE_CONTEXT_KEY,
        OFFICE_WORK_CONTEXT_KEY,
        OFFICE_WORK_IDENTITY_CONTEXT_KEY,
    }
)
_CONTEXT_SERVER_OWNED_KEYS = (
    _CONTEXT_INTERNAL_CALLER_KEYS | _CONTEXT_TRUSTED_OFFICE_KEYS
)


def strip_internal_context_keys(config: dict[str, Any]) -> None:
    """Drop server-owned values from an external runtime context."""
    runtime_context = config.get("context")
    if isinstance(runtime_context, dict):
        for key in _CONTEXT_SERVER_OWNED_KEYS:
            runtime_context.pop(key, None)


def merge_trusted_office_context(
    config: dict[str, Any],
    context: Mapping[str, Any] | None,
) -> None:
    if not context:
        return
    runtime_context = config.setdefault("context", {})
    if not isinstance(runtime_context, dict):
        raise ValueError("run config context must be an object")
    for key in _CONTEXT_TRUSTED_OFFICE_KEYS:
        if key in context:
            runtime_context.setdefault(key, context[key])


def merge_run_context_overrides(config: dict[str, Any], context: Mapping[str, Any] | None, *, internal: bool = False) -> None:
    """Merge allowed values into the LangGraph runtime context."""
    if not context:
        return
    runtime_context = config.setdefault("context", {})
    if not isinstance(runtime_context, dict):
        raise ValueError("run config context must be an object")
    keys = _RUNTIME_CONTEXT_KEYS | _CONTEXT_INTERNAL_CALLER_KEYS if internal else _RUNTIME_CONTEXT_KEYS
    for key in keys:
        if key in context:
            runtime_context.setdefault(key, context[key])


def inject_authenticated_user_context(config: dict[str, Any], request: Request) -> None:
    """Stamp the authenticated user into the run context for background tools.

    Tool execution may happen after the request handler has returned, so tools
    that persist user-scoped files should not rely only on ambient ContextVars.
    The value comes from server-side auth state, never from client context.
    """

    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return

    runtime_context = config.setdefault("context", {})
    if isinstance(runtime_context, dict):
        runtime_context["user_id"] = str(user_id)
        runtime_context["user_role"] = getattr(user, "system_role", None)
        runtime_context["oauth_provider"] = getattr(user, "oauth_provider", None)
        runtime_context["oauth_id"] = getattr(user, "oauth_id", None)


def resolve_agent_factory(assistant_id: str | None):
    """Resolve the agent factory callable from config.

    Custom agents are implemented as ``lead_agent`` + an ``agent_name``
    injected into ``configurable`` or ``context`` — see
    :func:`build_run_config`.  All ``assistant_id`` values therefore map to the
    same factory; the routing happens inside ``make_lead_agent`` when it reads
    ``cfg["agent_name"]``.
    """
    from deerflow.agents.lead_agent.agent import make_lead_agent

    return make_lead_agent


# Lead-agent recursion budget bounds. The Gateway must NOT trust a
# client-supplied ``recursion_limit`` verbatim: an arbitrarily large value lets
# a single run execute unbounded LangGraph super-steps (each at least one LLM
# call), enabling runaway API cost / DoS. ``_DEFAULT_RECURSION_LIMIT`` is the
# server default when the client sends nothing; the hard ceiling any client
# value is clamped to is configurable via ``AppConfig.max_recursion_limit``.
_DEFAULT_RECURSION_LIMIT = 100
_DEFAULT_MAX_RECURSION_LIMIT = 1000


def _resolve_max_recursion_limit() -> int:
    """Resolve the clamp ceiling from ``AppConfig.max_recursion_limit``.

    Falls back to ``_DEFAULT_MAX_RECURSION_LIMIT`` when the app config cannot be
    loaded (e.g. no ``config.yaml`` in a bare unit-test environment) so that the
    clamp still applies rather than crashing the run-config assembly.
    """
    try:
        return get_app_config().max_recursion_limit
    except Exception:
        return _DEFAULT_MAX_RECURSION_LIMIT


def _clamp_recursion_limit(value: Any, max_limit: int) -> int:
    """Clamp a client-supplied ``recursion_limit`` into a safe server range.

    Non-integer values (including ``bool``, an ``int`` subclass) and non-positive
    values fall back to ``_DEFAULT_RECURSION_LIMIT``; valid positive integers are
    capped at ``max_limit`` (from ``AppConfig.max_recursion_limit``).
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return _DEFAULT_RECURSION_LIMIT
    return min(value, max_limit)


def build_run_config(
    thread_id: str,
    request_config: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    *,
    assistant_id: str | None = None,
) -> dict[str, Any]:
    """Build the current RunnableConfig contract.

    ``configurable`` is reserved for LangGraph checkpoint identity. Runtime
    domain values live only in ``context``.
    """
    config: dict[str, Any] = {
        "recursion_limit": _DEFAULT_RECURSION_LIMIT,
        "configurable": {"thread_id": thread_id},
        "context": {"thread_id": thread_id},
    }
    if request_config:
        for key, value in request_config.items():
            if key not in {"configurable", "context"}:
                config[key] = value
        if "recursion_limit" in request_config:
            max_limit = _resolve_max_recursion_limit()
            clamped = _clamp_recursion_limit(request_config["recursion_limit"], max_limit)
            if clamped != request_config["recursion_limit"]:
                logger.warning(
                    "build_run_config: clamped client recursion_limit %r -> %d (max %d). thread_id=%s",
                    request_config["recursion_limit"],
                    clamped,
                    max_limit,
                    thread_id,
                )
            config["recursion_limit"] = clamped

    if assistant_id and assistant_id != _DEFAULT_ASSISTANT_ID:
        normalized = assistant_id.strip().lower().replace("_", "-")
        if not normalized or not re.fullmatch(r"[a-z0-9-]+", normalized):
            raise ValueError(
                f"Invalid assistant_id {assistant_id!r}: must contain only letters, digits, and hyphens after normalization."
            )
        config["context"]["agent_name"] = normalized
        config.setdefault("run_name", resolve_root_run_name(config, normalized))

    if metadata:
        config.setdefault("metadata", {}).update(metadata)
    return config


async def apply_checkpoint_to_run_config(
    config: dict[str, Any],
    *,
    thread_id: str,
    request: Request,
    execution_spec: RunExecutionSpecV1,
) -> None:
    """Validate a branch/resume checkpoint and attach it to RunnableConfig."""
    if execution_spec.mode == "new":
        return
    checkpoint_id = execution_spec.checkpoint_id
    if not checkpoint_id:
        raise HTTPException(status_code=400, detail="execution checkpoint_id is required")
    read_config: dict[str, Any] = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "",
            "checkpoint_id": checkpoint_id,
        }
    }
    checkpointer = get_checkpointer(request)
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(read_config)
    except Exception as exc:
        logger.exception(
            "Failed to validate checkpoint %s for thread %s",
            checkpoint_id,
            sanitize_log_param(thread_id),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to validate checkpoint",
        ) from exc
    if checkpoint_tuple is None:
        raise HTTPException(
            status_code=404,
            detail=f"Checkpoint {checkpoint_id} not found",
        )
    if checkpoint_run_id(checkpoint_tuple) != execution_spec.source_run_id:
        raise HTTPException(
            status_code=400,
            detail="Checkpoint does not belong to source run",
        )

    configurable = config.setdefault("configurable", {})
    if not isinstance(configurable, dict):
        raise HTTPException(
            status_code=400,
            detail="request config configurable must be an object",
        )
    configurable.update(read_config["configurable"])




def resolve_execution_spec(
    body: Any,
    *,
    trusted_internal: bool,
) -> tuple[RunExecutionSpecV1, Any]:
    """Parse the single durable execution contract accepted by the runtime."""
    try:
        spec = RunExecutionSpecV1.parse(getattr(body, "execution_spec", None))
    except RunExecutionSpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    graph_input = spec.graph_input()
    if spec.mode != "resume":
        graph_input = normalize_input(
            graph_input,
            trusted_internal=trusted_internal,
        )
    return spec, graph_input




async def validate_execution_source_run(
    run_mgr: RunManager,
    execution_spec: RunExecutionSpecV1 | None,
    *,
    thread_id: str,
    assistant_id: str | None,
    user_id: str,
) -> None:
    """Require branch/resume operations to name an owned source Run."""
    if execution_spec is None or execution_spec.mode == "new":
        return
    source = await run_mgr.get(execution_spec.source_run_id or "", user_id=user_id)
    if (
        source is None
        or source.thread_id != thread_id
        or source.assistant_id != assistant_id
        or source.user_id != user_id
    ):
        raise HTTPException(status_code=404, detail="Source run not found")


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PreparedRun:
    """Fully validated inputs required to start one DeerFlow worker."""

    record: RunRecord
    bridge: StreamBridge
    run_manager: RunManager
    owner_user: Any
    ctx: Any
    agent_factory: Any
    graph_input: Any
    config: dict[str, Any]
    stream_modes: list[str]
    stream_subgraphs: bool
    interrupt_before: list[str] | str | None
    interrupt_after: list[str] | str | None
    recovery: bool


async def prepare_persisted_run(
    record: RunRecord,
    request: Request,
    *,
    recovery: bool = False,
    graph_input_override: Any = _PREPARED_VALUE_MISSING,
    config_override: dict[str, Any] | None = None,
    run_context_override: Any = None,
    agent_factory_override: Any = None,
) -> PreparedRun:
    """Validate a durable launch contract without starting its worker."""

    owner_user = getattr(getattr(request, "state", None), "user", None)
    owner_user_id = getattr(owner_user, "id", None)
    if owner_user_id is None or str(owner_user_id) != str(record.user_id):
        raise RuntimeError(
            f"Run {record.run_id} owner context does not match persisted owner"
        )

    try:
        contract = RunLaunchContractV1.parse(record.kwargs.get("launch_contract"))
    except RunLaunchContractValidationError as exc:
        raise RuntimeError(
            f"Run {record.run_id} has an invalid launch contract: {exc}"
        ) from exc

    run_ctx = run_context_override or get_run_context(request)
    base_app_config = run_ctx.app_config or get_app_config()
    runtime_model_id: str | None = None
    if contract.model_profile_id is None:
        native_model_name = contract.context.get('model_name')
        if native_model_name is not None:
            if not isinstance(native_model_name, str) or not native_model_name:
                raise RuntimeError(
                    f'Run {record.run_id} has an invalid native model name'
                )
            if base_app_config.get_model_config(native_model_name) is None:
                raise RuntimeError(
                    f'Run {record.run_id} native model {native_model_name!r} '
                    'is not in the configured model allowlist'
                )
        run_app_config = base_app_config
    else:
        try:
            run_app_config = _resolve_run_model_config(
                base_app_config,
                contract.model_profile_id,
                getattr(record, 'metadata', {}) or {},
            )
            runtime_model_id = resolve_runtime_model_id(
                run_app_config,
                SELECTED_MODEL_NAME,
            )
        except ModelBindingError as exc:
            profile_label = repr(contract.model_profile_id)
            raise RuntimeError(
                f'Run {record.run_id} cannot resolve model profile '
                f'{profile_label}: {exc}'
            ) from exc
        persisted_runtime_model_id = contract.context.get(
            RUNTIME_MODEL_ID_METADATA_KEY
        )
        if (
            not isinstance(persisted_runtime_model_id, str)
            or not persisted_runtime_model_id.strip()
        ):
            raise RuntimeError(
                f'Run {record.run_id} has no persisted runtime model id'
            )
        if persisted_runtime_model_id.strip() != runtime_model_id:
            raise RuntimeError(
                f'Run {record.run_id} runtime model changed from '
                f'{persisted_runtime_model_id.strip()!r} to {runtime_model_id!r}'
            )
    run_ctx = replace(run_ctx, app_config=run_app_config)
    agent_factory = agent_factory_override or resolve_agent_factory(
        record.assistant_id
    )

    if config_override is None:
        config = build_run_config(
            record.thread_id,
            copy.deepcopy(contract.config),
            getattr(record, "metadata", {}) or {},
            assistant_id=record.assistant_id,
        )
        contract_context = dict(contract.context)
        if contract.model_profile_id is not None:
            contract_context['model_name'] = SELECTED_MODEL_NAME
        merge_run_context_overrides(config, contract_context)
        merge_run_context_overrides(
            config,
            contract.trusted_context,
            internal=True,
        )
        merge_trusted_office_context(config, contract.trusted_context)
        inject_authenticated_user_context(config, request)
    else:
        config = copy.deepcopy(config_override)

    runtime_metadata = config.setdefault('metadata', {})
    if not isinstance(runtime_metadata, dict):
        raise RuntimeError(
            f'Run {record.run_id} has an invalid metadata launch section'
        )
    runtime_metadata.pop(RUNTIME_MODEL_ID_METADATA_KEY, None)
    if runtime_model_id is not None:
        runtime_metadata[RUNTIME_MODEL_ID_METADATA_KEY] = runtime_model_id

    runtime_context = config.setdefault('context', {})
    if not isinstance(runtime_context, dict):
        raise RuntimeError(
            f'Run {record.run_id} has an invalid runtime context'
        )
    if contract.model_profile_id is not None:
        runtime_context['model_name'] = SELECTED_MODEL_NAME
    runtime_context[RUN_EXECUTION_MODE_CONTEXT_KEY] = contract.execution_spec.mode

    config.setdefault("metadata", {})[
        CHECKPOINT_RUN_ID_METADATA_KEY
    ] = record.run_id

    if recovery:
        checkpoint_config = await find_owned_checkpoint_config(
            run_ctx.checkpointer,
            thread_id=record.thread_id,
            run_id=record.run_id,
        )
        configurable = config.setdefault("configurable", {})
        if not isinstance(configurable, dict):
            raise RuntimeError(
                f"Run {record.run_id} has an invalid configurable launch section"
            )
        for key in ("checkpoint_ns", "checkpoint_id", "checkpoint_map"):
            configurable.pop(key, None)
        for key in (
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "checkpoint_map",
        ):
            if key in checkpoint_config:
                configurable[key] = checkpoint_config[key]
        graph_input = None
    elif graph_input_override is not _PREPARED_VALUE_MISSING:
        graph_input = graph_input_override
    else:
        graph_input = contract.execution_spec.graph_input()
        if contract.execution_spec.mode != "resume":
            graph_input = normalize_input(graph_input, trusted_internal=True)

    return PreparedRun(
        record=record,
        bridge=get_stream_bridge(request),
        run_manager=get_run_manager(request),
        owner_user=owner_user,
        ctx=run_ctx,
        agent_factory=agent_factory,
        graph_input=graph_input,
        config=config,
        stream_modes=list(contract.stream_modes),
        stream_subgraphs=contract.stream_subgraphs,
        interrupt_before=contract.interrupt_before,
        interrupt_after=contract.interrupt_after,
        recovery=recovery,
    )


def launch_prepared_run(prepared: PreparedRun) -> RunRecord:
    """Start a previously validated run without repeating admission work."""

    task = asyncio.create_task(
        run_agent(
            prepared.bridge,
            prepared.run_manager,
            prepared.record,
            owner_user=prepared.owner_user,
            ctx=prepared.ctx,
            agent_factory=prepared.agent_factory,
            graph_input=prepared.graph_input,
            config=prepared.config,
            stream_modes=prepared.stream_modes,
            stream_subgraphs=prepared.stream_subgraphs,
            interrupt_before=prepared.interrupt_before,
            interrupt_after=prepared.interrupt_after,
            recovery=prepared.recovery,
        )
    )
    prepared.record.task = task
    return prepared.record


async def start_run(
    body: Any,
    thread_id: str,
    request: Request,
    *,
    trusted_context: Mapping[str, Any] | None = None,
) -> RunRecord:
    """Create a RunRecord and launch the background agent task.

    Parameters
    ----------
    body : Any
        Internal Office or scheduler request carrying a RunExecutionSpecV1.
    thread_id : str
        Target thread.
    trusted_context : Mapping[str, Any] | None
        Server-verified Office workspace bindings supplied by the Office facade.
        Client run context cannot populate these internal path keys.
    request : Request
        FastAPI request — used to retrieve singletons from ``app.state``.
    """
    run_mgr = get_run_manager(request)
    run_ctx = get_run_context(request)
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authenticated user context is required")

    is_internal_caller = getattr(getattr(request, "state", None), "auth_source", None) == AUTH_SOURCE_INTERNAL
    owner_user_id = str(user.id)
    # Office and scheduled runs share the same owner check before any RunRecord
    # becomes visible. A thread owned by another user is not addressable here.
    allowed = await run_ctx.thread_store.check_access(thread_id, owner_user_id)
    if not allowed:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    disconnect = DisconnectMode.cancel if body.on_disconnect == 'cancel' else DisconnectMode.continue_
    raw_model_profile_id = getattr(body, 'model_profile_id', None)
    if raw_model_profile_id is None:
        model_profile_id = None
    elif not isinstance(raw_model_profile_id, str) or not raw_model_profile_id.strip():
        raise HTTPException(
            status_code=400,
            detail='model_profile_id must be a non-empty string when provided',
        )
    else:
        model_profile_id = raw_model_profile_id.strip()

    raw_body_context = getattr(body, 'context', None) or {}
    if not isinstance(raw_body_context, Mapping):
        raise HTTPException(status_code=400, detail='context must be an object')
    client_context = dict(raw_body_context)
    client_context.pop(RUNTIME_MODEL_ID_METADATA_KEY, None)
    trusted_run_context = {}
    for key in _CONTEXT_SERVER_OWNED_KEYS:
        if is_internal_caller and key in client_context:
            trusted_run_context[key] = client_context[key]
        client_context.pop(key, None)

    if trusted_context is not None and not isinstance(trusted_context, Mapping):
        raise TypeError('trusted_context must be a mapping')
    trusted_context_overrides = {
        key: trusted_context[key]
        for key in _CONTEXT_TRUSTED_OFFICE_KEYS
        if trusted_context is not None and key in trusted_context
    }
    trusted_run_context.update(trusted_context_overrides)
    body_context = dict(client_context)

    raw_request_metadata = getattr(body, 'metadata', None) or {}
    if not isinstance(raw_request_metadata, Mapping):
        raise HTTPException(status_code=400, detail='metadata must be an object')
    request_metadata = dict(raw_request_metadata)
    request_metadata.pop(RUNTIME_MODEL_ID_METADATA_KEY, None)
    thread_metadata = dict(request_metadata)

    raw_request_config = getattr(body, 'config', None)
    request_config = (
        copy.deepcopy(dict(raw_request_config))
        if isinstance(raw_request_config, Mapping)
        else raw_request_config
    )
    if isinstance(request_config, dict):
        request_config_metadata = request_config.get('metadata')
        if isinstance(request_config_metadata, Mapping):
            sanitized_config_metadata = dict(request_config_metadata)
            sanitized_config_metadata.pop(RUNTIME_MODEL_ID_METADATA_KEY, None)
            request_config['metadata'] = sanitized_config_metadata

    base_app_config = run_ctx.app_config or get_app_config()
    runtime_model_id: str | None = None
    if model_profile_id is not None:
        body_context['model_name'] = SELECTED_MODEL_NAME
        model_name = SELECTED_MODEL_NAME
    else:
        model_name = body_context.get('model_name')
        if model_name is not None and not isinstance(model_name, str):
            raise HTTPException(status_code=400, detail='context.model_name must be a string')
        if model_name and base_app_config.get_model_config(model_name) is None:
            raise HTTPException(
                status_code=400,
                detail=f'Model {model_name!r} is not in the configured model allowlist',
            )

    owner_context_token = set_current_user(user)
    try:
        # Complete every fallible admission check before persisting a pending
        # Run. Invalid packages, inputs, models, and checkpoints must not leave
        # durable ghost records behind.
        execution_spec, graph_input = resolve_execution_spec(
            body,
            trusted_internal=is_internal_caller,
        )
        agent_factory = resolve_agent_factory(body.assistant_id)
        if model_profile_id is not None:
            try:
                run_app_config = _resolve_run_model_config(
                    base_app_config,
                    model_profile_id,
                    request_metadata,
                )
                runtime_model_id = resolve_runtime_model_id(
                    run_app_config,
                    SELECTED_MODEL_NAME,
                )
            except ModelBindingError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            body_context[RUNTIME_MODEL_ID_METADATA_KEY] = runtime_model_id
            request_metadata[RUNTIME_MODEL_ID_METADATA_KEY] = runtime_model_id
        config = build_run_config(
            thread_id,
            copy.deepcopy(request_config),
            request_metadata,
            assistant_id=body.assistant_id,
        )
        await validate_execution_source_run(
            run_mgr,
            execution_spec,
            thread_id=thread_id,
            assistant_id=body.assistant_id,
            user_id=owner_user_id,
        )
        await apply_checkpoint_to_run_config(
            config,
            thread_id=thread_id,
            request=request,
            execution_spec=execution_spec,
        )
        merge_run_context_overrides(
            config,
            client_context,
        )
        if not is_internal_caller:
            strip_internal_context_keys(config)
        merge_run_context_overrides(
            config,
            trusted_run_context,
            internal=True,
        )
        merge_trusted_office_context(config, trusted_run_context)
        runtime_context = config.setdefault('context', {})
        if not isinstance(runtime_context, dict):
            raise HTTPException(status_code=400, detail='context must be an object')
        if model_profile_id is not None:
            runtime_context['model_name'] = SELECTED_MODEL_NAME
        runtime_context[RUN_EXECUTION_MODE_CONTEXT_KEY] = execution_spec.mode
        runtime_metadata = config.setdefault('metadata', {})
        if not isinstance(runtime_metadata, dict):
            raise HTTPException(status_code=400, detail='metadata must be an object')
        runtime_metadata.pop(RUNTIME_MODEL_ID_METADATA_KEY, None)
        if runtime_model_id is not None:
            runtime_metadata[RUNTIME_MODEL_ID_METADATA_KEY] = runtime_model_id
        inject_authenticated_user_context(config, request)
        stream_modes = normalize_stream_modes(body.stream_mode)
        launch_contract = RunLaunchContractV1.build(
            model_profile_id=model_profile_id,
            execution_spec=execution_spec,
            config=redact_config_secrets(request_config),
            context=body_context,
            trusted_context=trusted_run_context,
            stream_modes=stream_modes,
            stream_subgraphs=body.stream_subgraphs,
            interrupt_before=body.interrupt_before,
            interrupt_after=body.interrupt_after,
        )
        persisted_kwargs: dict[str, Any] = {
            "launch_contract": launch_contract.to_dict(),
        }

        try:
            async with goal_thread_lock(thread_id):
                record = await run_mgr.create_or_reject(
                    thread_id,
                    body.assistant_id,
                    on_disconnect=disconnect,
                    metadata=request_metadata,
                    kwargs=persisted_kwargs,
                    multitask_strategy=body.multitask_strategy,
                    model_name=model_name,
                    user_id=owner_user_id,
                )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except UnsupportedStrategyError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc

        config.setdefault("metadata", {})[
            CHECKPOINT_RUN_ID_METADATA_KEY
        ] = record.run_id

        # Keep the internal thread record synchronized with the Office Run.
        try:
            existing = await run_ctx.thread_store.get(thread_id)
            if existing is None:
                await run_ctx.thread_store.create(
                    thread_id,
                    assistant_id=body.assistant_id,
                    metadata=thread_metadata,
                )
            else:
                await run_ctx.thread_store.update_status(thread_id, "running")
        except Exception:
            logger.warning("Failed to upsert thread_meta for %s (non-fatal)", sanitize_log_param(thread_id))

        prepared = await prepare_persisted_run(
            record,
            request,
            recovery=False,
            graph_input_override=graph_input,
            config_override=config,
            run_context_override=run_ctx,
            agent_factory_override=agent_factory,
        )
        launch_prepared_run(prepared)

        # Title sync is handled by worker.py's finally block which reads the
        # title from the checkpoint and calls thread_store.update_display_name
        # after the run completes.

        return record
    finally:
        reset_current_user(owner_context_token)


async def launch_scheduled_thread_run(
    *,
    thread_id: str,
    assistant_id: str | None,
    prompt: str,
    app: Any,
    owner_user_id: str,
    model_profile_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not owner_user_id:
        raise ValueError("scheduled runs require an owner_user_id")

    if is_auth_disabled() and owner_user_id == AUTH_DISABLED_USER_ID:
        owner_user = get_auth_disabled_user()
    else:
        owner_user = await get_local_provider().get_user(owner_user_id)
        if owner_user is None:
            raise RuntimeError(f"Scheduled run owner {owner_user_id!r} no longer exists")

    request = SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            user=owner_user,
            auth_source=AUTH_SOURCE_INTERNAL,
        ),
        cookies={},
    )
    body = SimpleNamespace(
        model_profile_id=model_profile_id,
        assistant_id=assistant_id,
        execution_spec=RunExecutionSpecV1(
            version=1,
            mode="new",
            input={"messages": [{"role": "user", "content": prompt}]},
        ).to_dict(),
        metadata=metadata or {},
        config=None,
        context={"non_interactive": True},
        interrupt_before=None,
        interrupt_after=None,
        stream_mode=None,
        stream_subgraphs=False,
        on_disconnect="continue",
        multitask_strategy="reject",
    )
    record = await start_run(body, thread_id, request)
    return {"run_id": record.run_id, "thread_id": record.thread_id}
