import logging
from collections.abc import Iterable
from typing import Any

from langchain.tools import BaseTool

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_variable
from deerflow.sandbox.security import is_host_bash_allowed
from deerflow.tools.builtins import ask_clarification_tool, present_file_tool, review_skill_package, task_tool, view_image_tool
from deerflow.tools.mcp_metadata import tag_mcp_tool
from deerflow.tools.sync import make_sync_tool_wrapper

logger = logging.getLogger(__name__)

from deerflow.office.library_adapter import library_aware_web_tool, search_office_library

BUILTIN_TOOLS = [
    search_office_library,
    present_file_tool,
    ask_clarification_tool,
    review_skill_package,
]

SUBAGENT_TOOLS = [
    task_tool,
    # task_status_tool is no longer exposed to LLM (backend handles polling internally)
]


def _is_host_bash_tool(tool: object) -> bool:
    """Return True if the tool config represents a host-bash execution surface."""
    group = getattr(tool, "group", None)
    use = getattr(tool, "use", None)
    if group == "bash":
        return True
    if use == "deerflow.sandbox.tools:bash_tool":
        return True
    return False


def _ensure_sync_invocable_tool(tool: BaseTool) -> BaseTool:
    """Attach a sync wrapper to async-only tools used by sync agent callers."""
    if getattr(tool, "func", None) is None and getattr(tool, "coroutine", None) is not None:
        tool.func = make_sync_tool_wrapper(tool.coroutine, tool.name)
    return tool


def _select_runtime_tool_configs(
    config: AppConfig,
    groups: list[str] | None,
    allowed_configured_tool_names: Iterable[str] | None = None,
) -> tuple[list[Any], dict[str, str]]:
    requested_groups = set(groups) if groups is not None else None
    configured_allowlist = (
        None
        if allowed_configured_tool_names is None
        else {
            str(name).strip()
            for name in allowed_configured_tool_names
            if str(name).strip()
        }
    )
    tool_configs = [
        tool
        for tool in config.tools
        if requested_groups is None or tool.group in requested_groups
    ]
    unavailable_reasons: dict[str, str] = {}

    if configured_allowlist is not None:
        denied_groups = {
            str(tool.group)
            for tool in tool_configs
            if str(tool.name) not in configured_allowlist
        }
        tool_configs = [
            tool for tool in tool_configs if str(tool.name) in configured_allowlist
        ]
        unavailable_reasons.update(
            {group: "not_authorized" for group in denied_groups}
        )

    if not is_host_bash_allowed(config):
        blocked_groups = {
            str(tool.group)
            for tool in tool_configs
            if _is_host_bash_tool(tool)
        }
        tool_configs = [tool for tool in tool_configs if not _is_host_bash_tool(tool)]
        unavailable_reasons.update(
            {group: 'host_bash_disabled' for group in blocked_groups}
        )

    if requested_groups is not None:
        configured_groups = {str(tool.group) for tool in tool_configs}
        for group in requested_groups - configured_groups:
            unavailable_reasons.setdefault(group, 'not_configured')

    return tool_configs, unavailable_reasons


def get_runtime_tool_inventory(
    groups: list[str] | None = None,
    include_mcp: bool = True,
    model_name: str | None = None,
    subagent_enabled: bool = False,
    allowed_configured_tool_names: Iterable[str] | None = None,
    allowed_external_tool_names: Iterable[str] | None = None,
    *,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    '''Describe authorized tool groups separately from tools bound at runtime.'''
    config = app_config or get_app_config()
    tool_configs, unavailable_reasons = _select_runtime_tool_configs(
        config,
        groups,
        allowed_configured_tool_names,
    )
    available_groups = sorted({str(tool.group) for tool in tool_configs})
    authorized_groups = sorted(
        set(groups)
        if groups is not None
        else {str(tool.group) for tool in config.tools}
    )
    runtime_tools = get_available_tools(
        groups=groups,
        include_mcp=include_mcp,
        model_name=model_name,
        subagent_enabled=subagent_enabled,
        allowed_configured_tool_names=allowed_configured_tool_names,
        allowed_external_tool_names=allowed_external_tool_names,
        app_config=config,
    )
    unavailable_groups = sorted(set(authorized_groups) - set(available_groups))
    # 组成员保持 config.yaml 里的声明顺序，便于面板按配置顺序展示
    group_members: dict[str, list[str]] = {}
    for tool in tool_configs:
        group_members.setdefault(str(tool.group), []).append(str(tool.name))
    return {
        'authorized_tool_groups': authorized_groups,
        'available_tool_groups': available_groups,
        'available_tool_group_members': {
            group: list(names) for group, names in group_members.items()
        },
        'unavailable_tool_groups': unavailable_groups,
        'available_tool_names': sorted({tool.name for tool in runtime_tools}),
        'unavailable_reasons': {
            group: unavailable_reasons.get(group, 'not_configured')
            for group in unavailable_groups
        },
    }


def get_available_tools(
    groups: list[str] | None = None,
    include_mcp: bool = True,
    model_name: str | None = None,
    subagent_enabled: bool = False,
    allowed_configured_tool_names: Iterable[str] | None = None,
    allowed_external_tool_names: Iterable[str] | None = None,
    *,
    app_config: AppConfig | None = None,
) -> list[BaseTool]:
    """Get all available tools from config.

    Note: MCP tools should be initialized at application startup using
    `initialize_mcp_tools()` from deerflow.mcp module.

    Args:
        groups: Optional list of tool groups to filter by.
        include_mcp: Whether to include tools from MCP servers (default: True).
        model_name: Optional model name to determine if vision tools should be included.
        subagent_enabled: Whether to include subagent tools (task, task_status).
        allowed_configured_tool_names: Exact config-defined tool names authorized
            for a managed agent. None preserves standalone DeerFlow behavior.
        allowed_external_tool_names: Exact MCP/ACP tool names authorized for a
            managed agent. None preserves standalone DeerFlow behavior.

    Returns:
        List of available tools.
    """
    config = app_config or get_app_config()
    external_allowlist = (
        None
        if allowed_external_tool_names is None
        else {
            str(name).strip()
            for name in allowed_external_tool_names
            if str(name).strip()
        }
    )
    configured_allowlist = (
        None
        if allowed_configured_tool_names is None
        else {
            str(name).strip()
            for name in allowed_configured_tool_names
            if str(name).strip()
        }
    )
    tool_configs, _ = _select_runtime_tool_configs(
        config,
        groups,
        configured_allowlist,
    )

    loaded_tools_raw = [(cfg, resolve_variable(cfg.use, BaseTool)) for cfg in tool_configs]
    if configured_allowlist is not None:
        loaded_tools_raw = [
            (cfg, loaded)
            for cfg, loaded in loaded_tools_raw
            if str(cfg.name) in configured_allowlist
            and str(loaded.name) in configured_allowlist
        ]

    # Warn when the config ``name`` field and the tool object's ``.name``
    # attribute diverge — this mismatch is the root cause of issue #1803 where
    # the LLM receives one name in its tool schema but the runtime router
    # recognises a different name, producing "not a valid tool" errors.
    for cfg, loaded in loaded_tools_raw:
        if cfg.name != loaded.name:
            logger.warning(
                "Tool name mismatch: config name %r does not match tool .name %r (use: %s). The tool's own .name will be used for binding.",
                cfg.name,
                loaded.name,
                cfg.use,
            )

    loaded_tools = [_ensure_sync_invocable_tool(t) for _, t in loaded_tools_raw]
    if configured_allowlist is not None:
        loaded_tools = [library_aware_web_tool(tool) for tool in loaded_tools]

    # Conditionally add tools based on config
    builtin_tools = [tool for tool in BUILTIN_TOOLS if tool.name != 'search_office_library'
                     or (configured_allowlist is not None and tool.name in configured_allowlist)]
    if configured_allowlist is not None:
        from deerflow.sandbox.powershell_tools import powershell_run, powershell_read, powershell_list, powershell_stop
        from deerflow.office.delivery_tool import publish_task_result
        builtin_tools.extend(t for t in (powershell_run, powershell_read, powershell_list, powershell_stop, publish_task_result)
                             if t.name in configured_allowlist and t.name not in {loaded.name for loaded in loaded_tools})
    skill_evolution_config = getattr(config, "skill_evolution", None)
    if getattr(skill_evolution_config, "enabled", False):
        from deerflow.tools.skill_manage_tool import skill_manage_tool

        builtin_tools.append(skill_manage_tool)

    # Add subagent tools only if enabled via runtime parameter
    if subagent_enabled:
        builtin_tools.extend(SUBAGENT_TOOLS)
        logger.info("Including subagent tools (task)")

    # If no model_name specified, use the first model (default)
    if model_name is None and config.models:
        model_name = config.models[0].name

    # Add view_image_tool only if the model supports vision
    model_config = config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        builtin_tools.append(view_image_tool)
        logger.info(f"Including view_image_tool for model '{model_name}' (supports_vision=True)")

    # Get cached MCP tools if enabled
    # NOTE: We use ExtensionsConfig.from_file() instead of config.extensions
    # to always read the latest configuration from disk. This ensures that changes
    # made through the Gateway API (which runs in a separate process) are immediately
    # reflected when loading MCP tools.
    mcp_tools = []
    if include_mcp:
        try:
            from deerflow.config.extensions_config import ExtensionsConfig
            from deerflow.mcp.cache import get_cached_mcp_tools

            extensions_config = ExtensionsConfig.from_file()
            if extensions_config.get_enabled_mcp_servers():
                mcp_tools = get_cached_mcp_tools()
                if mcp_tools:
                    logger.info(f"Using {len(mcp_tools)} cached MCP tool(s)")

                    # Tag MCP-sourced tools so deferred-tool assembly (done at
                    # the agent construction site, after employee contract filtering)
                    # can identify them. No ContextVar / registry is built here;
                    # the deferred catalog + tool_search tool are assembled per
                    # agent from its already authorized tool list.
                    for t in mcp_tools:
                        tag_mcp_tool(t)
                    if external_allowlist is not None:
                        mcp_tools = [
                            tool for tool in mcp_tools if tool.name in external_allowlist
                        ]
        except ImportError:
            logger.warning("MCP module not available. Install 'langchain-mcp-adapters' package to enable MCP tools.")
        except Exception as e:
            logger.error(f"Failed to get cached MCP tools: {e}")

    # Add invoke_acp_agent tool if any ACP agents are configured
    acp_tools: list[BaseTool] = []
    try:
        from deerflow.tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool

        if app_config is None:
            from deerflow.config.acp_config import get_acp_agents

            acp_agents = get_acp_agents()
        else:
            acp_agents = getattr(config, "acp_agents", {}) or {}
        if acp_agents:
            acp_tools.append(build_invoke_acp_agent_tool(acp_agents))
            if external_allowlist is not None:
                acp_tools = [
                    tool for tool in acp_tools if tool.name in external_allowlist
                ]
            logger.info(f"Including invoke_acp_agent tool ({len(acp_agents)} agent(s): {list(acp_agents.keys())})")
    except Exception as e:
        logger.warning(f"Failed to load ACP tool: {e}")

    logger.info(f"Total tools loaded: {len(loaded_tools)}, built-in tools: {len(builtin_tools)}, MCP tools: {len(mcp_tools)}, ACP tools: {len(acp_tools)}")

    # Deduplicate by tool name — config-loaded tools take priority, followed by
    # built-ins, MCP tools, and ACP tools.  Duplicate names cause the LLM to
    # receive ambiguous or concatenated function schemas (issue #1803).
    all_tools = [_ensure_sync_invocable_tool(t) for t in loaded_tools + builtin_tools + mcp_tools + acp_tools]
    seen_names: set[str] = set()
    unique_tools: list[BaseTool] = []
    for t in all_tools:
        if t.name not in seen_names:
            unique_tools.append(t)
            seen_names.add(t.name)
        else:
            logger.warning(
                "Duplicate tool name %r detected and skipped — check your config.yaml and MCP server registrations (issue #1803).",
                t.name,
            )
    return unique_tools
