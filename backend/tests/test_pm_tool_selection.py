from types import SimpleNamespace

import pytest

from deerflow.office.authority import DELEGATING_AUTHORITY
from deerflow.office.tool_contract import ToolEntry, required_tool_names, resolve_employee_tools
from deerflow.office.tool_guard import EmployeeToolGuard
from deerflow.office.tool_manifest import EmployeeToolManifest, save_tool_manifest


@pytest.mark.parametrize('selected', [False, True])
def test_pm_tools_follow_manifest_not_role_blacklist(tmp_path, selected):
    base = required_tool_names(DELEGATING_AUTHORITY)
    optional = {'write_todos', 'apply_patch', 'powershell_run', 'publish_task_result'}
    names = base | (optional if selected else set())
    manifest = EmployeeToolManifest(1, tuple(sorted(names)))
    save_tool_manifest(tmp_path, manifest, expected_revision='')
    catalog = {name: ToolEntry(name, name, name) for name in base | optional}
    contract = resolve_employee_tools(manifest, catalog=catalog, authority=DELEGATING_AUTHORITY)
    assert set(contract.runtime_names) == names
    guard = EmployeeToolGuard(tmp_path, contract)
    request = SimpleNamespace(tools=[{'name': name} for name in base | optional])
    request.override = lambda **kwargs: kwargs
    assert {tool['name'] for tool in guard.wrap_model_call(request, lambda value: value)['tools']} == names
    for name in optional:
        result = guard.wrap_tool_call(SimpleNamespace(tool_call={'name': name, 'id': name}), lambda value: 'allowed')
        if selected:
            assert result == 'allowed'
        else:
            assert result.status == 'error'


@pytest.mark.parametrize('office_pm,selected,expected', [(True, True, True), (True, False, False), (False, False, True)])
def test_todo_middleware_obeys_selection_without_changing_generic_plan_mode(office_pm, selected, expected):
    from deerflow.agents.lead_agent.agent import build_middlewares
    from deerflow.agents.middlewares.todo_middleware import OFFICE_TODO_TOOL_DESCRIPTION
    middlewares = build_middlewares({'context': {'is_plan_mode': True}}, model_name=None,
                                    office_pm=office_pm, office_todos_enabled=selected)
    todos = [item for item in middlewares if type(item).__name__ == 'TodoMiddleware']
    assert bool(todos) == expected
    if office_pm and selected:
        assert not todos[0]._enforce_completion
        assert todos[0].tools[0].description == OFFICE_TODO_TOOL_DESCRIPTION
