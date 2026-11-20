"""Resolve employee tool selections against registered tools and runtime policy."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from deerflow.office.authority import normalize_authority, validate_tool_authority
from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, manifest_revision

COMMON_REQUIRED = frozenset({'read_file'})
PM_REQUIRED = frozenset({'task', 'search_employee_registry', 'capture_user_message'})
TERMINAL_MEMBERS = ('powershell_run', 'powershell_read', 'powershell_list', 'powershell_stop')
TERMINAL_QUERIES = ('powershell_read', 'powershell_list')
# Display groups only. tools.yaml and Run contracts still contain real tool IDs.
EMPLOYEE_TOOL_CHOICES = (
    ('read_file', '读取文件', '读取文件、浏览目录、查找文件和搜索文件内容。', ('read_file', 'ls', 'glob', 'grep')),
    ('apply_patch', '编辑文件', '通过补丁新建、修改、删除或移动文件。', ('apply_patch',)),
    ('web_search', '网络搜索', '搜索网络、读取网页和搜索图片。', ('web_search', 'web_fetch', 'image_search')),
    ('search_office_library', '办公室资料库', '检索用户上传到办公室资料库的资料。', ('search_office_library',)),
    ('powershell_terminal', '终端控制', '执行命令、查询输出、列出和停止本任务的进程。', ('powershell_terminal',)),
)


def with_delivery_capability(names):
    """Delivery accompanies authorized writing; it is not a separate user choice."""
    selected = set(names) - {'publish_task_result'}
    if selected.intersection({'apply_patch', 'powershell_terminal', 'powershell_run'}):
        selected.add('publish_task_result')
    return selected


def employee_tool_choices(catalog, authority):
    """Expose capability groups without expanding the employee's authority."""
    choices = []
    for identity, label, description, names in EMPLOYEE_TOOL_CHOICES:
        members = []
        for name in names:
            entry = catalog.get(name)
            if entry is None:
                continue
            try:
                validate_tool_authority(authority, tool_name=entry.name, tool_group=entry.group, permissions=entry.permissions)
            except ValueError:
                continue
            members.append(name)
        if not members:
            continue
        reasons = sorted({catalog[name].unavailable_reason for name in members if catalog[name].unavailable_reason})
        choices.append({'id': identity, 'label': label, 'description': description,
                        'members': members, 'required': identity == 'read_file',
                        'unavailableReason': ', '.join(reasons)})
    if authority.can_delegate:
        members = []
        for name in TERMINAL_QUERIES:
            entry = catalog[name]
            try:
                validate_tool_authority(authority, tool_name=entry.name, tool_group=entry.group, permissions=entry.permissions)
            except ValueError:
                continue
            members.append(name)
        if members:
            choices.append({'id': 'powershell_readonly', 'label': '终端查询（只读）',
                            'description': '查询当前项目的命令状态和输出，不执行或停止命令。',
                            'members': members, 'required': False, 'unavailableReason': ''})
        choices.append({'id': 'write_todos', 'label': '任务计划', 'description': 'write_todos：创建和更新任务待办清单，不修改项目文件。',
                        'members': ['write_todos'], 'required': False, 'unavailableReason': ''})
    return choices


def required_tool_names(authority) -> set[str]:
    return set(COMMON_REQUIRED | (PM_REQUIRED if authority.can_delegate else {'report_to_pm'}))


@dataclass(frozen=True)
class ToolEntry:
    id: str
    name: str
    description: str
    group: str = ''
    permissions: tuple[str, ...] = ()
    source: str = 'builtin'
    unavailable_reason: str = ''
    members: tuple[str, ...] = ()
    service_grant: str = ""

    @property
    def purpose(self) -> str:
        return _TOOL_PURPOSES.get(self.name) or self.description.strip().split('\n\n', 1)[0][:300]


_TOOL_PURPOSES = {
    'read_file': '读取文件内容。',
    'ls': '列出目录内容。',
    'glob': '按文件名或路径模式查找文件。',
    'grep': '在文件内容中搜索文本或正则表达式。',
    'apply_patch': '通过补丁新建、修改、删除或移动文本文件。',
    'web_search': '搜索互联网上的信息和来源。',
    'web_fetch': '读取指定网页的正文。',
    'image_search': '搜索网络图片。',
    'view_image': '查看图片内容。',
    'search_office_library': '检索用户上传的资料，返回相关片段与来源。',
    'powershell_run': '通过 PowerShell 执行命令。',
    'powershell_read': '读取已有终端执行的状态和输出，可等待完成。',
    'powershell_list': '列出当前项目或工作区的终端执行与服务。',
    'powershell_stop': '停止指定终端执行及其子进程。',
    'publish_task_result': '将独立工作区成果合并并写回绑定项目，保留原生 Git 冲突供处理。',
    'report_to_pm': '向 PM 报告任务阻碍、能力缺口及已完成工作。',
    'present_files': '将已有文件以可预览的文件卡片交给用户。',
    'review_skill_package': '检查本地 Skill 包的内容与风险，不安装远程资源。',
    'memory_search': '搜索可访问的长期记忆。',
    'memory_add': '新增长期记忆。',
    'memory_update': '更新指定长期记忆。',
    'memory_delete': '删除指定长期记忆。',
    'write_todos': '维护当前任务的计划清单，不编辑项目文件。',
    'tool_search': '发现已授权的外部工具并加载调用参数。',
}


def build_tool_catalog(app_config=None, *, model_name=None, include_mcp=True) -> dict[str, ToolEntry]:
    from deerflow.config import get_app_config
    from deerflow.reflection import resolve_variable
    from langchain.tools import BaseTool
    from deerflow.tools.tools import BUILTIN_TOOLS, _select_runtime_tool_configs
    from deerflow.office.routing import build_report_to_pm_tool, build_search_employee_registry_tool
    from deerflow.office.project_knowledge_tool import capture_user_message
    from deerflow.tools.builtins.task_tool import task_tool
    from deerflow.tools.builtins.view_image_tool import view_image_tool
    from deerflow.sandbox.powershell_tools import powershell_run, powershell_read, powershell_list, powershell_stop
    from deerflow.office.delivery_tool import publish_task_result
    from deerflow.agents.memory.tools import get_memory_tools
    from langchain.agents.middleware import TodoListMiddleware
    from deerflow.agents.middlewares.todo_middleware import OFFICE_TODO_TOOL_DESCRIPTION
    config = app_config or get_app_config()
    catalog = {}

    def add(tool, *, identity=None, group='', permissions=(), source='builtin', reason='', service_grant=''):
        key = identity or tool.name
        if key in catalog or any(e.name == tool.name for e in catalog.values()):
            raise ValueError(f'Ambiguous tool registration: {key}')
        catalog[key] = ToolEntry(key, tool.name, tool.description, group, tuple(permissions), source, reason, service_grant=service_grant)

    selected, _ = _select_runtime_tool_configs(config, None)
    available = {t.name for t in selected}
    for item in config.tools:
        tool = resolve_variable(item.use, BaseTool)
        if item.name != tool.name:
            raise ValueError(f'Tool registration name mismatch: {item.name}')
        add(tool, group=item.group, permissions=getattr(item, 'permissions', ()) or (), source='configured',
            reason='' if item.name in available else 'host_bash_disabled')
    for tool in [*BUILTIN_TOOLS, task_tool, capture_user_message,
                 build_report_to_pm_tool('employee'), build_search_employee_registry_tool('default'),
                 *TodoListMiddleware(tool_description=OFFICE_TODO_TOOL_DESCRIPTION).tools]:
        add(tool)
    for tool in (powershell_run, powershell_read, powershell_list, powershell_stop):
        if tool.name not in catalog:
            add(tool, group='powershell_terminal', permissions=() if tool.name in TERMINAL_QUERIES else ('process.execute',))
    if publish_task_result.name not in catalog:
        add(publish_task_result, group='file:write', permissions=('file.write',))
    catalog['powershell_terminal'] = ToolEntry('powershell_terminal', 'powershell_terminal',
        'PowerShell 终端：执行命令、查询输出、列出和停止本任务的进程。',
        'powershell_terminal', ('process.execute',), members=TERMINAL_MEMBERS)
    from deerflow.config.memory_config import should_use_memory_tools
    for tool in get_memory_tools():
        add(tool, reason='' if should_use_memory_tools(config.memory) else 'memory_tools_disabled')
    model = config.get_model_config(model_name) if model_name else (config.models[0] if config.models else None)
    add(view_image_tool, reason='' if model and model.supports_vision else 'vision_not_supported')
    # Discovery helper is generated per authorized deferred catalog, never a provider capability.
    catalog['tool_search'] = ToolEntry('tool_search', 'tool_search', 'Discover authorized external tools for this employee.')
    from deerflow.mcp.cache import get_cached_mcp_tools
    from deerflow.mcp.office_registry import registry
    services = registry.entries() if include_mcp else {}
    external = []
    if include_mcp:
        external = ([tool for service in services.values() for tool in service.tools]
                    if registry.active else get_cached_mcp_tools() or [])
    for tool in external:
        metadata = tool.metadata or {}
        identity = metadata.get('office_tool_id')
        if identity:
            add(tool, identity=identity, source='mcp', service_grant=metadata.get('office_mcp_grant', ''))
    for name, service in services.items():
        members = tuple(t.metadata['office_tool_id'] for t in service.tools
                        if t.metadata['office_tool_id'] in catalog)
        catalog[service.grant] = ToolEntry(service.grant, name, name, source='mcp-service', members=members)
    from deerflow.tools.skill_manage_tool import skill_manage_tool
    from deerflow.tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool
    evolution = getattr(config, 'skill_evolution', None)
    add(skill_manage_tool, reason='' if evolution and evolution.enabled else 'skill_evolution_disabled')
    acp_agents = getattr(config, 'acp_agents', None) or {}
    add(build_invoke_acp_agent_tool(acp_agents), source='acp', reason='' if acp_agents else 'acp_disabled')
    return catalog


def validate_required_tool_names(names: list[str] | None, *, app_config=None) -> None:
    if not names:
        return
    catalog = build_tool_catalog(app_config)
    runtime_names = {entry.name for entry in catalog.values() if not entry.members and entry.source != 'mcp-service'}
    unknown = sorted(set(names) - runtime_names)
    if unknown:
        raise ValueError(
            f'Invalid required_tools: {", ".join(unknown)}. '
            'Use exact names from search_employee_registry employees[].tools, such as apply_patch; '
            'tool groups and display labels are not tool names. Correct the arguments; '
            'this does not indicate an employee capability gap.'
        )


@dataclass(frozen=True)
class ResolvedEmployeeTools:
    revision: str
    authorized_ids: tuple[str, ...]
    available_ids: tuple[str, ...]
    unavailable_reasons: dict[str, str]
    runtime_names: tuple[str, ...]
    derived_groups: tuple[str, ...]
    bindings: dict[str, str] = field(default_factory=dict)
    groups_by_id: dict[str, str] = field(default_factory=dict)

    def snapshot(self):
        return {'revision': self.revision, 'tools': sorted(set(self.authorized_ids) | set(self.bindings)),
                'runtime_names': list(self.runtime_names), 'bindings': dict(self.bindings)}


def validate_tool_discovery_selection(manifest, *, deferred_enabled: bool) -> None:
    if deferred_enabled and 'tool_search' not in manifest.tools and any(t.startswith('mcp/') for t in manifest.tools):
        raise ValueError('Deferred MCP tools require tool_search; retain discovery or remove the MCP selections')


def restrict_to_tool_snapshot(current: ResolvedEmployeeTools, snapshot: dict) -> ResolvedEmployeeTools:
    """A resumed run can lose tools, but cannot gain a new identity or binding."""
    if not isinstance(snapshot, dict) or snapshot.get('revision') != current.revision:
        raise RuntimeError('Employee tool contract changed since Run launch')
    bindings = snapshot.get('bindings')
    if (not isinstance(bindings, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in bindings.items())
            or not isinstance(snapshot.get('tools'), list)
            or not isinstance(snapshot.get('runtime_names'), list)
            or not set(bindings).issubset(snapshot['tools'])
            or set(bindings.values()) != set(snapshot['runtime_names'])):
        raise RuntimeError('Run tool snapshot has no reliable bindings; start a new Run')
    kept = {k: v for k, v in current.bindings.items() if bindings.get(k) == v}
    reasons = dict(current.unavailable_reasons)
    reasons.update({k: 'not_in_run_snapshot' for k in current.available_ids if k not in kept})
    return replace(current, available_ids=tuple(sorted(kept)), runtime_names=tuple(sorted(kept.values())),
                   bindings=kept, unavailable_reasons=reasons,
                   derived_groups=tuple(sorted({current.groups_by_id[k] for k in kept if current.groups_by_id.get(k)})))


def resolve_employee_tools(manifest, *, catalog, authority, execution_kind='direct', deferred_enabled=False) -> ResolvedEmployeeTools:
    required = required_tool_names(authority)
    if missing := required - set(manifest.tools):
        raise ValueError(f'Missing required employee tools: {sorted(missing)}')
    available, names, groups, reasons = [], [], set(), {}
    expanded = set(manifest.tools)
    if 'publish_task_result' in catalog:
        expanded = with_delivery_capability(expanded)
    for identity in manifest.tools:
        entry = catalog.get(identity)
        if entry is not None:
            validate_tool_authority(authority, tool_name=entry.name, tool_group=entry.group, permissions=entry.permissions)
        if identity.startswith('mcp-service/'):
            expanded.discard(identity)
            if deferred_enabled and entry and entry.members:
                expanded.add('tool_search')
        if entry is not None and entry.members:
            expanded.discard(identity)
            expanded.update(entry.members)
    for identity in sorted(expanded):
        entry = catalog.get(identity)
        if entry is None:
            # A previously selected external tool may be temporarily offline. Its exact
            # identity is retained, but it cannot resolve to another server's tool.
            if identity.startswith('mcp/'):
                reasons[identity] = 'external_tool_unavailable'
                continue
            raise ValueError(f'Unknown employee tool: {identity}')
        validate_tool_authority(authority, tool_name=entry.name, tool_group=entry.group, permissions=entry.permissions)
        if authority.can_delegate and entry.name in {'report_to_pm', 'memory_add', 'memory_update', 'memory_delete'}:
            raise ValueError(f'Tool {entry.name} is not permitted for PM')
        if not authority.can_delegate and entry.name in PM_REQUIRED:
            raise ValueError(f'Tool {entry.name} requires PM authority')
        reason = entry.unavailable_reason
        if entry.service_grant and entry.service_grant not in manifest.tools:
            reason = 'select_mcp_service'
        if deferred_enabled and identity.startswith('mcp/') and 'tool_search' not in expanded:
            reason = 'tool_search_required'
        if execution_kind == 'delegated' and entry.name in {'task', 'ask_clarification'}:
            reason = 'delegated_context_denied'
        if reason:
            reasons[identity] = reason
        else:
            available.append(identity)
            names.append(entry.name)
            if entry.group:
                groups.add(entry.group)
    return ResolvedEmployeeTools(manifest_revision(manifest), tuple(sorted(manifest.tools)), tuple(sorted(available)),
                                 reasons, tuple(sorted(names)), tuple(sorted(groups)),
                                 {k: catalog[k].name for k in available},
                                 {k: catalog[k].group for k in available})


def employee_tool_contract(agent_dir, config, *, app_config=None, execution_kind='direct', model_name=None):
    from deerflow.config import get_app_config
    from deerflow.office.model_binding import resolve_employee_model
    app_config = app_config or get_app_config()
    if model_name is not None and app_config.get_model_config(model_name) is None:
        raise ValueError(f'Employee runtime model is unavailable: {model_name}')
    if model_name is None:
        model_name = resolve_employee_model(config)
    authority = normalize_authority((config.get('runtime_contract') or {}).get('authority'))
    catalog = build_tool_catalog(app_config, model_name=model_name)
    manifest = load_tool_manifest(agent_dir)
    return resolve_employee_tools(manifest, catalog=catalog, authority=authority, execution_kind=execution_kind,
                                  deferred_enabled=app_config.tool_search.enabled)


def default_tool_manifest(groups, authority, *, app_config=None, default_tools=()):
    catalog = build_tool_catalog(app_config)
    names = required_tool_names(authority) | set(default_tools)
    defaults = {'search_office_library', 'ask_clarification', 'present_files', 'review_skill_package', 'view_image', 'tool_search', 'memory_search'}
    if authority.can_delegate:
        defaults.update(TERMINAL_QUERIES)
    if not authority.can_delegate:
        defaults.update({'memory_add', 'memory_update', 'memory_delete'})
    for key, entry in catalog.items():
        if entry.source == 'configured' and entry.group in groups:
            try:
                validate_tool_authority(authority, tool_name=entry.name, tool_group=entry.group, permissions=entry.permissions)
            except ValueError:
                continue
            if authority.allowed_configured_tool_names is None or entry.name in authority.allowed_configured_tool_names:
                names.add(key)
    names.update(defaults & catalog.keys())
    if 'apply_patch' in names:
        names.add('publish_task_result')
    manifest = EmployeeToolManifest(1, tuple(sorted(names)))
    resolve_employee_tools(manifest, catalog=catalog, authority=authority)
    return manifest


_OFFICE_TOOL_DESCRIPTIONS = {
    'task': (
        'Delegate to a real Office employee in an independent context and wait for its result. '
        'Use the selected/routed Employee ID as subagent_type, never a generic agent type. '
        'description is a short display label; prompt supplies the goal, necessary background, '
        'constraints and acceptance criteria, not the full parent conversation. '
        'A small task may also require delegation when PM cannot perform it. '
        'workspace_mode=current uses the current environment (normally the bound project); new creates an independent worktree. '
        'continue reuses a returned workspace_id; fork creates an independent worktree from that workspace version. '
        'Use separate worktrees for independent parallel development, sharing an existing scaffold through fork. '
        'required_tools uses only needed names from employees[].tools (e.g. apply_patch, not file:write). '
        'Invalid names require argument correction, not changing employees. Busy employees or missing tools are reported without queuing. '
        'A new delegation does not itself require a new directory. Results identify the actual environment and execution outcome.'
    ),
    'present_files': (
        'Present existing user-facing files in the client, not intermediate files or private processing reads. '
        'Use complete virtual paths returned by tools/runtime. Standalone exports use outputs; '
        'do not copy project files there solely for presentation or as a substitute for requested workspace files. '
    ),
    'web_fetch': (
        'Read a public web page using an exact http(s) URL supplied by the user or returned by web_search/web_fetch. '
        'Do not invent URLs, add www, or use this for authenticated/private pages.'
    ),
    'image_search': (
        'Find online image references before generating visuals that require accurate characters, '
        'objects, poses, scenes or styles. Returned URLs can be used as image-generation references.'
    ),
    'view_image': (
        'Read an image file for visual inspection and display. For non-image files or presenting '
        'multiple files use present_files instead.'
    ),
}


def compact_office_tool_descriptions(tools):
    """Copy only local tool descriptions; preserve schemas, closures and metadata."""
    selected = []
    for tool in tools:
        metadata = getattr(tool, 'metadata', None) or {}
        description = _OFFICE_TOOL_DESCRIPTIONS.get(getattr(tool, 'name', None))
        if description and not metadata.get('office_tool_id') and hasattr(tool, 'model_copy'):
            if tool.description != description:
                tool = tool.model_copy(update={'description': description})
        selected.append(tool)
    return selected


def select_contract_tools(tools, contract):
    """Keep the runtime tools whose stable catalog identity is authorized.

    Builtins and configured tools use their runtime name as their catalog id.
    MCP tools are different: their manifest id is ``mcp/<server>/<original>``
    while LangChain exposes the prefixed runtime name.  The loader attaches
    that stable id as metadata, so authorization must use it here instead of
    guessing from the runtime name.
    """
    # Non-Office integrations may pass a lightweight contract projection with
    # only runtime_names. Office contracts always carry available_ids; this
    # fallback does not change the Office manifest authorization boundary.
    allowed = set(getattr(contract, 'available_ids', contract.runtime_names))
    selected = []
    for tool in tools:
        metadata = getattr(tool, 'metadata', None) or {}
        runtime_name = getattr(tool, 'name', tool if isinstance(tool, str) else None)
        if not runtime_name:
            continue
        identity = metadata.get('office_tool_id') or runtime_name
        if identity in allowed:
            selected.append(tool)
    return compact_office_tool_descriptions(selected) if isinstance(contract, ResolvedEmployeeTools) else selected
