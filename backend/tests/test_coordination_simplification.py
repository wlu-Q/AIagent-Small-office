from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware
from deerflow.config.tool_output_config import ToolOutputConfig
from deerflow.office import workspace_delivery as delivery
from deerflow.office.execution_workspace import _git, result_reference
from test_workspace_delivery import delivery_setup as delivery_setup
from test_execution_workspace import repository as repository


def test_task_reference_is_location_not_delivery_qualification(delivery_setup):
    service, work, allocate, project, _ = delivery_setup
    workspace_id = allocate()
    root = Path(service.prepare(workspace_id, work=work)['root_path'])
    (root / 'index.txt').write_text('written\n')
    delivered = delivery.publish(service, work, workspace_id, context={})
    (root / 'index.txt').write_text('not delivered yet\n')
    (root / 'new.txt').write_text('new untracked file\n')
    head = _git(root, 'rev-parse', 'HEAD')
    index_path = Path(_git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index').decode().strip())
    index = index_path.read_bytes()
    result = result_reference(service.get(workspace_id, work=work))
    assert 'result_commit' not in result
    assert delivered['result_commit'] == head.decode().strip()
    assert result['workspace_path'].endswith(f'{workspace_id}/workspace/')
    assert not {'integration_status', 'published_commit', 'has_unpublished_changes', 'changed_files'} & result.keys()
    assert 'integration_status' not in service.get(workspace_id, work=work)
    assert _git(root, 'rev-parse', 'HEAD') == head
    assert index_path.read_bytes() == index
    assert (project / 'index.txt').read_text() == 'written\n'


def test_worktree_guidance_covers_continued_delivery_and_actual_terminal_defaults():
    from deerflow.office.execution_workspace import workspace_guidance
    from deerflow.office.work_context import build_work_context, build_delegation_prompt

    guidance = workspace_guidance({'workspace_id': 'existing-workspace'})
    assert 'even if this task makes no new edits' in guidance
    assert 'Existing permissions and user approval still apply' in guidance
    assert 'Direct edits in the bound project need no writeback' in guidance
    assert 'User-facing installation and preview use the bound project' in guidance
    assert 'Default execution environment: the bound project' in workspace_guidance(None)
    context = build_work_context(user_id='alice', project_id='project', thread_id='thread', work_id='work', latest_user_message='继续收尾')
    prompt = build_delegation_prompt(task_prompt='继续收尾', shared_context=context, project_knowledge={})
    assert '普通命令默认使用本次任务目录' in prompt
    assert 'preview_service 默认使用绑定项目' in prompt
    assert 'PowerShell 工具省略 cwd 即使用本次执行目录' not in prompt


def test_model_workspace_facts_drop_old_head_but_keep_actual_writeback_commit():
    from deerflow.tools.builtins.task_tool import _task_result_command
    from deerflow.agents.middlewares.tool_output_budget_middleware import _retained_result_facts

    evidence = {'writebacks': {'total_operations': 1, 'operations': [{'status': 'written', 'result_commit': 'real-writeback'}]}}
    original = {'workspace_id': 'existing-workspace', 'result_commit': 'old-head', 'execution_evidence': evidence}
    message = _task_result_command(tool_call_id='call', status='completed', result='Written', workspace_result=original).update['messages'][0]
    assert 'old-head' not in message.content
    assert 'real-writeback' in message.content
    facts = _retained_result_facts(message)
    assert 'result_commit' not in facts['workspace']
    assert facts['execution_evidence'] == evidence
    assert original['result_commit'] == 'old-head'


def history(status='success'):
    old = AIMessage(content='editing', id='old-message', tool_calls=[
        {'id': 'old-call', 'name': 'apply_patch', 'args': {'patch': 'a' * 2000}},
    ])
    return [HumanMessage(content='Preserve my requirements'), old,
            ToolMessage(content='Applied index.txt', tool_call_id='old-call', status=status),
            AIMessage(content='checking', tool_calls=[{'id': 'recent-call', 'name': 'apply_patch', 'args': {'patch': 'b' * 2000}}]),
            ToolMessage(content='Applied other.txt', tool_call_id='recent-call')]


@pytest.mark.anyio
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('status', ['success', 'error'])
async def test_model_entrypoints_preserve_completed_input_prefix(tmp_path, asynchronous, status):
    messages = history(status)
    middleware = ToolOutputBudgetMiddleware(ToolOutputConfig(externalize_min_chars=1000, preview_head_chars=100))
    snapshots = []
    def record(request):
        snapshots.append([message.model_dump() for message in request.messages])
        return []
    async def async_record(request):
        return record(request)
    for size in [3, len(messages)]:
        request = ModelRequest(model=None, messages=messages[:size], tools=[],
                               state={'thread_data': {'outputs_path': str(tmp_path)}})
        if asynchronous:
            await middleware.awrap_model_call(request, async_record)
        else:
            middleware.wrap_model_call(request, record)
    assert snapshots[0] == snapshots[1][:3]
    assert snapshots[1][1]['tool_calls'][0]['args']['patch'] == 'a' * 2000
    assert not (tmp_path / '.tool-results').exists()


def test_missing_required_tool_rejects_before_admission(monkeypatch):
    from test_task_tool_core_logic import _make_runtime, _run_task_tool, _task_tool_message, _test_tool_contract, task_tool_module

    monkeypatch.setattr('deerflow.office.delegation.lookup_delegation', lambda *args, **kwargs: None)
    package = SimpleNamespace(tool_contract=_test_tool_contract('apply_patch'))
    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *args: package)
    gate = MagicMock(side_effect=AssertionError('Must not reserve an employee'))
    monkeypatch.setattr(task_tool_module, '_office_employee_gate', gate)
    result = _run_task_tool(runtime=_make_runtime(metadata={'office_employee_id': 'pm'}),
                            description='Build frontend', prompt='Build the site', subagent_type='front',
                            tool_call_id='missing-terminal', required_tools=['apply_patch', 'powershell_run'])
    assert 'lacks required tools: powershell_run' in _task_tool_message(result).content
    gate.assert_not_called()


@pytest.mark.parametrize('name', ['file:write', 'file:read', 'web', 'powershell_terminal', '编辑文件', 'write_file'])
def test_invalid_required_tool_is_not_employee_capability_gap(monkeypatch, name):
    from test_task_tool_core_logic import _make_runtime, _run_task_tool, _task_tool_message, _test_tool_contract, task_tool_module

    monkeypatch.setattr('deerflow.office.delegation.lookup_delegation', lambda *args, **kwargs: None)
    package = SimpleNamespace(tool_contract=_test_tool_contract('read_file', 'ls', 'grep', 'apply_patch'))
    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *args: package)
    gate = MagicMock(side_effect=AssertionError('Must not reserve an employee'))
    monkeypatch.setattr(task_tool_module, '_office_employee_gate', gate)
    result = _run_task_tool(runtime=_make_runtime(metadata={'office_employee_id': 'pm'}),
                           description='Write product overview', prompt='Write PRODUCT_OVERVIEW.md', subagent_type='document-a',
                           tool_call_id='invalid-tool', required_tools=['read_file', 'ls', 'grep', name])
    message = _task_tool_message(result)
    assert f'Invalid required_tools: {name}' in message.content
    assert 'lacks required tools' not in message.content
    assert 'Report the capability gap' not in message.content
    assert 'Correct the arguments' in message.content
    gate.assert_not_called()
