import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from deerflow.office.authority import DELEGATING_AUTHORITY
from deerflow.office.tool_contract import (
    TERMINAL_QUERIES, build_tool_catalog, default_tool_manifest, employee_tool_choices,
    required_tool_names, resolve_employee_tools, with_delivery_capability,
)
from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, save_tool_manifest


@pytest.mark.parametrize('office_managed', [True, False])
@pytest.mark.parametrize('delegated', [True, False])
def test_office_omits_loop_heuristic_but_keeps_budgets(office_managed, delegated):
    from deerflow.agents.lead_agent.agent import build_middlewares
    from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
    from test_office_employee_lifecycle import employee_app_config

    config = employee_app_config()
    config.loop_detection.enabled = True
    config.token_budget.enabled = True
    config.subagents.token_budget.enabled = True
    if delegated:
        chain = build_subagent_runtime_middlewares(app_config=config, office_managed=office_managed, max_model_calls=50)
    else:
        chain = build_middlewares({'context': {}}, None, app_config=config, office_managed=office_managed)
    names = {type(middleware).__name__ for middleware in chain}
    assert ('LoopDetectionMiddleware' in names) == (not office_managed)
    assert 'TokenBudgetMiddleware' in names
    if delegated:
        assert 'ModelCallLimitMiddleware' in names
    order = [type(middleware).__name__ for middleware in chain]
    for stop in ('ModelCallLimitMiddleware', 'TokenBudgetMiddleware', 'SafetyFinishReasonMiddleware'):
        if stop in names:
            assert order.index('ToolErrorHandlingMiddleware') < order.index(stop)


def test_pm_queries_are_selected_without_execute_permission_or_publication():
    catalog = build_tool_catalog()
    authority = replace(DELEGATING_AUTHORITY, denied_permissions=('process.execute',))
    manifest = default_tool_manifest([], authority)
    assert set(TERMINAL_QUERIES) <= set(manifest.tools)
    contract = resolve_employee_tools(manifest, catalog=catalog, authority=authority)
    assert set(TERMINAL_QUERIES) <= set(contract.runtime_names)
    assert set(contract.runtime_names).isdisjoint({'powershell_run', 'powershell_stop', 'publish_task_result'})
    assert 'publish_task_result' not in with_delivery_capability(TERMINAL_QUERIES)
    choices = employee_tool_choices(catalog, authority)
    readonly = next(choice for choice in choices if choice['id'] == 'powershell_readonly')
    assert readonly['label'] == '终端查询（只读）'
    assert set(readonly['members']) == set(TERMINAL_QUERIES)
    for name in ('powershell_run', 'powershell_stop'):
        with pytest.raises(ValueError, match='process.execute'):
            resolve_employee_tools(EmployeeToolManifest(1, tuple(sorted(set(manifest.tools) | {name}))), catalog=catalog, authority=authority)
    unselected = EmployeeToolManifest(1, tuple(sorted(required_tool_names(authority))))
    assert set(TERMINAL_QUERIES).isdisjoint(resolve_employee_tools(unselected, catalog=catalog, authority=authority).runtime_names)


def test_query_migration_only_updates_pm_and_keeps_original_choices(tmp_path):
    path = Path(__file__).parents[1] / 'scripts/grant_pm_terminal_queries.py'
    spec = importlib.util.spec_from_file_location('grant_pm_terminal_queries', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, delegate in [('pm', True), ('front', False)]:
        directory = tmp_path / 'users/alice/agents' / name
        directory.mkdir(parents=True)
        (directory / 'config.yaml').write_text(yaml.safe_dump({'runtime_contract': {'authority': {'can_delegate': delegate}}}), encoding='utf-8')
        save_tool_manifest(directory, EmployeeToolManifest(1, ('read_file',)), expected_revision='')
    assert len(module.migrate(tmp_path)) == 1
    pm = tmp_path / 'users/alice/agents/pm'
    assert load_tool_manifest(pm).tools == ('read_file',)
    assert len(module.migrate(tmp_path, apply=True)) == 1
    assert set(load_tool_manifest(pm).tools) == {'read_file', *TERMINAL_QUERIES}
    assert load_tool_manifest(pm.parent / 'front').tools == ('read_file',)
    assert (pm / 'tools.before-terminal-queries.yaml').is_file()
    assert module.migrate(tmp_path, apply=True) == []


def test_preview_description_requires_reported_port():
    from deerflow.sandbox.powershell_tools import powershell_run
    description = ' '.join(powershell_run.description.split())
    assert 'the client never receives an allocated port' in description
    assert 'Keep a working service rather than restarting it' in description
    assert 'source dev server reflects edits' in description
    assert 'build preview needs a rebuild after edits' in description
    assert 'HTTP 200 alone does not prove page functionality or freshness' in description
    assert 'Report the exact URL tested' in description
    assert 'HTTP-only check confirms accessibility, not browser rendering or interactions' in description
    assert "Untested URLs must not inherit another URL's verification" in description


def test_java_guidance_distinguishes_arguments_and_environment_notice():
    from deerflow.sandbox.powershell_tools import powershell_run
    description = ' '.join(powershell_run.description.split())
    assert "java '-Dfile.encoding=UTF-8' Main" in description
    assert '"Picked up JAVA_TOOL_OPTIONS" is informational, not a failure' in description
    assert 'Diagnose the actual error before changing those options' in description
