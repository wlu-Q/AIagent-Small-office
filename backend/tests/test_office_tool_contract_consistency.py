"""Contract consistency across model binding, dependency changes and resumes."""
from dataclasses import replace

import pytest
from test_office_pm_tools import pm_factory

from deerflow.office.authority import DEFAULT_AUTHORITY
from deerflow.office.tool_contract import (
    ToolEntry, build_tool_catalog, employee_tool_contract, resolve_employee_tools,
)
from deerflow.office.tool_manifest import EmployeeToolManifest, save_tool_manifest


BASE = ('read_file', 'report_to_pm')


@pytest.fixture
def app(monkeypatch):
    from test_office_employee_lifecycle import employee_app_config
    from deerflow.config.app_config import AppConfig
    payload = employee_app_config().model_dump()
    payload['models'] = [{**payload['models'][0], 'name': 'vision-profile', 'supports_vision': True}]
    payload['memory'].update(enabled=True, mode='tool')
    payload['acp_agents'] = {}
    payload['skill_evolution'] = {'enabled': False}
    monkeypatch.setattr('deerflow.mcp.cache.get_cached_mcp_tools', lambda: [])
    return AppConfig.model_validate(payload)


@pytest.mark.parametrize('vision,selected', [(True, True), (False, True), (True, False)])
def test_actual_run_model_controls_vision(tmp_path, app, vision, selected):
    from app.gateway.model_bindings import resolve_run_app_config
    app.models[0].supports_vision = vision
    run_config = resolve_run_app_config(app, 'vision-profile')
    manifest = EmployeeToolManifest(1, BASE + (('view_image',) if selected else ()))
    save_tool_manifest(tmp_path, manifest, expected_revision='')
    result = employee_tool_contract(tmp_path, {'model': 'vision-profile'},
                                    app_config=run_config, model_name='office-selected-model')
    assert ('view_image' in result.runtime_names) == (vision and selected)


def test_missing_explicit_runtime_model_is_not_a_fallback(tmp_path, app):
    save_tool_manifest(tmp_path, EmployeeToolManifest(1, BASE), expected_revision='')
    with pytest.raises(ValueError, match='model'):
        employee_tool_contract(tmp_path, {}, app_config=app, model_name='missing')


def test_optional_provider_keeps_identity_while_disabled(app):
    catalog = build_tool_catalog(app)
    assert catalog['invoke_acp_agent'].unavailable_reason
    assert catalog['skill_manage'].unavailable_reason
    result = resolve_employee_tools(EmployeeToolManifest(1, BASE + ('invoke_acp_agent',)),
                                    catalog=catalog, authority=DEFAULT_AUTHORITY)
    assert 'invoke_acp_agent' in result.authorized_ids
    assert 'invoke_acp_agent' not in result.runtime_names


def test_resume_cannot_gain_tools_when_dependency_becomes_available(app):
    from deerflow.office.tool_contract import restrict_to_tool_snapshot
    catalog = build_tool_catalog(app)
    manifest = EmployeeToolManifest(1, BASE + ('memory_search',))
    offline = {**catalog, 'memory_search': replace(catalog['memory_search'], unavailable_reason='offline')}
    before = resolve_employee_tools(manifest, catalog=offline, authority=DEFAULT_AUTHORITY)
    now = resolve_employee_tools(manifest, catalog=catalog, authority=DEFAULT_AUTHORITY)
    resumed = restrict_to_tool_snapshot(now, before.snapshot())
    assert 'memory_search' not in resumed.runtime_names
    assert resumed.unavailable_reasons['memory_search'] == 'not_in_run_snapshot'
    assert 'memory_search' in now.runtime_names
    assert restrict_to_tool_snapshot(before, now.snapshot()).runtime_names == before.runtime_names


def test_snapshot_requires_exact_identity_binding_and_revision(app):
    from deerflow.office.tool_contract import restrict_to_tool_snapshot
    catalog = build_tool_catalog(app)
    key = 'mcp/server/search'
    catalog[key] = ToolEntry(key, 'server_search', 'Search', source='mcp')
    manifest = EmployeeToolManifest(1, BASE + (key,))
    before = resolve_employee_tools(manifest, catalog=catalog, authority=DEFAULT_AUTHORITY)
    catalog[key] = replace(catalog[key], name='renamed_search')
    now = resolve_employee_tools(manifest, catalog=catalog, authority=DEFAULT_AUTHORITY)
    assert key not in restrict_to_tool_snapshot(now, before.snapshot()).available_ids
    for broken in ({'revision': now.revision}, {**before.snapshot(), 'revision': 'other'}):
        with pytest.raises(RuntimeError):
            restrict_to_tool_snapshot(now, broken)


@pytest.mark.parametrize('vision,selected', [(True, True), (False, True), (True, False)])
def test_lead_agent_binds_actual_vision_model(pm_factory, app, monkeypatch, vision, selected):
    from app.gateway.model_bindings import resolve_run_app_config
    from deerflow.config.agents_config import AgentConfig, resolve_agent_dir
    from deerflow.tools.builtins.view_image_tool import view_image_tool
    from deerflow.office.tool_manifest import load_tool_manifest, manifest_revision
    build, module = pm_factory
    build(authority=DEFAULT_AUTHORITY)
    directory = resolve_agent_dir('pm')
    old = load_tool_manifest(directory)
    save_tool_manifest(directory, EmployeeToolManifest(1, BASE + (('view_image',) if selected else ())),
                       expected_revision=manifest_revision(old))
    app.models[0].supports_vision = vision
    run_config = resolve_run_app_config(app, 'vision-profile')
    monkeypatch.setattr(module, 'get_app_config', lambda: run_config)
    monkeypatch.setattr(module, '_resolve_model_name', lambda *a, **k: 'office-selected-model')
    monkeypatch.setattr(module, 'load_agent_config', lambda *a: AgentConfig(name='pm', model='vision-profile', skills=[]))
    monkeypatch.setattr('deerflow.office.tool_contract.build_tool_catalog', build_tool_catalog)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **k: [view_image_tool])
    built = module.make_lead_agent({'context': {'agent_name': 'pm', 'user_id': 'alice'}})
    assert ('view_image' in {t.name for t in built['tools']}) == (vision and selected)


@pytest.mark.asyncio
async def test_resumed_guard_rejects_newly_enabled_tool(tmp_path, app):
    from types import SimpleNamespace
    from deerflow.office.tool_contract import restrict_to_tool_snapshot
    from deerflow.office.tool_guard import EmployeeToolGuard
    manifest = EmployeeToolManifest(1, BASE + ('memory_search',))
    save_tool_manifest(tmp_path, manifest, expected_revision='')
    app.memory.enabled = False
    original = employee_tool_contract(tmp_path, {}, app_config=app).snapshot()
    app.memory.enabled = True
    current = employee_tool_contract(tmp_path, {}, app_config=app)
    guard = EmployeeToolGuard(tmp_path, restrict_to_tool_snapshot(current, original))
    request = SimpleNamespace(tool_call={'name': 'memory_search', 'id': 'call'})
    denied = await guard.awrap_tool_call(request, lambda r: pytest.fail('new tool executed'))
    assert denied.status == 'error'
    request = SimpleNamespace(tools=[{'name': 'memory_search'}, {'name': 'read_file'}], override=lambda **k: k)
    assert guard.wrap_model_call(request, lambda r: r)['tools'] == [{'name': 'read_file'}]


@pytest.mark.parametrize('vision,selected', [(True, True), (False, True), (True, False)])
def test_delegated_factory_uses_own_model_and_snapshot(tmp_path, app, monkeypatch, vision, selected):
    from types import SimpleNamespace
    from deerflow.config.agents_config import AgentConfig
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents import executor as module
    from deerflow.tools.builtins.view_image_tool import view_image_tool
    from deerflow.office.tool_guard import EmployeeToolGuard
    app.models[0].supports_vision = vision
    save_tool_manifest(tmp_path, EmployeeToolManifest(1, BASE + (('view_image',) if selected else ())), expected_revision='')
    config = AgentConfig(name='frontend', model='vision-profile', skills=[])
    contract = employee_tool_contract(tmp_path, config.model_dump(), app_config=app, execution_kind='delegated')
    monkeypatch.setattr('deerflow.config.agents_config.load_agent_config', lambda *a, **k: config)
    monkeypatch.setattr('deerflow.config.agents_config.resolve_agent_dir', lambda *a, **k: tmp_path)
    monkeypatch.setattr(module, 'create_chat_model', lambda **k: k['name'])
    monkeypatch.setattr(module, 'create_agent', lambda **k: k)
    monkeypatch.setattr('deerflow.agents.middlewares.tool_error_handling_middleware.build_subagent_runtime_middlewares', lambda **k: [])
    executor = module.SubagentExecutor(SubagentConfig(name='frontend', description='frontend', model='vision-profile', skills=[]),
        tools=[view_image_tool], parent_model='pm-other-model', user_id='alice', app_config=app,
        office_context={'office_native_task_id': 'child', 'office_tool_contract': contract.snapshot()})
    built = executor._create_agent()
    assert built['model'] == 'vision-profile'
    guard = next(m for m in built['middleware'] if isinstance(m, EmployeeToolGuard))
    request = SimpleNamespace(tools=built['tools'], override=lambda **k: k)
    visible = guard.wrap_model_call(request, lambda r: r)['tools']
    assert ('view_image' in {t.name for t in visible}) == (vision and selected)
    request = SimpleNamespace(tool_call={'name': 'view_image', 'id': 'view'})
    result = guard.wrap_tool_call(request, lambda r: 'allowed')
    assert (result == 'allowed') == (vision and selected)


def test_deferred_mcp_requires_explicit_discovery_tool(app):
    from deerflow.office.tool_contract import validate_tool_discovery_selection
    key = 'mcp/server/search'
    catalog = build_tool_catalog(app)
    catalog[key] = ToolEntry(key, 'server_search', 'Search', source='mcp')
    manifest = EmployeeToolManifest(1, BASE + (key,))
    with pytest.raises(ValueError, match='tool_search'):
        validate_tool_discovery_selection(manifest, deferred_enabled=True)
    validate_tool_discovery_selection(manifest, deferred_enabled=False)
    result = resolve_employee_tools(manifest, catalog=catalog, authority=DEFAULT_AUTHORITY, deferred_enabled=True)
    assert key not in result.available_ids
    assert result.unavailable_reasons[key] == 'tool_search_required'
    complete = EmployeeToolManifest(1, (*manifest.tools, 'tool_search'))
    validate_tool_discovery_selection(complete, deferred_enabled=True)
    assert key in resolve_employee_tools(complete, catalog=catalog, authority=DEFAULT_AUTHORITY, deferred_enabled=True).available_ids


@pytest.mark.asyncio
async def test_save_can_remove_a_disappeared_registration(tmp_path):
    from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload
    from deerflow.office.tool_manifest import load_tool_manifest, manifest_revision
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    agent_dir = service.paths.user_agent_dir('default', 'frontend-a')
    old = load_tool_manifest(agent_dir)
    broken = EmployeeToolManifest(1, (*old.tools, 'removed_provider_tool'))
    revision = save_tool_manifest(agent_dir, broken, expected_revision=manifest_revision(old))
    result = await service.save_employee_tools('frontend-a', {'tools': list(old.tools), 'expectedRevision': revision})
    assert 'removed_provider_tool' not in result['tools']


@pytest.mark.asyncio
async def test_disabled_providers_and_deferred_discovery_through_http(tmp_path, app, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.gateway.routers.office import router, get_office_employee_service
    from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload
    from deerflow.office.tool_manifest import load_tool_manifest, manifest_revision
    monkeypatch.setattr('deerflow.config.get_app_config', lambda: app)
    app.tool_search.enabled = True
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    directory = service.paths.user_agent_dir('default', 'frontend-a')
    old = load_tool_manifest(directory)
    selected = EmployeeToolManifest(1, (*old.tools, 'invoke_acp_agent', 'mcp/offline/search'))
    save_tool_manifest(directory, selected, expected_revision=manifest_revision(old))
    http = FastAPI()
    http.include_router(router)
    http.dependency_overrides[get_office_employee_service] = lambda: service
    with TestClient(http) as client:
        url = router.prefix + '/employees/frontend-a/tools'
        response = client.get(url)
        assert response.status_code == 200
        state = response.json()
        assert state['unavailableTools']['invoke_acp_agent'] == 'acp_disabled'
        assert 'tool_search' in state['requiredTools']
        def save(names):
            return client.put(url, json={'tools': names, 'expectedRevision': state['revision']})
        assert save([*state['tools'], 'not_registered']).status_code == 400
        invalid = save([t for t in state['tools'] if t != 'tool_search'])
        assert invalid.status_code == 400 and 'tool_search' in invalid.text
        clean = [t for t in state['tools'] if t not in {'invoke_acp_agent', 'mcp/offline/search', 'tool_search'}]
        response = save(clean)
        assert response.status_code == 200, response.text
        assert response.json()['tools'] == sorted(clean)


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_resume_launch_keeps_original_snapshot_or_rejects_legacy(app, legacy):
    from types import SimpleNamespace
    from test_office_run_launch import EmployeeServiceStub, CheckpointerStub, CapturingStarter
    from deerflow.office.run_launch import OfficeRunLauncher
    from deerflow.runtime.runs.execution import RunExecutionSpecV1, RunLaunchContractV1
    manifest = EmployeeToolManifest(1, BASE + ('memory_search',))
    app.memory.enabled = False
    old = resolve_employee_tools(manifest, catalog=build_tool_catalog(app), authority=DEFAULT_AUTHORITY).snapshot()
    app.memory.enabled = True
    current = resolve_employee_tools(manifest, catalog=build_tool_catalog(app), authority=DEFAULT_AUTHORITY).snapshot()
    if legacy:
        old.pop('bindings')
    class Service(EmployeeServiceStub):
        def get_capability_index(self, employee_id, *, model_name=None):
            return {**super().get_capability_index(employee_id), 'toolContract': current}
    contract = RunLaunchContractV1.build(model_profile_id='profile-primary',
        execution_spec=RunExecutionSpecV1(version=1, mode='new', input={'messages': []}),
        config={}, context={}, trusted_context={'office_tool_contract': old},
        stream_modes=['values'], stream_subgraphs=False, interrupt_before=None, interrupt_after=None)
    source = SimpleNamespace(run_id='run-interrupted', thread_id='office__default__employee-one__private',
        assistant_id='employee-one', kwargs={'launch_contract': contract.to_dict()}, metadata={})
    launcher = OfficeRunLauncher(employee_service=Service(), checkpointer=CheckpointerStub())
    starter = CapturingStarter()
    async def resume():
        return await launcher.resume(source, {'version': 1, 'kind': 'human_input_response',
            'source': 'ask_clarification', 'request_id': 'clarification:call', 'response_kind': 'text', 'value': 'yes'},
            start_run=starter, request=object(), office_lease_id='lease', office_queue_id='queue')
    if legacy:
        with pytest.raises(RuntimeError, match='reliable bindings'):
            await resume()
        assert starter.calls == []
    else:
        await resume()
        assert starter.calls[0][0].context['office_tool_contract'] == old
        assert 'memory_search' not in old['runtime_names']


@pytest.mark.asyncio
async def test_delegated_memory_uses_employee_and_user_scope(app, monkeypatch):
    import json
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from deerflow.subagents.executor import SubagentExecutor, SubagentStatus
    from deerflow.subagents.config import SubagentConfig
    from deerflow.agents.memory import tools as memory
    facts = {}
    class Store:
        def get_memory(self, *, agent_name, user_id):
            return {'facts': facts.get((user_id, agent_name), [])}
        def create_fact(self, content, *, agent_name, user_id, **kwargs):
            facts.setdefault((user_id, agent_name), []).append({'id': content, 'content': content})
            return {}, content
        def search(self, query, *, agent_name, user_id, **kwargs):
            return facts.get((user_id, agent_name), [])
    monkeypatch.setattr(memory, 'get_memory_manager', lambda: Store())
    class Graph:
        async def astream(self, state, *, context, **kwargs):
            runtime = SimpleNamespace(context=context)
            content = context['user_id'] + ':' + context['agent_name']
            result = json.loads(memory.memory_add_tool.func(runtime=runtime, content=content))
            assert result['status'] == 'added'
            found = json.loads(memory.memory_search_tool.func(runtime=runtime, query=''))['results']
            assert [x['content'] for x in found] == [content]
            yield {'messages': [AIMessage(content='done')]}
    for user_id in ('alice', 'bob'):
        for employee in ('frontend', 'tester'):
            executor = SubagentExecutor(SubagentConfig(name=employee, description='test', model='vision-profile', skills=[]),
                tools=[], app_config=app, user_id=user_id, office_context={'office_native_task_id': 'task'})
            monkeypatch.setattr(executor, '_create_agent', lambda *args, **kwargs: Graph())
            result = await executor._aexecute('remember')
            assert result.status == SubagentStatus.COMPLETED, result.error
    assert set(facts) == {(u, a) for u in ('alice', 'bob') for a in ('frontend', 'tester')}

@pytest.mark.parametrize('can_write', [False, True])
def test_publication_follows_authorized_editing(app, can_write):
    contract = resolve_employee_tools(EmployeeToolManifest(1, BASE + (('apply_patch',) if can_write else ())),
                                      catalog=build_tool_catalog(app), authority=DEFAULT_AUTHORITY)
    assert ('publish_task_result' in contract.runtime_names) == can_write
    assert ('publish_task_result' in contract.available_ids) == can_write


@pytest.mark.parametrize('workspace', [None, {'workspace_id': 'explicit'}])
@pytest.mark.parametrize('can_write', [False, True])
def test_lead_assembly_keeps_authorized_delivery_in_either_directory(pm_factory, app, monkeypatch, workspace, can_write):
    from deerflow.config.agents_config import AgentConfig, resolve_agent_dir
    from deerflow.office.delivery_tool import publish_task_result
    from deerflow.sandbox.patch_tool import apply_patch_tool
    from deerflow.office.tool_manifest import load_tool_manifest, manifest_revision

    build, module = pm_factory
    build(authority=DEFAULT_AUTHORITY)
    directory = resolve_agent_dir('pm')
    save_tool_manifest(directory, EmployeeToolManifest(1, BASE + (('apply_patch',) if can_write else ())),
                       expected_revision=manifest_revision(load_tool_manifest(directory)))
    monkeypatch.setattr(module, 'get_app_config', lambda: app)
    monkeypatch.setattr(module, '_resolve_model_name', lambda *args, **kwargs: 'vision-profile')
    monkeypatch.setattr(module, 'load_agent_config', lambda *args: AgentConfig(name='pm', model='vision-profile', skills=[]))
    monkeypatch.setattr('deerflow.office.tool_contract.build_tool_catalog', build_tool_catalog)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **kwargs: [apply_patch_tool, publish_task_result])
    built = module.make_lead_agent({'context': {'agent_name': 'pm', 'user_id': 'alice', 'office_execution_workspace': workspace}})
    names = {tool.name for tool in built['tools']}
    assert ('publish_task_result' in names) == can_write
    assert 'powershell_run' not in names


@pytest.mark.asyncio
@pytest.mark.parametrize('workspace', [None, {'workspace_id': 'explicit'}])
async def test_delegated_initial_tools_keep_delivery_in_either_directory(app, workspace):
    from deerflow.office.delivery_tool import publish_task_result
    from deerflow.sandbox.patch_tool import apply_patch_tool
    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor

    executor = SubagentExecutor(SubagentConfig(name='frontend', description='frontend', skills=[]),
        tools=[apply_patch_tool, publish_task_result], app_config=app, user_id='alice',
        office_context={'office_native_task_id': 'child', 'office_execution_workspace': workspace})
    state, tools, _ = await executor._build_initial_state('Integrate existing work')
    assert 'publish_task_result' in {tool.name for tool in tools}
    assert 'target=' in str(state['messages'][0].content)
