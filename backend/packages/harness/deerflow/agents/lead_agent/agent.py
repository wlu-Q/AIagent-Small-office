# 本文件属于 DeerFlow Lead Agent 工厂模块：只装配模型、工具、中间件和主 Agent 图。
# 不负责 Office 消息路由、员工排队、Run 存储、Event 投影或客户端状态。
# 关联：lead_agent/prompt.py、agents/middlewares 与 tools 模块共同组成 DeerFlow 主执行 Agent。
"""Lead agent factory.

INVARIANT — tracing callback placement
======================================

Tracing callbacks (Langfuse, LangSmith) are attached at the **graph
invocation root** in :func:`_make_lead_agent` (see the
``build_tracing_callbacks()`` block that appends to ``config["callbacks"]``).
Every ``create_chat_model(...)`` call inside this module — and inside any
middleware reachable from this graph (e.g. ``TitleMiddleware``) — MUST pass
``attach_tracing=False``.

Forgetting that flag emits duplicate spans (one rooted at the graph, one at
the model) AND prevents the Langfuse handler's ``propagate_attributes``
path from firing, so ``session_id`` / ``user_id`` never reach the trace.
The four current sites are: bootstrap agent, default agent, summarization
middleware, and the async path inside ``TitleMiddleware``. Any new in-graph
``create_chat_model`` call must add to this list and pass the flag.
"""

from __future__ import annotations

import logging

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig

from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.agents.middlewares.safety_finish_reason_middleware import SafetyFinishReasonMiddleware
from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware, create_summarization_middleware
from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware
from deerflow.agents.middlewares.title_middleware import TitleMiddleware
from deerflow.agents.middlewares.todo_middleware import OFFICE_TODO_TOOL_DESCRIPTION, TodoMiddleware
from deerflow.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import (
    is_office_managed_agent,
    load_agent_config,
    load_agent_soul,
    validate_agent_name,
)
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.config.memory_config import should_use_memory_tools
from deerflow.config.subagents_config import (
    DEFAULT_MAX_TOTAL_SUBAGENTS_PER_RUN,
    MAX_CONCURRENT_SUBAGENTS,
    clamp_subagent_concurrency,
    clamp_total_subagents_per_run,
)
from deerflow.models import create_chat_model
from deerflow.office.authority import (
    AgentAuthoritySpec,
    build_authority_prompt,
    normalize_authority,
)
from deerflow.office.routing import (
    build_report_to_pm_tool,
    build_search_employee_registry_tool,
)
from deerflow.office.project_knowledge_tool import capture_user_message
from deerflow.runtime.context_keys import (
    RUNTIME_BOUND_TOOL_NAMES_METADATA_KEY,
    RUNTIME_MODEL_ID_METADATA_KEY,
)
from deerflow.skills.types import Skill
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)

_BOOTSTRAP_SKILL_NAMES = {"bootstrap"}
_NON_INTERACTIVE_DISABLED_TOOL_NAMES = frozenset({"ask_clarification"})


def _default_max_total_subagents(app_config: object) -> int:
    subagents_config = getattr(app_config, "subagents", None)
    return getattr(subagents_config, "max_total_per_run", DEFAULT_MAX_TOTAL_SUBAGENTS_PER_RUN)


def _append_memory_tools_without_name_conflicts(tools: list) -> None:
    """Append memory tools without dropping unrelated duplicate-named tools."""
    from deerflow.agents.memory.tools import get_memory_tools

    existing_names = {getattr(tool, "name", None) for tool in tools}
    for memory_tool in get_memory_tools():
        if memory_tool.name in existing_names:
            logger.warning("Memory tool name %r already exists and was skipped.", memory_tool.name)
            continue
        tools.append(memory_tool)
        existing_names.add(memory_tool.name)


def _runtime_bound_tool_name(tool: object) -> str:
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not isinstance(name, str) or not name.strip():
        raise TypeError(f"Runtime tool {tool!r} does not expose a stable name")
    return name


def _record_runtime_facts(
    config: RunnableConfig,
    *,
    model_name: str,
    model_config: object,
    final_tools: list,
) -> list[str]:
    tool_names = [_runtime_bound_tool_name(tool) for tool in final_tools]
    metadata = config.setdefault("metadata", {})
    runtime_model_id = getattr(model_config, "model", None)
    if not isinstance(runtime_model_id, str) or not runtime_model_id.strip():
        raise ValueError(f"Model {model_name!r} has no physical runtime model id")
    metadata["model_name"] = model_name
    metadata[RUNTIME_MODEL_ID_METADATA_KEY] = runtime_model_id.strip()
    metadata[RUNTIME_BOUND_TOOL_NAMES_METADATA_KEY] = tool_names
    return tool_names


def _get_runtime_context(config: RunnableConfig) -> dict:
    """Return the LangGraph runtime context."""
    context = config.get("context", {}) or {}
    return dict(context) if isinstance(context, dict) else {}


def _resolve_model_name(requested_model_name: str | None = None, *, app_config: AppConfig | None = None) -> str:
    """Resolve a runtime model name safely, falling back to default if invalid. Returns None if no models are configured."""
    app_config = app_config or get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name and requested_model_name != default_model_name:
        logger.warning(f"Model '{requested_model_name}' not found in config; fallback to default model '{default_model_name}'.")
    return default_model_name


def _create_summarization_middleware(*, app_config: AppConfig | None = None) -> DeerFlowSummarizationMiddleware | None:
    """Create and configure the summarization middleware from config."""
    return create_summarization_middleware(app_config=app_config)


def _create_todo_list_middleware(is_plan_mode: bool) -> TodoMiddleware | None:
    """Create and configure the TodoList middleware.

    Args:
        is_plan_mode: Whether to enable plan mode with TodoList middleware.

    Returns:
        TodoMiddleware instance if plan mode is enabled, None otherwise.
    """
    if not is_plan_mode:
        return None

    # Custom prompts matching DeerFlow's style
    system_prompt = """
<todo_list_system>
You have access to the `write_todos` tool to help you manage and track complex multi-step objectives.

**CRITICAL RULES:**
- Mark todos as completed IMMEDIATELY after finishing each step - do NOT batch completions
- Keep EXACTLY ONE task as `in_progress` at any time (unless tasks can run in parallel)
- Update the todo list in REAL-TIME as you work - this gives users visibility into your progress
- DO NOT use this tool for simple tasks (< 3 steps) - just complete them directly

**When to Use:**
This tool is designed for complex objectives that require systematic tracking:
- Complex multi-step tasks requiring 3+ distinct steps
- Non-trivial tasks needing careful planning and execution
- User explicitly requests a todo list
- User provides multiple tasks (numbered or comma-separated list)
- The plan may need revisions based on intermediate results

**When NOT to Use:**
- Single, straightforward tasks
- Trivial tasks (< 3 steps)
- Purely conversational or informational requests
- Simple tool calls where the approach is obvious

**Best Practices:**
- Break down complex tasks into smaller, actionable steps
- Use clear, descriptive task names
- Remove tasks that become irrelevant
- Add new tasks discovered during implementation
- Don't be afraid to revise the todo list as you learn more

**Task Management:**
Writing todos takes time and tokens - use it when helpful for managing complex problems, not for simple requests.
</todo_list_system>
"""

    tool_description = """Use this tool to create and manage a structured task list for complex work sessions.

**IMPORTANT: Only use this tool for complex tasks (3+ steps). For simple requests, just do the work directly.**

## When to Use

Use this tool in these scenarios:
1. **Complex multi-step tasks**: When a task requires 3 or more distinct steps or actions
2. **Non-trivial tasks**: Tasks requiring careful planning or multiple operations
3. **User explicitly requests todo list**: When the user directly asks you to track tasks
4. **Multiple tasks**: When users provide a list of things to be done
5. **Dynamic planning**: When the plan may need updates based on intermediate results

## When NOT to Use

Skip this tool when:
1. The task is straightforward and takes less than 3 steps
2. The task is trivial and tracking provides no benefit
3. The task is purely conversational or informational
4. It's clear what needs to be done and you can just do it

## How to Use

1. **Starting a task**: Mark it as `in_progress` BEFORE beginning work
2. **Completing a task**: Mark it as `completed` IMMEDIATELY after finishing
3. **Updating the list**: Add new tasks, remove irrelevant ones, or update descriptions as needed
4. **Multiple updates**: You can make several updates at once (e.g., complete one task and start the next)

## Task States

- `pending`: Task not yet started
- `in_progress`: Currently working on (can have multiple if tasks run in parallel)
- `completed`: Task finished successfully

## Task Completion Requirements

**CRITICAL: Only mark a task as completed when you have FULLY accomplished it.**

Never mark a task as completed if:
- There are unresolved issues or errors
- Work is partial or incomplete
- You encountered blockers preventing completion
- You couldn't find necessary resources or dependencies
- Quality standards haven't been met

If blocked, keep the task as `in_progress` and create a new task describing what needs to be resolved.

## Best Practices

- Create specific, actionable items
- Break complex tasks into smaller, manageable steps
- Use clear, descriptive task names
- Update task status in real-time as you work
- Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
- Remove tasks that are no longer relevant
- **IMPORTANT**: When you write the todo list, mark your first task(s) as `in_progress` immediately
- **IMPORTANT**: Unless all tasks are completed, always have at least one task `in_progress` to show progress

Being proactive with task management demonstrates thoroughness and ensures all requirements are completed successfully.

**Remember**: If you only need a few tool calls to complete a task and it's clear what to do, it's better to just do the task directly and NOT use this tool at all.
"""

    return TodoMiddleware(system_prompt=system_prompt, tool_description=tool_description)


# ThreadDataMiddleware must be before SandboxMiddleware to ensure thread_id is available
# UploadsMiddleware should be after ThreadDataMiddleware to access thread_id
# DanglingToolCallMiddleware patches missing ToolMessages before model sees the history
# SummarizationMiddleware should be early to reduce context before other processing
# TodoListMiddleware should be before ClarificationMiddleware to allow todo management
# TitleMiddleware generates title after first exchange
# MemoryMiddleware queues conversation for memory update (after TitleMiddleware)
# ViewImageMiddleware should be before ClarificationMiddleware to inject image details before LLM
# ToolErrorHandlingMiddleware should be before ClarificationMiddleware to convert tool exceptions to ToolMessages
# ClarificationMiddleware should be last to intercept clarification requests after model calls
def build_middlewares(
    config: RunnableConfig,
    model_name: str | None,
    agent_name: str | None = None,
    custom_middlewares: list[AgentMiddleware] | None = None,
    *,
    available_skills: set[str] | None = None,
    app_config: AppConfig | None = None,
    deferred_setup=None,
    mcp_routing_middleware: AgentMiddleware | None = None,
    user_id: str | None = None,
    office_pm: bool = False,
    office_managed: bool = False,
    office_todos_enabled: bool = False,
    subagent_enabled: bool | None = None,
):
    """Build the lead-agent middleware chain based on runtime configuration.

    Public entry point for the lead agent's full middleware composition. Used by
    ``make_lead_agent`` and by the embedded ``DeerFlowClient`` (a lead-agent variant
    that needs the identical chain). Keep this name stable: it is imported across a
    module boundary, so renames/signature changes ripple into ``client.py``.

    Args:
        config: Runtime configuration whose context contains options such as is_plan_mode.
        model_name: Resolved runtime model name; gates vision-only middleware.
        agent_name: If provided, MemoryMiddleware will use per-agent memory storage.
        custom_middlewares: Optional list of custom middlewares to inject into the chain.
        app_config: Explicit AppConfig; falls back to ``get_app_config()`` when omitted.
        deferred_setup: Optional deferred-MCP-tool setup that attaches
            ``DeferredToolFilterMiddleware`` when ``tool_search`` is enabled.
        mcp_routing_middleware: Optional PR2 middleware that auto-promotes
            deferred MCP schemas before the deferred filter runs.
        user_id: Effective user ID for user-scoped skill loading. Passed through
            to ``SkillActivationMiddleware`` so it can resolve per-user custom skills.
        subagent_enabled: Resolved tool availability, when supplied by the agent
            factory. This takes precedence over a stale request-level switch.

    Returns:
        List of middleware instances.
    """
    resolved_app_config = app_config or get_app_config()
    middlewares = build_lead_runtime_middlewares(app_config=resolved_app_config, lazy_init=True)

    # Always inject current date (and optionally memory) as <system-reminder> into the
    # first HumanMessage to keep the system prompt fully static for prefix-cache reuse.
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware

    middlewares.append(DynamicContextMiddleware(agent_name=agent_name, app_config=resolved_app_config))

    # Deterministically load a full SKILL.md when the user starts the turn with
    # /skill-name. This keeps the base system prompt metadata-only while giving
    # explicit user activation priority over model-side relevance guessing.
    from deerflow.agents.middlewares.skill_activation_middleware import SkillActivationMiddleware

    middlewares.append(SkillActivationMiddleware(available_skills=available_skills, app_config=resolved_app_config, user_id=user_id))

    # Capture completed task delegations and loaded skill files before
    # summarization can compact them, then inject durable context channels
    # (summary + ledger + skills) into model calls.
    from deerflow.agents.middlewares.durable_context_middleware import DurableContextMiddleware

    middlewares.append(
        DurableContextMiddleware(
            skills_container_path=resolved_app_config.skills.container_path,
            skill_file_read_tool_names=resolved_app_config.summarization.skill_file_read_tool_names,
        )
    )

    # Add summarization middleware if enabled
    summarization_middleware = _create_summarization_middleware(app_config=resolved_app_config)
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)

    # Add TodoList middleware if plan mode is enabled
    cfg = _get_runtime_context(config)
    is_plan_mode = cfg.get("is_plan_mode", False)
    todo_list_middleware = None if office_pm else _create_todo_list_middleware(is_plan_mode)
    if office_pm and office_todos_enabled:
        todo_list_middleware = TodoMiddleware(
            enforce_completion=False,
            system_prompt='Use write_todos when helpful to track multi-step work. Keep statuses factual; simple questions need no list. Incomplete todos do not prevent reporting blockers or ending a Run.',
            tool_description=OFFICE_TODO_TOOL_DESCRIPTION,
        )
    if todo_list_middleware is not None:
        middlewares.append(todo_list_middleware)

    # Add TokenUsageMiddleware when token_usage tracking is enabled
    if resolved_app_config.token_usage.enabled:
        middlewares.append(TokenUsageMiddleware())

    # Add TitleMiddleware
    middlewares.append(TitleMiddleware(app_config=resolved_app_config))

    # Add MemoryMiddleware (after TitleMiddleware) — skipped in enabled tool mode
    if should_use_memory_tools(resolved_app_config.memory):
        pass
    else:
        if resolved_app_config.memory.mode == "tool" and not resolved_app_config.memory.enabled:
            logger.warning("memory.mode is 'tool' but memory.enabled is false; memory tools will not be registered.")
        middlewares.append(MemoryMiddleware(agent_name=agent_name, memory_config=resolved_app_config.memory))

    # Add ViewImageMiddleware only if the current model supports vision.
    # Use the resolved runtime model_name from make_lead_agent to avoid stale config values.
    model_config = resolved_app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        middlewares.append(ViewImageMiddleware())

    # Auto-promote deferred MCP schemas from PR1 routing metadata before the
    # deferred filter decides which schemas to hide for this model call.
    if mcp_routing_middleware is not None:
        middlewares.append(mcp_routing_middleware)

    # Hide deferred tool schemas from model binding until tool_search promotes them.
    # The deferred set + catalog hash come from the build-time setup (assembled
    # after tool-policy filtering); promotion is read from graph state.
    if deferred_setup is not None and deferred_setup.deferred_names:
        from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware

        middlewares.append(DeferredToolFilterMiddleware(deferred_setup.deferred_names, deferred_setup.catalog_hash))
        from deerflow.agents.middlewares.mcp_routing_middleware import assert_mcp_routing_before_deferred_filter

        assert_mcp_routing_before_deferred_filter(middlewares)

    # Coalesce every SystemMessage into a single leading one before the request
    # reaches the provider. Strict backends (vLLM, SGLang, Qwen, Anthropic)
    # reject non-leading SystemMessages. See system_message_coalescing_middleware.py.
    from deerflow.agents.middlewares.system_message_coalescing_middleware import SystemMessageCoalescingMiddleware

    middlewares.append(SystemMessageCoalescingMiddleware())

    # Add SubagentLimitMiddleware to truncate excess parallel task calls
    if subagent_enabled is None:
        subagent_enabled = cfg.get("subagent_enabled", False)
    if subagent_enabled:
        max_concurrent_subagents = cfg.get("max_concurrent_subagents", MAX_CONCURRENT_SUBAGENTS)
        max_total_subagents = cfg.get("max_total_subagents", _default_max_total_subagents(resolved_app_config))
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents, max_total=max_total_subagents))

    # LoopDetectionMiddleware — detect and break repetitive tool call loops
    loop_detection_config = resolved_app_config.loop_detection
    if loop_detection_config.enabled and not office_managed:
        middlewares.append(LoopDetectionMiddleware.from_config(loop_detection_config))

    # TokenBudgetMiddleware - enforce per-run token limits
    token_budget_config = resolved_app_config.token_budget
    if token_budget_config.enabled:
        from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware

        middlewares.append(TokenBudgetMiddleware.from_config(token_budget_config))

    # Inject custom middlewares before ClarificationMiddleware
    if custom_middlewares:
        middlewares.extend(custom_middlewares)

    # A provider may return an empty AIMessage after tool execution. Retry the
    # final response once, then persist a visible error fallback rather than
    # allowing LangChain's no-tool-call router to end a silent successful run.
    middlewares.append(TerminalResponseMiddleware())

    # SafetyFinishReasonMiddleware — suppress tool execution when the provider
    # safety-terminated the response. Registered after the terminal-response
    # and custom middlewares so LangChain's reverse-order after_model dispatch
    # runs Safety first; cleared tool_calls then flow through the remaining
    # accounting/terminal guards without firing extra alarms.
    safety_config = resolved_app_config.safety_finish_reason
    if safety_config.enabled:
        middlewares.append(SafetyFinishReasonMiddleware.from_config(safety_config))

    # ClarificationMiddleware should always be last
    middlewares.append(ClarificationMiddleware())
    return middlewares


def _available_skill_names(agent_config, is_bootstrap: bool) -> set[str] | None:
    if is_bootstrap:
        return set(_BOOTSTRAP_SKILL_NAMES)
    if agent_config and agent_config.skills is not None:
        return set(agent_config.skills)
    return None


def _load_enabled_employee_skills(available_skills: set[str] | None, *, app_config: AppConfig, user_id: str | None = None) -> list[Skill]:
    try:
        from deerflow.agents.lead_agent.prompt import get_enabled_skills_for_config

        skills = get_enabled_skills_for_config(app_config, user_id=user_id)
    except Exception:
        logger.exception("Failed to load the employee Skill catalog")
        raise

    if available_skills is None:
        return skills
    return [skill for skill in skills if skill.name in available_skills]


def make_lead_agent(config: RunnableConfig):
    """LangGraph graph factory; keep the signature compatible with LangGraph Server."""
    runtime_config = _get_runtime_context(config)
    runtime_app_config = runtime_config.get("app_config")
    return _make_lead_agent(config, app_config=runtime_app_config or get_app_config())


def _make_lead_agent(config: RunnableConfig, *, app_config: AppConfig):
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools
    from deerflow.tools.builtins import setup_agent, update_agent
    from deerflow.tools.builtins.tool_search import assemble_deferred_tools, build_mcp_routing_middleware, get_mcp_routing_hints_prompt_section

    cfg = _get_runtime_context(config)
    resolved_app_config = app_config

    # Extract user_id for user-scoped skill loading.
    # Use the authenticated runtime context, then the current user contextvar.
    from deerflow.runtime.user_context import get_effective_user_id

    runtime_user_id = cfg.get("user_id")
    resolved_user_id = str(runtime_user_id) if runtime_user_id else get_effective_user_id()

    thinking_enabled = cfg.get("thinking_enabled", True)
    reasoning_effort = cfg.get("reasoning_effort", None)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    is_plan_mode = cfg.get("is_plan_mode", False)
    subagent_enabled = cfg.get("subagent_enabled", False)
    max_concurrent_subagents = clamp_subagent_concurrency(cfg.get("max_concurrent_subagents", MAX_CONCURRENT_SUBAGENTS))
    max_total_subagents = clamp_total_subagents_per_run(cfg.get("max_total_subagents", _default_max_total_subagents(resolved_app_config)))
    is_bootstrap = cfg.get("is_bootstrap", False)
    non_interactive = bool(cfg.get("non_interactive", False))
    agent_name = validate_agent_name(cfg.get("agent_name"))
    office_managed_agent = is_office_managed_agent(agent_name, user_id=resolved_user_id)

    agent_config = load_agent_config(agent_name) if not is_bootstrap else None
    office_authority: AgentAuthoritySpec | None = None
    office_preselected_employee_ids: list[str] = []
    if office_managed_agent:
        runtime_contract = agent_config.runtime_contract if agent_config else None
        runtime_contract = runtime_contract if isinstance(runtime_contract, dict) else {}
        office_authority = normalize_authority(runtime_contract.get("authority"))
        # One persisted authority fact decides whether the native task tool is
        # present. Child executors still force subagent_enabled=False.
        subagent_enabled = office_authority.can_delegate
        raw_preselected_employee_ids = (config.get("metadata") or {}).get(
            "office_preselected_employee_ids"
        )
        if isinstance(raw_preselected_employee_ids, list):
            office_preselected_employee_ids = [
                str(employee_id).strip()
                for employee_id in raw_preselected_employee_ids
                if str(employee_id).strip()
            ]
    agent_soul = (
        load_agent_soul(agent_name)
        if not is_bootstrap
        else None
    )
    available_skills = _available_skill_names(agent_config, is_bootstrap)
    # Custom agent model from agent config (if any), or None to let _resolve_model_name pick the default
    agent_model_name = agent_config.model if agent_config and agent_config.model else None

    # Final model name resolution: request → agent config → global default, with fallback for unknown names
    model_name = _resolve_model_name(requested_model_name or agent_model_name, app_config=resolved_app_config)

    model_config = resolved_app_config.get_model_config(model_name)

    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    saved_thinking = getattr(model_config, 'default_thinking_enabled', None)
    if office_managed_agent and saved_thinking is not None:
        thinking_enabled = saved_thinking
        # The saved employee profile owns this setting, including during resume.
        reasoning_effort = None
    if thinking_enabled and not model_config.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_name}' does not support it; fallback to non-thinking mode.")
        thinking_enabled = False

    logger.info(
        "Create Agent(%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s, max_total_subagents: %s",
        agent_name or "default",
        thinking_enabled,
        reasoning_effort,
        model_name,
        is_plan_mode,
        subagent_enabled,
        max_concurrent_subagents,
        max_total_subagents,
    )

    # Inject run metadata for LangSmith trace tagging
    if "metadata" not in config:
        config["metadata"] = {}

    config["metadata"].update(
        {
            "agent_name": agent_name or "default",
            "model_name": model_name or "default",
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
            "is_plan_mode": is_plan_mode,
            "subagent_enabled": subagent_enabled,
            "tool_groups": agent_config.tool_groups if agent_config else None,
            "available_skills": sorted(available_skills) if available_skills is not None else None,
            "office_authority": (
                office_authority.to_dict() if office_authority is not None else None
            ),
        }
    )

    # Inject tracing callbacks at the graph invocation root so a single LangGraph
    # run produces one trace with all node / LLM / tool calls as child spans,
    # AND so the Langfuse handler sees ``on_chain_start(parent_run_id=None)`` and
    # actually propagates ``langfuse_session_id`` / ``langfuse_user_id`` from
    # ``config["metadata"]`` onto the trace. Without root-level attachment the
    # model is a nested observation and the handler strips ``langfuse_*`` keys.
    tracing_callbacks = build_tracing_callbacks()
    if tracing_callbacks:
        existing = config.get("callbacks") or []
        if not isinstance(existing, list):
            existing = list(existing)
        config["callbacks"] = [*existing, *tracing_callbacks]

    employee_skills = _load_enabled_employee_skills(available_skills, app_config=resolved_app_config, user_id=resolved_user_id)

    # Build the current skill-search setup independently from tool_search.


    if is_bootstrap:
        # Special bootstrap agent with minimal prompt for initial custom agent creation flow
        # Keep the bootstrap skill set intentionally narrow so agent creation
        # remains deterministic before the custom agent's own config exists.
        bootstrap_skills = [s for s in employee_skills if s.name in _BOOTSTRAP_SKILL_NAMES]
        raw_tools = get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled, app_config=resolved_app_config) + [setup_agent]
        filtered = [t for t in raw_tools if t.name in {'setup_agent', 'read_file', 'review_skill_package'}]
        if non_interactive:
            filtered = [tool for tool in filtered if tool.name not in _NON_INTERACTIVE_DISABLED_TOOL_NAMES]
        final_tools, setup = assemble_deferred_tools(filtered, enabled=resolved_app_config.tool_search.enabled)
        mcp_routing_middleware = build_mcp_routing_middleware(
            final_tools,
            setup,
            top_k=resolved_app_config.tool_search.auto_promote_top_k,
        )
        if should_use_memory_tools(resolved_app_config.memory):
            _append_memory_tools_without_name_conflicts(final_tools)
        _record_runtime_facts(
            config,
            model_name=model_name,
            model_config=model_config,
            final_tools=final_tools,
        )
        return create_agent(
            model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, app_config=resolved_app_config, attach_tracing=False),
            tools=final_tools,
            middleware=build_middlewares(
                config,
                model_name=model_name,
                app_config=resolved_app_config,
                deferred_setup=setup,
                mcp_routing_middleware=mcp_routing_middleware,
                available_skills=available_skills,
                user_id=resolved_user_id,
            ),
            system_prompt=apply_prompt_template(
                subagent_enabled=subagent_enabled,
                max_concurrent_subagents=max_concurrent_subagents,
                max_total_subagents=max_total_subagents,
                app_config=resolved_app_config,
                deferred_tools=tuple(tool for tool in filtered if tool.name in setup.deferred_names),
                skills=bootstrap_skills,
                agent_soul=None,
            ),
            state_schema=ThreadState,
        )

    # Standalone DeerFlow custom agents may update themselves. Little Office
    # employees are governed by office.yaml and only change through the
    # employee-management contract.
    # Build Skill discovery from the employee Skill catalog. Tool authorization
    # is resolved independently from the employee tools.yaml contract.
    extra_tools = [update_agent] if agent_name and not office_managed_agent else []
    if office_authority is not None:
        if not office_authority.can_delegate and agent_name:
            extra_tools.append(build_report_to_pm_tool(agent_name))
        if office_authority.can_delegate and agent_name:
            extra_tools.append(capture_user_message)
            extra_tools.append(build_search_employee_registry_tool(resolved_user_id))
    # Default lead agent (unchanged behavior)
    tool_load_kwargs = {
        "model_name": model_name,
        "groups": agent_config.tool_groups if agent_config else None,
        "subagent_enabled": subagent_enabled,
        "app_config": resolved_app_config,
    }
    tool_contract = None
    if office_managed_agent:
        from deerflow.config.agents_config import resolve_agent_dir
        from deerflow.office.tool_contract import employee_tool_contract, restrict_to_tool_snapshot
        tool_contract = employee_tool_contract(
            resolve_agent_dir(agent_name, user_id=resolved_user_id), agent_config.model_dump(),
            app_config=resolved_app_config, model_name=model_name)
        snapshot = cfg.get('office_tool_contract')
        if snapshot is not None:
            tool_contract = restrict_to_tool_snapshot(tool_contract, snapshot)
        tool_load_kwargs['groups'] = None
        tool_load_kwargs['allowed_configured_tool_names'] = list(tool_contract.runtime_names)
        tool_load_kwargs['allowed_external_tool_names'] = list(tool_contract.runtime_names)
    raw_tools = get_available_tools(**tool_load_kwargs)
    filtered = raw_tools + extra_tools
    if tool_contract is not None:
        from deerflow.office.tool_contract import select_contract_tools
        filtered = select_contract_tools(filtered, tool_contract)
    if non_interactive:
        filtered = [tool for tool in filtered if tool.name not in _NON_INTERACTIVE_DISABLED_TOOL_NAMES]
    final_tools, setup = assemble_deferred_tools(filtered, enabled=resolved_app_config.tool_search.enabled)
    mcp_routing_middleware = build_mcp_routing_middleware(
        final_tools,
        setup,
        top_k=resolved_app_config.tool_search.auto_promote_top_k,
    )
    mcp_routing_hints_section = get_mcp_routing_hints_prompt_section(filtered, deferred_names=setup.deferred_names)
    if should_use_memory_tools(resolved_app_config.memory):
        _append_memory_tools_without_name_conflicts(final_tools)
    if tool_contract is not None:
        final_tools = select_contract_tools(final_tools, tool_contract)
    from deerflow.skills.access import bind_skill_read_tools

    final_tools = bind_skill_read_tools(final_tools, employee_skills, app_config=resolved_app_config,
                                       user_id=resolved_user_id, manage_custom=office_managed_agent)
    from deerflow.office.project_knowledge_service import knowledge_prompt
    from deerflow.office.work_context import work_context_prompt
    from deerflow.runtime.context_keys import OFFICE_PROJECT_KNOWLEDGE_CONTEXT_KEY, OFFICE_WORK_CONTEXT_KEY

    authority_section = (
        build_authority_prompt(office_authority, preselected_employee_ids=office_preselected_employee_ids)
        if office_authority is not None else ""
    )
    if office_authority is not None and office_authority.can_delegate:
        authority_section += work_context_prompt(cfg.get(OFFICE_WORK_CONTEXT_KEY), user_id=resolved_user_id)
    authority_section += knowledge_prompt(cfg.get(OFFICE_PROJECT_KNOWLEDGE_CONTEXT_KEY))
    middlewares = build_middlewares(
        config,
        model_name=model_name,
        agent_name=agent_name,
        app_config=resolved_app_config,
        deferred_setup=setup,
        mcp_routing_middleware=mcp_routing_middleware,
        available_skills=available_skills,
        user_id=resolved_user_id,
        office_pm=office_authority is not None and office_authority.can_delegate,
        office_managed=office_managed_agent,
        office_todos_enabled=tool_contract is not None and 'write_todos' in tool_contract.runtime_names,
        subagent_enabled=any(tool.name == 'task' for tool in final_tools),
    )
    audit_tools = list(final_tools)
    if tool_contract is not None:
        bound_names = {tool.name for tool in audit_tools}
        for middleware in middlewares:
            for tool in getattr(middleware, 'tools', ()) or ():
                if tool.name in tool_contract.runtime_names and tool.name not in bound_names:
                    audit_tools.append(tool)
                    bound_names.add(tool.name)
    _record_runtime_facts(
        config,
        model_name=model_name,
        model_config=model_config,
        final_tools=audit_tools,
    )
    employee_guard = []
    if office_managed_agent:
        from deerflow.agents.middlewares.office_round_budget import OfficeRoundBudgetMiddleware
        middlewares.insert(0, OfficeRoundBudgetMiddleware())
    workspace_ref = (config.get('context') or {}).get('office_execution_workspace')
    if office_managed_agent:
        from deerflow.office.execution_workspace import workspace_guidance
        authority_section += '\n' + workspace_guidance(workspace_ref)
    if tool_contract is not None:
        from deerflow.office.tool_guard import EmployeeToolGuard
        employee_guard = [EmployeeToolGuard(resolve_agent_dir(agent_name, user_id=resolved_user_id), tool_contract)]
    return create_agent(
        model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort, app_config=resolved_app_config, attach_tracing=False),
        tools=final_tools,
        middleware=employee_guard + middlewares,
        system_prompt=apply_prompt_template(
            subagent_enabled=subagent_enabled,
            max_concurrent_subagents=max_concurrent_subagents,
            max_total_subagents=max_total_subagents,
            agent_name=agent_name,
            app_config=resolved_app_config,
            deferred_tools=tuple(tool for tool in filtered if tool.name in setup.deferred_names),
            mcp_routing_hints_section=mcp_routing_hints_section,
            skills=employee_skills,
            self_update_enabled=not office_managed_agent,
            agent_soul=agent_soul,
            office_authority_section=authority_section,
            office_employee_name=(agent_config.description or agent_name) if office_managed_agent and agent_config else None,
            office_user_id=resolved_user_id,
        ),
        state_schema=ThreadState,
    )
