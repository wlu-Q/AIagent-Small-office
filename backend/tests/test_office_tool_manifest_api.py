import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload, employee_app_config


@pytest.mark.asyncio
async def test_tool_choices_are_named_and_delivery_is_derived(tmp_path):
    from deerflow.office.tool_manifest import EmployeeToolManifest, save_tool_manifest, manifest_revision, load_tool_manifest
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    directory = service.paths.user_agent_dir('default', 'frontend-a')
    old = load_tool_manifest(directory)
    # Existing employees can lack the newly hidden implementation tool.
    save_tool_manifest(directory, EmployeeToolManifest(1, tuple(t for t in old.tools if t != 'publish_task_result')),
                       expected_revision=manifest_revision(old))
    state = service.get_employee_tools('frontend-a')
    choices = {t['id']: t for t in state['choices']}
    assert choices['read_file']['required'] and choices['read_file']['label'] == '读取文件'
    assert choices['apply_patch']['label'] == '编辑文件'
    assert choices['web_search']['label'] == '网络搜索'
    assert choices['powershell_terminal']['label'] == '终端控制'
    assert choices['search_office_library']['label'] == '办公室资料库'
    assert set(choices) == {'read_file', 'apply_patch', 'web_search', 'search_office_library', 'powershell_terminal'}
    assert set(choices['read_file']['members']) == {'read_file', 'ls', 'glob', 'grep'}
    assert set(choices['web_search']['members']) == {'web_search', 'web_fetch', 'image_search'}
    assert 'publish_task_result' in state['availableTools']
    state = await service.save_employee_tools('frontend-a', {'tools': [t for t in state['tools'] if t != 'apply_patch'] + ['powershell_terminal'], 'expectedRevision': state['revision']})
    assert 'publish_task_result' in state['availableTools']
    state = await service.save_employee_tools('frontend-a', {'tools': [t for t in state['tools'] if t != 'powershell_terminal'], 'expectedRevision': state['revision']})
    assert 'publish_task_result' not in state['availableTools']
    await service.ensure_builtin_pm(default_model_profile_id='profile-primary')
    pm_choices = {t['id'] for t in service.get_employee_tools('pm')['choices']}
    assert {'powershell_terminal', 'apply_patch', 'write_todos'} <= pm_choices
    assert 'publish_task_result' not in pm_choices
    pm_state = service.get_employee_tools('pm')
    todo = next(choice for choice in pm_state['choices'] if choice['id'] == 'write_todos')
    assert not todo.get('disabled') and not todo['required']
    assert 'write_todos' not in pm_state['tools']
    assert 'write_todos' not in pm_state['availableTools']
    assert 'capture_user_message' in pm_state['availableTools']
    enabled = await service.save_employee_tools('pm', {'tools': [*pm_state['tools'], 'write_todos', 'apply_patch'], 'expectedRevision': pm_state['revision']})
    assert {'write_todos', 'apply_patch', 'publish_task_result'} <= set(enabled['availableTools'])
    assert 'powershell_run' not in enabled['availableTools']
    disabled = await service.save_employee_tools('pm', {'tools': pm_state['tools'], 'expectedRevision': enabled['revision']})
    assert not {'write_todos', 'apply_patch', 'publish_task_result'} & set(disabled['availableTools'])


@pytest.mark.asyncio
async def test_group_members_save_without_removing_hidden_tools(tmp_path):
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    state = service.get_employee_tools('frontend-a')
    groups = {choice['id']: choice for choice in state['choices']}
    reading = set(groups['read_file']['members'])
    web = set(groups['web_search']['members'])
    hidden = set(state['tools']) - {name for group in groups.values() for name in group['members']}
    assert {'report_to_pm'} <= hidden
    # A group toggle submits real IDs. Required reading members are restored on save.
    state = await service.save_employee_tools('frontend-a', {
        'tools': sorted(set(state['tools']) - web - (reading - {'read_file'})),
        'expectedRevision': state['revision'],
    })
    assert not web.intersection(state['availableTools'])
    assert reading <= set(state['tools'])
    assert hidden <= set(state['tools'])
    state = await service.save_employee_tools('frontend-a', {
        'tools': sorted(set(state['tools']) | web), 'expectedRevision': state['revision'],
    })
    assert web <= set(state['availableTools'])
    assert hidden <= set(state['tools'])


def test_group_choices_do_not_bypass_authority():
    from deerflow.office.authority import normalize_authority
    from deerflow.office.tool_contract import ToolEntry, employee_tool_choices
    catalog = {name: ToolEntry(name, name, name, permissions=('restricted',) if name in {'grep', 'web_fetch'} else ())
               for name in ('read_file', 'grep', 'web_search', 'web_fetch')}
    authority = normalize_authority({'denied_permissions': ['restricted']})
    groups = {choice['id']: choice for choice in employee_tool_choices(catalog, authority)}
    assert groups['read_file']['members'] == ['read_file']
    assert groups['web_search']['members'] == ['web_search']


@pytest.mark.asyncio
async def test_tools_save_persists_and_changes_routing_and_delegation(tmp_path, monkeypatch):
    from deerflow.config import agents_config
    from deerflow.office.packages import resolve_employee_subagent_package
    from deerflow.office.capability_registry import EmployeeCapabilityRegistry
    service, _, _ = build_employee_service(tmp_path)
    monkeypatch.setattr(agents_config, 'get_paths', lambda: service.paths)
    await service.create_employee(frontend_employee_payload())
    before = service.get_employee_tools('frontend-a')
    assert 'apply_patch' in before['tools']
    after = await service.save_employee_tools('frontend-a', {
        'tools': [t for t in before['tools'] if t != 'apply_patch'], 'expectedRevision': before['revision']})
    assert 'apply_patch' not in after['availableTools']
    assert 'publish_task_result' not in after['availableTools']
    assert 'apply_patch' not in (await EmployeeCapabilityRegistry(service).get('frontend-a'))['tools']
    package = resolve_employee_subagent_package('frontend-a', 'default', employee_app_config())
    assert 'apply_patch' not in package.config.tools
    assert 'report_to_pm' in package.config.tools
    assert 'ask_clarification' not in package.config.tools
    with pytest.raises(RuntimeError, match='revision'):
        await service.save_employee_tools('frontend-a', {'tools': before['tools'], 'expectedRevision': before['revision']})
    with pytest.raises(ValueError, match='required'):
        await service.save_employee_tools('frontend-a', {'tools': [], 'expectedRevision': after['revision']})


@pytest.mark.asyncio
async def test_tools_api_and_busy_conflict(tmp_path):
    from app.gateway.routers.office import router, get_office_employee_service
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_office_employee_service] = lambda: service
    with TestClient(app) as client:
        url = '/api/office/employees/frontend-a/tools'
        # Router's prefix is authoritative rather than a separately maintained test constant.
        url = router.prefix + '/employees/frontend-a/tools'
        response = client.get(url)
        assert response.status_code == 200
        current = response.json()
        response = client.put(url, json={'tools': current['tools'], 'expectedRevision': 'stale'})
        assert response.status_code == 409
        response = client.put(url, json={'tools': [*current['tools'], 'task'], 'expectedRevision': current['revision']})
        assert response.status_code == 400
        service._write_projection('frontend-a', {'state': 'working'})
        response = client.put(url, json={'tools': current['tools'], 'expectedRevision': current['revision']})
        assert response.status_code == 409


def test_guard_blocks_dynamic_tool_and_manifest_drift(tmp_path):
    from types import SimpleNamespace
    from deerflow.office.tool_guard import EmployeeToolGuard
    from deerflow.office.tool_manifest import EmployeeToolManifest, save_tool_manifest
    manifest = EmployeeToolManifest(1, ('read_file',))
    revision = save_tool_manifest(tmp_path, manifest, expected_revision='')
    guard = EmployeeToolGuard(tmp_path, SimpleNamespace(revision=revision, runtime_names=('read_file',)))
    request = SimpleNamespace(tool_call={'name': 'write_todos', 'id': 'call-1'})
    assert guard.wrap_tool_call(request, lambda r: pytest.fail('unauthorized execution')).status == 'error'
    save_tool_manifest(tmp_path, EmployeeToolManifest(1, ('apply_patch',)), expected_revision=revision)
    with pytest.raises(RuntimeError, match='changed'):
        guard.wrap_tool_call(request, lambda r: None)
