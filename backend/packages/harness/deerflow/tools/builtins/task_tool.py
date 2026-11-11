# 本文件属于 DeerFlow 原生 Subagent 委派工具模块：只解析委派目标、接入 Office 共享许可并通过 SubagentExecutor 执行子任务。
# 不负责 Office 群聊路由、维护第二套 FIFO、公共任务表、独立调度器或保存员工上下文副本。
# 关联：Office Agent Package 为被选员工提供 Prompt、Skills、Tools、MCP 与模型，DeerFlow Runtime 管理子任务执行。
"""Task tool for delegating work to subagents."""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command

from deerflow.config import get_app_config
from deerflow.office.packages import (
    resolve_employee_subagent_package,
    update_employee_runtime_state,
)
from deerflow.office.run_gate import (
    QueuedOfficeRun,
    get_process_employee_run_gate,
)
from deerflow.runtime.context_keys import OFFICE_PROJECT_ID_CONTEXT_KEY
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.executor import (
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
from deerflow.subagents.status_contract import (
    SubagentStatusValue,
    SubagentStopReasonValue,
    format_subagent_result_message,
    make_subagent_additional_kwargs,
)
from deerflow.tools.types import Runtime
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY, get_current_trace_id, normalize_trace_id

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# Cache subagent token usage by tool_call_id so TokenUsageMiddleware can
# write it back to the triggering AIMessage's usage_metadata.
_subagent_usage_cache: dict[str, dict[str, int]] = {}
# Keep existing deferred cleanup tasks alive until their native child settles.
_deferred_cleanup_tasks: set[asyncio.Task[None]] = set()


def _office_employee_gate() -> Any:
    """Return the Gateway-owned gates used by Office employee delegation."""

    return get_process_employee_run_gate()


def _token_usage_cache_enabled(app_config: "AppConfig | None") -> bool:
    if app_config is None:
        try:
            app_config = get_app_config()
        except FileNotFoundError:
            return False
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))


def _cache_subagent_usage(tool_call_id: str, usage: dict | None, *, enabled: bool = True) -> None:
    if enabled and usage:
        _subagent_usage_cache[tool_call_id] = usage


def pop_cached_subagent_usage(tool_call_id: str) -> dict | None:
    return _subagent_usage_cache.pop(tool_call_id, None)


def _is_subagent_terminal(result: Any) -> bool:
    """Return whether a background subagent result is safe to clean up."""
    return result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None


async def _release_office_execution_lease(
    employee_gate: Any | None,
    lease_id: str | None,
    *,
    employee_id: str | None = None,
    user_id: str | None = None,
    task_id: str | None = None,
) -> None:
    if lease_id is None:
        return
    try:
        if employee_gate is not None:
            await employee_gate.release(lease_id)
    finally:
        if employee_id is not None and user_id is not None and task_id is not None:
            try:
                update_employee_runtime_state(
                    employee_id,
                    user_id,
                    state='idle',
                    status_detail='等待新任务',
                    current_task_id=None,
                    expected_task_id=task_id,
                )
            except FileNotFoundError:
                logger.warning(
                    'Office employee %s disappeared while releasing task %s',
                    employee_id,
                    task_id,
                )


async def _deferred_cleanup_subagent_task(
    task_id: str,
    trace_id: str,
    *,
    employee_gate: Any | None = None,
    office_lease_id: str | None = None,
    employee_id: str | None = None,
    user_id: str | None = None,
    finalize_result=None,
) -> None:
    """Keep polling a cancelled subagent until it can be safely removed."""
    try:
        while True:
            result = get_background_task_result(task_id)
            if result is None:
                if finalize_result is not None:
                    finalize_result(None)
                return
            if _is_subagent_terminal(result):
                if finalize_result is not None:
                    finalize_result(result)
                cleanup_background_task(task_id)
                return
            await asyncio.sleep(5)
    except Exception:
        # The native child was already asked to cancel. A result lookup
        # failure must not leave the Office employee permanently working.
        logger.warning(
            "[trace=%s] Could not observe cancelled task %s; releasing Office lease",
            trace_id,
            task_id,
            exc_info=True,
        )
    finally:
        await _release_office_execution_lease(
            employee_gate,
            office_lease_id,
            employee_id=employee_id,
            user_id=user_id,
            task_id=task_id,
        )


def _log_cleanup_failure(cleanup_task: asyncio.Task[None], *, trace_id: str, task_id: str) -> None:
    if cleanup_task.cancelled():
        return

    exc = cleanup_task.exception()
    if exc is not None:
        logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")


def _schedule_deferred_subagent_cleanup(
    task_id: str,
    trace_id: str,
    *,
    employee_gate: Any | None = None,
    office_lease_id: str | None = None,
    employee_id: str | None = None,
    user_id: str | None = None,
    finalize_result=None,
) -> None:
    logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
    cleanup_task = asyncio.create_task(
        _deferred_cleanup_subagent_task(
            task_id,
            trace_id,
            employee_gate=employee_gate,
            office_lease_id=office_lease_id,
            employee_id=employee_id,
            user_id=user_id,
            finalize_result=finalize_result,
        )
    )
    _deferred_cleanup_tasks.add(cleanup_task)

    def finish_cleanup(task: asyncio.Task[None]) -> None:
        _deferred_cleanup_tasks.discard(task)
        _log_cleanup_failure(task, trace_id=trace_id, task_id=task_id)

    cleanup_task.add_done_callback(finish_cleanup)


async def _cancel_office_subagent_and_defer_cleanup(
    task_id: str,
    trace_id: str,
    *,
    employee_gate: Any | None,
    office_lease_id: str | None,
    employee_id: str | None = None,
    user_id: str | None = None,
    finalize_result=None,
) -> None:
    if office_lease_id is None:
        return
    try:
        request_cancel_background_task(task_id)
    except Exception:
        # Keep the lease until the existing deferred cleanup observes a
        # terminal child; a failed cancel request must not skip that cleanup.
        logger.warning(
            "[trace=%s] Failed to request cancellation for task %s",
            trace_id,
            task_id,
            exc_info=True,
        )

    try:
        result = get_background_task_result(task_id)
    except Exception:
        result = None
    if result is not None and _is_subagent_terminal(result):
        try:
            if finalize_result is not None:
                finalize_result(result)
        finally:
            await _release_office_execution_lease(
                employee_gate, office_lease_id, employee_id=employee_id, user_id=user_id, task_id=task_id)
        cleanup_background_task(task_id)
        return

    _schedule_deferred_subagent_cleanup(
        task_id,
        trace_id,
        employee_gate=employee_gate,
        office_lease_id=office_lease_id,
        employee_id=employee_id,
        user_id=user_id,
        finalize_result=finalize_result,
    )


def _find_usage_recorder(runtime: Any) -> Any | None:
    """Find a callback handler with ``record_external_llm_usage_records`` in the runtime config.

    LangChain may pass ``config["callbacks"]`` in three different shapes:

    - ``None`` (no callbacks registered): no recorder.
    - A plain ``list[BaseCallbackHandler]``: iterate it directly.
    - A ``BaseCallbackManager`` instance (e.g. ``AsyncCallbackManager`` on async
      tool runs): managers are not iterable, so we unwrap ``.handlers`` first.

    Any other shape (e.g. a single handler object accidentally passed without a
    list wrapper) cannot be iterated safely; treat it as "no recorder" rather
    than raise.
    """
    if runtime is None:
        return None
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks:
        return None
    if not isinstance(callbacks, list):
        return None
    for cb in callbacks:
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None


def _summarize_usage(records: list[dict] | None) -> dict | None:
    """Summarize token usage records into a compact dict for SSE events."""
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }


def _report_subagent_usage(runtime: Any, result: Any) -> None:
    """Report collected calls; the parent journal deduplicates each call identity."""
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        logger.debug("No usage recorder found in runtime callbacks — subagent token usage not recorded")
        return
    try:
        journal.record_external_llm_usage_records(records)
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)


def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None


def _office_employee_target_error(
    metadata: dict[str, Any],
    employee_id: str,
) -> str | None:
    if employee_id == metadata.get('office_employee_id') or employee_id == 'pm':
        return 'PM delegation must target a non-delegating employee'
    return None


def _resolve_office_employee_target(
    metadata: dict[str, Any],
    target: str,
) -> str:
    """Resolve one exact employee display name from the PM run snapshot."""
    normalized_target = str(target or '').strip()
    snapshot = metadata.get('office_employee_registry_snapshot')
    if not isinstance(snapshot, list):
        return normalized_target

    for entry in snapshot:
        if (
            isinstance(entry, dict)
            and str(entry.get('employee_id') or '').strip() == normalized_target
        ):
            return normalized_target

    matches = [
        str(entry.get('employee_id') or '').strip()
        for entry in snapshot
        if isinstance(entry, dict)
        and str(entry.get('display_name') or '').strip() == normalized_target
        and str(entry.get('employee_id') or '').strip()
    ]
    return matches[0] if len(matches) == 1 else normalized_target


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """Return the effective subagent skill allowlist under the parent policy."""
    if parent is None:
        return child
    if child is None:
        return list(parent)

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]


def _replayed_delegation_result(receipt, tool_call_id):
    result = receipt.task_result()
    status = {'succeeded': 'completed', 'failed': 'failed', 'interrupted': 'cancelled',
              'timeout': 'timed_out', 'unknown': 'unknown', 'claimed': 'in_progress', 'running': 'in_progress'}[receipt.status]
    return _task_result_command(tool_call_id=tool_call_id, status=status,
                                result=result.get('report'), stop_reason=result.get('stop_reason'),
                                workspace_result=result)


def _task_result_command(
    *,
    tool_call_id: str,
    status: SubagentStatusValue,
    result: str | None = None,
    error: str | None = None,
    stop_reason: SubagentStopReasonValue | None = None,
    model_name: str | None = None,
    usage: dict[str, int] | None = None,
    workspace_result: dict | None = None,
) -> Command:
    content, metadata_error = format_subagent_result_message(status, result=result, error=error, stop_reason=stop_reason)
    if workspace_result:
        from deerflow.office.execution_workspace import result_reference
        facts = result_reference(workspace_result)
        prefix = (f"/mnt/office-workspaces/{workspace_result['workspace_id']}"
                  if workspace_result.get('workspace_id') else '/mnt/user-data')
        facts.update(workspace_path=prefix + '/workspace/', outputs_path=prefix + '/outputs/')
        content += '\nTask workspace facts: ' + json.dumps(facts, ensure_ascii=False)
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    name="task",
                    additional_kwargs={**make_subagent_additional_kwargs(
                        status,
                        result=result,
                        error=metadata_error,
                        stop_reason=stop_reason,
                        model_name=model_name,
                        token_usage=usage,
                    ), **({'office_execution_workspace': workspace_result} if workspace_result else {})},
                )
            ]
        }
    )


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    workspace_mode: Literal['current', 'new', 'continue', 'fork'] = 'current',
    workspace_id: str | None = None,
    required_tools: list[str] | None = None,
) -> str | Command:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Built-in subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.
    - **bash**: Command execution specialist for running bash commands. This is only
      available when host bash is explicitly allowed or when using an isolated shell
      sandbox such as `AioSandboxProvider`.

    Additional custom subagent types may be defined in config.yaml under
    `subagents.custom_agents`. Each custom type can have its own system prompt,
    tools, skills, model, and timeout configuration. If an unknown subagent_type
    is provided, the error message will list all available types.

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
        workspace_mode: Office execution environment: current uses the current project/environment; new creates a worktree; continue reuses an existing worktree; fork creates an independent copy of its current version.
        workspace_id: Existing workspace ID returned by a prior task, required only for continue or fork.
        required_tools: Needed names from search_employee_registry employees[].tools (e.g. apply_patch, not file:write).
            Invalid names require argument correction, not changing employees. Unavailable staff or missing tools are reported, not queued.
    """
    runtime_app_config = _get_runtime_app_config(runtime)
    user_id = resolve_runtime_user_id(runtime)
    runtime_config = getattr(runtime, 'config', None)
    runtime_metadata = (
        runtime_config.get('metadata', {})
        if isinstance(runtime_config, dict)
        else {}
    )
    office_parent_id = runtime_metadata.get('office_employee_id')
    office_parent_id = (
        office_parent_id.strip()
        if isinstance(office_parent_id, str) and office_parent_id.strip()
        else None
    )
    original_subagent_type = subagent_type
    if (workspace_mode != 'current' or workspace_id is not None) and office_parent_id is None:
        return _task_result_command(tool_call_id=tool_call_id, status='failed', error='Workspace selection requires an Office PM')
    employee_package = None
    if office_parent_id is not None:
        from deerflow.office.delegation import lookup_delegation

        subagent_type = _resolve_office_employee_target(runtime_metadata, subagent_type)
        try:
            receipt = lookup_delegation(
                runtime, user_id=user_id, employee_id=subagent_type, description=description, prompt=prompt,
                subagent_type=original_subagent_type, tool_call_id=tool_call_id, workspace_mode=workspace_mode,
                workspace_id=workspace_id, required_tools=required_tools)
        except (ValueError, TypeError) as error:
            return _task_result_command(tool_call_id=tool_call_id, status='failed', error=str(error))
        if receipt is not None:
            return _replayed_delegation_result(receipt, tool_call_id)
        # A new delegation resolves today's employee binding, while the parent
        # keeps its own pinned model. Preserve all other parent runtime settings.
        from deerflow.config.app_config import AppConfig
        current_models = get_app_config().models
        if runtime_app_config is not None:
            runtime_app_config = AppConfig.model_validate({
                **runtime_app_config.model_dump(),
                'models': [model.model_dump() for model in current_models],
            })
        else:
            runtime_app_config = get_app_config()
        target_error = _office_employee_target_error(runtime_metadata, subagent_type)
        if target_error is not None:
            return _task_result_command(
                tool_call_id=tool_call_id,
                status='failed',
                error=target_error,
            )
    try:
        employee_package = resolve_employee_subagent_package(
            subagent_type,
            user_id,
            runtime_app_config,
        )
    except (FileNotFoundError, RuntimeError) as error:
        if office_parent_id is not None:
            return _task_result_command(
                tool_call_id=tool_call_id,
                status='failed',
                error=str(error),
            )
    if employee_package is not None and office_parent_id is None:
        return _task_result_command(
            tool_call_id=tool_call_id,
            status='failed',
            error='Office employees can only be delegated by an Office PM run',
        )
    if employee_package is not None:
        from deerflow.office.tool_contract import validate_required_tool_names

        try:
            validate_required_tool_names(required_tools, app_config=runtime_app_config)
        except ValueError as error:
            return _task_result_command(tool_call_id=tool_call_id, status='failed', error=str(error))
        missing_tools = sorted(set(required_tools or []) - set(employee_package.tool_contract.runtime_names))
        if missing_tools:
            return _task_result_command(tool_call_id=tool_call_id, status='failed',
                                        error=f'Employee {subagent_type} lacks required tools: {", ".join(missing_tools)}. Report the capability gap to the user.')
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) if runtime_app_config is not None else get_available_subagent_names()
    if employee_package is not None:
        available_subagent_names = [*available_subagent_names, employee_package.employee_id]

    # Get subagent configuration
    config = employee_package.config if employee_package is not None else (
        get_subagent_config(subagent_type, app_config=runtime_app_config)
        if runtime_app_config is not None
        else get_subagent_config(subagent_type)
    )
    if config is None:
        available = ", ".join(available_subagent_names)
        error = f"Unknown subagent type '{subagent_type}'. Available: {available}"
        return _task_result_command(
            tool_call_id=tool_call_id,
            status="failed",
            error=error,
        )
    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return _task_result_command(
                tool_call_id=tool_call_id,
                status="failed",
                error=LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE,
            )

    # Build config overrides
    overrides: dict = {}

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    trace_id = None
    deerflow_trace_id = None
    metadata: dict = {}

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

        # Try to get parent model from configurable
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # Get user_id for tracing (uses standard resolution order)
    user_id = resolve_runtime_user_id(runtime)

    # Propagate the authenticated runtime context so delegated tool calls are
    # evaluated by GuardrailMiddleware with the same identity/attribution as
    # the lead agent. Sourced from the server-side context written by
    # inject_authenticated_user_context (and run_id by the run worker); stays
    # None when absent in direct embedded runs so guardrail behavior is
    # unchanged. Without this, role-aware policy silently mis-attributes any
    # tool call delegated to a subagent (user_role=None).
    parent_context = runtime.context if runtime is not None else None
    parent_context = parent_context if isinstance(parent_context, dict) else {}
    user_role = parent_context.get("user_role")
    oauth_provider = parent_context.get("oauth_provider")
    oauth_id = parent_context.get("oauth_id")
    run_id = parent_context.get("run_id")
    deerflow_trace_id = normalize_trace_id(parent_context.get(DEERFLOW_TRACE_METADATA_KEY)) or normalize_trace_id(metadata.get(DEERFLOW_TRACE_METADATA_KEY)) or get_current_trace_id()

    parent_available_skills = metadata.get("available_skills")
    if employee_package is None and parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    if overrides:
        config = replace(config, **overrides)

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools

    # Inherit parent agent's tool_groups so subagents respect the same restrictions
    parent_tool_groups = metadata.get("tool_groups")
    if employee_package is not None:
        parent_tool_groups = None
    resolved_app_config = runtime_app_config
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)

    # Subagents should not have subagent tools enabled (prevent recursive nesting)
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    if employee_package is not None:
        available_tools_kwargs['allowed_configured_tool_names'] = list(
            employee_package.allowed_configured_tool_names
        )
        available_tools_kwargs['allowed_external_tool_names'] = list(
            employee_package.allowed_external_tool_names
        )
    tools = get_available_tools(**available_tools_kwargs)
    if employee_package is not None and not any(
        getattr(item, 'name', None) == 'report_to_pm' for item in tools
    ):
        from deerflow.office.routing import build_report_to_pm_tool

        tools = [*tools, build_report_to_pm_tool(employee_package.employee_id)]

    if employee_package is not None:
        from deerflow.office.tool_contract import select_contract_tools
        from deerflow.config.memory_config import should_use_memory_tools
        from deerflow.agents.memory.tools import get_memory_tools
        if should_use_memory_tools((resolved_app_config or get_app_config()).memory):
            existing_names = {getattr(t, 'name', t if isinstance(t, str) else None) for t in tools}
            tools = [*tools, *(t for t in get_memory_tools() if t.name not in existing_names)]
        tools = select_contract_tools(tools, employee_package.tool_contract)

    task_id = tool_call_id
    employee_id = (
        employee_package.employee_id
        if employee_package is not None
        else None
    )
    raw_writer = get_stream_writer()
    receipt_store = None
    receipt_identity = None
    receipt_terminal = False
    workspace_ref = None

    def record_execution_status(status: str, *, report: str = '', steps=None, stop_reason=None) -> None:
        nonlocal receipt_terminal
        if receipt_store is None or receipt_terminal:
            return
        extra = {'workspace_id': None} if parent_context.get(OFFICE_PROJECT_ID_CONTEXT_KEY) else {}
        if workspace_ref:
            from deerflow.office.execution_workspace import ExecutionWorkspaceService, result_reference
            service = ExecutionWorkspaceService(user_id=user_id)
            try:
                extra = result_reference(service.get(workspace_ref['workspace_id'], work=receipt_identity.work))
            except (ValueError, OSError):
                logger.exception('Task workspace lookup failed: %s', workspace_ref['workspace_id'])
                extra = {'workspace_id': workspace_ref['workspace_id']}
        if status in receipt_store.TERMINAL:
            from deerflow.office.delegation_memory import execution_evidence
            from deerflow.office.terminal import manager_for
            manager_for(receipt_store).finish_run(run_id, task_id)
            extra['execution_evidence'] = execution_evidence(
                receipt_store, None, work=receipt_identity.work, employee_id=employee_id, run_id=run_id,
                native_task_id=task_id, steps=steps or [])
        receipt = receipt_store.observe(receipt_identity, parent_run_id=run_id, native_task_id=task_id,
                              status=status, result_ref={'report': report, 'stop_reason': stop_reason, **extra},
                              evidence=steps or [])
        receipt_terminal = status in receipt_store.TERMINAL
        if receipt_terminal:
            from deerflow.office.delegation_memory import remember_delegation

            remember_delegation(identity=receipt_identity, receipt=receipt,
                                memory_config=(resolved_app_config or get_app_config()).memory,
                                trace_id=trace_id)
        if status == 'succeeded' and steps and not workspace_ref and parent_context.get(OFFICE_PROJECT_ID_CONTEXT_KEY):
            from deerflow.config.paths import get_paths
            from deerflow.office.project_knowledge import ProjectKnowledgeStore
            from deerflow.office.project_knowledge_service import ProjectKnowledgeService
            try:
                ProjectKnowledgeService(ProjectKnowledgeStore(paths=get_paths(), user_id=user_id)).capture_verification(
                    project_id=parent_context[OFFICE_PROJECT_ID_CONTEXT_KEY], user_id=user_id,
                    run_id=run_id, native_task_id=task_id, topic=description, steps=steps,
                    occurred_at=int(time.time() * 1000))
            except ValueError:
                # A successful employee report without successful tool observations is not verification.
                pass

    def finalize_result(result) -> None:
        if result is None:
            record_execution_status('unknown', report='Native execution outcome is unavailable; execution was not repeated.')
            return
        _report_subagent_usage(runtime, result)
        status = {SubagentStatus.COMPLETED: 'succeeded', SubagentStatus.FAILED: 'failed',
                  SubagentStatus.CANCELLED: 'interrupted', SubagentStatus.TIMED_OUT: 'timeout'}[result.status]
        record_execution_status(status, report=result.result or result.error or '',
                                steps=result.ai_messages or [], stop_reason=result.stop_reason)

    def writer(payload: dict[str, Any]) -> None:
        projected_status = {
            'task_started': 'running', 'task_completed': 'succeeded',
            'task_failed': 'failed', 'task_cancelled': 'interrupted', 'task_timed_out': 'timeout',
        }.get(payload.get('type'))
        if projected_status:
            record_execution_status(projected_status, report=payload.get("result") or payload.get("error") or "")
        raw_writer(
            {
                **payload,
                'employee_id': employee_id,
                'subagent_type': subagent_type,
                'workspace_id': workspace_ref['workspace_id'] if workspace_ref else None,
            }
        )

    employee_gate = None
    office_lease_id: str | None = None
    employee_projection_active = False

    async def release_office_execution() -> None:
        await _release_office_execution_lease(
            employee_gate,
            office_lease_id,
            employee_id=employee_id if employee_projection_active else None,
            user_id=user_id if employee_projection_active else None,
            task_id=task_id if employee_projection_active else None,
        )

    if office_parent_id is not None and employee_id is not None:
        if not isinstance(run_id, str) or not run_id.strip():
            return _task_result_command(
                tool_call_id=tool_call_id,
                status='failed',
                error='Office parent run identity is unavailable',
            )
        from deerflow.office.delegation import claim_delegation

        employee_gate = _office_employee_gate()
        try:
            admission = await employee_gate.admit(
                QueuedOfficeRun(
                    queue_id=task_id,
                    target_employee_id=employee_id,
                    source_kind='pm_delegate',
                    text=description,
                    enqueued_at=int(time.time() * 1000),
                    parent_run_id=run_id,
                    tool_call_id=tool_call_id,
                    conversation_kind="group",
                )
            )
            if not admission.started:
                return _task_result_command(
                    tool_call_id=tool_call_id, status='failed',
                    error=f'Employee {employee_id} is busy or waiting for input. No task was queued or started; report this to the user.')
            office_lease_id = admission.lease_id
            if office_lease_id is None:
                raise RuntimeError(f'Office employee lease was not granted: {employee_id}')
            try:
                receipt_store, receipt_identity, receipt, first, prompt = claim_delegation(
                    runtime, user_id=user_id, employee_id=employee_id, description=description,
                    prompt=prompt, subagent_type=original_subagent_type, tool_call_id=tool_call_id,
                    workspace_mode=workspace_mode, workspace_id=workspace_id, required_tools=required_tools,
                    package_snapshot={'skills': config.skills, 'model': effective_model,
                                      'tool_groups': list(employee_package.tool_groups),
                                      'tool_names': [str(getattr(tool, 'name', '')) for tool in tools],
                                      'disallowed_tools': config.disallowed_tools})
            except (ValueError, TypeError) as error:
                await release_office_execution()
                return _task_result_command(tool_call_id=tool_call_id, status='failed', error=str(error))
            workspace_ref = receipt.input_ref.get('workspace')
            if not first:
                await release_office_execution()
                return _replayed_delegation_result(receipt, tool_call_id)
            try:
                update_employee_runtime_state(
                    employee_id,
                    user_id,
                    state='working',
                    status_detail='正在执行任务',
                    current_task_id=task_id,
                )
                employee_projection_active = True
            except FileNotFoundError:
                logger.warning(
                    'Office employee %s disappeared before task %s started',
                    employee_id,
                    tool_call_id,
                )
        except asyncio.CancelledError:
            # A PM cancellation can arrive before this delegation gets a lease.
            # Remove only pending PM delegations owned by this parent; direct
            # employee messages are filtered by EmployeeRunGate.cancel_parent.
            if office_lease_id is None and isinstance(run_id, str) and run_id.strip():
                await employee_gate.cancel_parent(run_id)
            await release_office_execution()
            record_execution_status('interrupted')
            raise
        except BaseException:
            await release_office_execution()
            record_execution_status('failed')
            raise

    # Create executor only after Office admission has completed.
    executor_kwargs = {
        "config": config,
        "tools": tools,
        "parent_model": parent_model,
        "sandbox_state": sandbox_state,
        "thread_data": thread_data,
        "thread_id": thread_id,
        "trace_id": trace_id,
        "user_id": user_id,
        "user_role": user_role,
        "oauth_provider": oauth_provider,
        "oauth_id": oauth_id,
        "run_id": run_id,
        "deerflow_trace_id": deerflow_trace_id,
    }
    usage_recorder = _find_usage_recorder(runtime)
    if usage_recorder is not None:
        executor_kwargs['usage_reporter'] = usage_recorder.record_external_llm_usage_records
    if employee_package is not None:
        executor_kwargs["office_context"] = {key: parent_context[key] for key in (
            "office_project_id", "office_project_path", "office_work", "office_work_context") if key in parent_context}
        executor_kwargs["office_context"]["office_native_task_id"] = task_id
        executor_kwargs["office_context"]["office_employee_id"] = employee_id
        if workspace_ref:
            executor_kwargs["office_context"]["office_execution_workspace"] = workspace_ref
        executor_kwargs["office_context"]["office_tool_contract"] = employee_package.tool_contract.snapshot()
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    try:
        executor = SubagentExecutor(**executor_kwargs)
        # Start background execution (always async to prevent blocking)
        task_id = executor.execute_async(prompt, task_id=task_id)
    except asyncio.CancelledError:
        await release_office_execution()
        record_execution_status('interrupted')
        raise
    except Exception:
        await release_office_execution()
        record_execution_status('failed')
        raise

    # Poll for task completion in backend (removes need for LLM to poll)
    poll_count = 0
    last_status = None
    last_message_count = 0  # Track how many AI messages we've already sent
    max_poll_count = None if employee_package is not None else (config.timeout_seconds + 60) // 5

    timeout_policy = 'Office per-round budget' if employee_package is not None else f'task {config.timeout_seconds}s'
    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout_policy={timeout_policy}, polling_limit={max_poll_count})")

    # Send Task Started message'
    try:
        writer(
            {
                "type": "task_started",
                "task_id": task_id,
                "description": description,
                "model_name": effective_model,
            }
        )
    except Exception:
        await _cancel_office_subagent_and_defer_cleanup(
            task_id,
            trace_id,
            employee_gate=employee_gate,
            office_lease_id=office_lease_id,
            employee_id=employee_id if employee_projection_active else None,
            user_id=user_id if employee_projection_active else None,
        )
        _subagent_usage_cache.pop(tool_call_id, None)
        record_execution_status('interrupted')
        raise

    try:
        while True:
            result = get_background_task_result(task_id)

            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                record_execution_status("unknown", report="Native task result is unavailable; execution was not repeated.")
                await release_office_execution()
                writer({"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"})
                cleanup_background_task(task_id)
                error = f"Task {task_id} disappeared from background tasks"
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="failed",
                    error=error,
                )

            # Log status changes for debugging
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                last_status = result.status

            # The collector publishes cumulative records. Reuse one snapshot for
            # both live progress and the terminal event so the frontend can
            # replace, rather than add, its per-task total.
            usage = _summarize_usage(getattr(result, "token_usage_records", None))

            # Check for new AI messages and send task_running events
            ai_messages = result.ai_messages or []
            current_message_count = len(ai_messages)
            if current_message_count > last_message_count:
                # Send task_running event for each new message
                for i in range(last_message_count, current_message_count):
                    message = ai_messages[i]
                    writer(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "message": message,
                            "message_index": i + 1,  # 1-based index for display
                            "total_messages": current_message_count,
                            "usage": usage,
                            "model_name": effective_model,
                        }
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # Check if task completed, failed, or timed out
            if result.status == SubagentStatus.COMPLETED:
                record_execution_status('succeeded', report=result.result or '', steps=result.ai_messages or [],
                                       stop_reason=getattr(result, 'stop_reason', None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                await release_office_execution()
                writer(
                    {
                        "type": "task_completed",
                        "task_id": task_id,
                        "result": result.result,
                        "usage": usage,
                        "model_name": effective_model,
                    }
                )
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)
                # stop_reason carries a guardrail cap (token_capped / turn_capped)
                # when the run was ended early but still produced a final answer
                # — the work survives on result_brief like a clean success.
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="completed",
                    result=result.result,
                    stop_reason=result.stop_reason,
                    model_name=effective_model,
                    usage=usage,
                    workspace_result=receipt_store.get(receipt_identity).task_result() if receipt_store else None,
                )
            elif result.status == SubagentStatus.FAILED:
                record_execution_status('failed', report=result.result or result.error or '', steps=result.ai_messages or [],
                                       stop_reason=getattr(result, 'stop_reason', None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                await release_office_execution()
                writer(
                    {
                        "type": "task_failed",
                        "task_id": task_id,
                        "result": result.result,
                        "error": result.error,
                        "usage": usage,
                        "model_name": effective_model,
                    }
                )
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                # A turn-capped run with no usable output surfaces as failed +
                # stop_reason=turn_capped; the cap note lets the lead tell "out
                # of budget" from "broken subagent".
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="failed",
                    result=result.result,
                    error=result.error,
                    stop_reason=result.stop_reason,
                    model_name=effective_model,
                    usage=usage,
                    workspace_result=receipt_store.get(receipt_identity).task_result() if receipt_store else None,
                )
            elif result.status == SubagentStatus.CANCELLED:
                record_execution_status('interrupted', report=result.result or result.error or '',
                                        steps=result.ai_messages or [], stop_reason=getattr(result, 'stop_reason', None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                await release_office_execution()
                writer(
                    {
                        "type": "task_cancelled",
                        "task_id": task_id,
                        "result": result.result,
                        "error": result.error,
                        "usage": usage,
                        "model_name": effective_model,
                    }
                )
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="cancelled",
                    result=result.result,
                    error=result.error,
                    model_name=effective_model,
                    usage=usage,
                    stop_reason=result.stop_reason,
                    workspace_result=receipt_store.get(receipt_identity).task_result() if receipt_store else None,
                )
            elif result.status == SubagentStatus.TIMED_OUT:
                record_execution_status('timeout', report=result.result or result.error or '',
                                        steps=result.ai_messages or [], stop_reason=getattr(result, 'stop_reason', None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                await release_office_execution()
                writer(
                    {
                        "type": "task_timed_out",
                        "task_id": task_id,
                        "result": result.result,
                        "error": result.error,
                        "usage": usage,
                        "model_name": effective_model,
                    }
                )
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="timed_out",
                    result=result.result,
                    error=result.error,
                    model_name=effective_model,
                    usage=usage,
                    stop_reason=result.stop_reason,
                    workspace_result=receipt_store.get(receipt_identity).task_result() if receipt_store else None,
                )

            # Still running, wait before next poll
            await asyncio.sleep(5)
            poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            if max_poll_count is not None and poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                _report_subagent_usage(runtime, result)
                usage = _summarize_usage(getattr(result, "token_usage_records", None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                writer(
                    {
                        "type": "task_timed_out",
                        "task_id": task_id,
                        "usage": usage,
                        "model_name": effective_model,
                    }
                )
                # The task may still be running in the background. Signal cooperative
                # cancellation and schedule deferred cleanup to remove the entry from
                # _background_tasks once the background thread reaches a terminal state.
                try:
                    request_cancel_background_task(task_id)
                except Exception:
                    logger.warning(
                        "[trace=%s] Failed to request cancellation for timed-out task %s",
                        trace_id,
                        task_id,
                        exc_info=True,
                    )
                _schedule_deferred_subagent_cleanup(
                    task_id,
                    trace_id,
                    employee_gate=employee_gate,
                    office_lease_id=office_lease_id,
                    employee_id=employee_id if employee_projection_active else None,
                    user_id=user_id if employee_projection_active else None,
                )
                message = f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="polling_timed_out",
                    result=result.result,
                    error=message,
                    model_name=effective_model,
                    usage=usage,
                )
    except asyncio.CancelledError:
        # A PM cancellation can reach an already-admitted child while sibling
        # delegations are still waiting in the shared employee FIFO. Remove
        # only pending delegations owned by this parent before releasing the
        # active child's lease; direct user work remains untouched by the gate.
        if (
            employee_gate is not None
            and isinstance(run_id, str)
            and run_id.strip()
        ):
            try:
                await employee_gate.cancel_parent(run_id)
            except Exception:
                logger.warning(
                    "[trace=%s] Failed to cancel pending Office delegations for parent %s",
                    trace_id,
                    run_id,
                    exc_info=True,
                )

        # Signal the background subagent thread to stop cooperatively.
        try:
            request_cancel_background_task(task_id)
        except Exception:
            # A failed cancellation request must not prevent Office cleanup.
            logger.warning(
                "[trace=%s] Failed to request cancellation for task %s",
                trace_id,
                task_id,
                exc_info=True,
            )

        # Do not hold the parent Run open while a model/tool call ignores the
        # cooperative cancellation signal. The existing deferred cleanup keeps
        # polling the native DeerFlow background task and removes it later.
        try:
            final_result = get_background_task_result(task_id)
        except Exception:
            logger.warning(
                "[trace=%s] Could not read cancelled task %s; deferring cleanup",
                trace_id,
                task_id,
            )
            final_result = None
        if final_result is not None and not _is_subagent_terminal(final_result):
            _report_subagent_usage(runtime, final_result)
        if final_result is not None and _is_subagent_terminal(final_result):
            try:
                finalize_result(final_result)
            finally:
                await release_office_execution()
            cleanup_background_task(task_id)
        else:
            # Keep an Office employee lease while the native DeerFlow child is
            # still running. The deferred cleanup releases it after terminal
            # observation, so the same employee cannot start a second Run.
            _schedule_deferred_subagent_cleanup(
                task_id,
                trace_id,
                employee_gate=employee_gate,
                office_lease_id=office_lease_id,
                employee_id=employee_id if employee_projection_active else None,
                user_id=user_id if employee_projection_active else None,
                finalize_result=finalize_result,
            )
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
    except Exception:
        await _cancel_office_subagent_and_defer_cleanup(
            task_id,
            trace_id,
            employee_gate=employee_gate,
            office_lease_id=office_lease_id,
            employee_id=employee_id if employee_projection_active else None,
            user_id=user_id if employee_projection_active else None,
            finalize_result=finalize_result,
        )
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
