import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware, _patch_model_inputs
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


def test_history_inputs_are_references_only_in_model_view(tmp_path):
    messages = history()
    config = ToolOutputConfig(externalize_min_chars=1000, preview_head_chars=100)
    state = {'thread_data': {'outputs_path': str(tmp_path), 'office_workspace_id': 'workspace-a'}}
    view = _patch_model_inputs(messages, config, state)
    assert view[0] is messages[0]
    assert view[2] is messages[2]
    assert view[3] is messages[3]
    assert view[1].id == messages[1].id
    assert view[1].tool_calls[0]['id'] == 'old-call'
    assert len(messages[1].tool_calls[0]['args']['patch']) == 2000
    assert '/mnt/office-workspaces/workspace-a/outputs/.tool-results/' in view[1].tool_calls[0]['args']['patch']
    files = list((tmp_path / '.tool-results').iterdir())
    assert len(files) == 1
    assert json.loads(files[0].read_text()) == messages[1].tool_calls[0]['args']
    assert _patch_model_inputs(messages, config, state) == view
    assert len(list((tmp_path / '.tool-results').iterdir())) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('asynchronous', [False, True])
async def test_model_entrypoints_externalize_completed_inputs(tmp_path, asynchronous):
    messages = history()
    config = ToolOutputConfig(externalize_min_chars=1000, preview_head_chars=100)
    middleware = ToolOutputBudgetMiddleware(config)
    request = ModelRequest(model=None, messages=messages, tools=[], state={'thread_data': {'outputs_path': str(tmp_path)}})

    def check(forwarded):
        assert forwarded is not request
        assert 'Archived completed tool input' in forwarded.messages[1].tool_calls[0]['args']['patch']
        assert forwarded.messages[1].tool_calls[0]['id'] == 'old-call'
        assert forwarded.messages[2].tool_call_id == 'old-call'
        assert forwarded.messages[3] is messages[3]
        assert len(messages[1].tool_calls[0]['args']['patch']) == 2000
        assert len(list((tmp_path / '.tool-results').iterdir())) == 1
        return []

    async def async_handler(forwarded):
        return check(forwarded)

    if asynchronous:
        await middleware.awrap_model_call(request, async_handler)
    else:
        middleware.wrap_model_call(request, check)


def test_old_failure_input_can_be_read_by_reference_without_hiding_error(tmp_path):
    messages = history('error')
    messages[-1].status = 'error'
    config = ToolOutputConfig(externalize_min_chars=1000, preview_head_chars=100)
    view = _patch_model_inputs(messages, config, {'thread_data': {'outputs_path': str(tmp_path)}})
    assert 'Archived completed tool input' in view[1].tool_calls[0]['args']['patch']
    assert view[2] is messages[2]
    assert view[2].status == 'error'
    assert view[-2] is messages[-2]
    assert view[-1] is messages[-1]
    assert len(view[-2].tool_calls[0]['args']['patch']) == 2000


@pytest.mark.parametrize('condition', ['pending', 'no_storage', 'disabled', 'write_failed', 'signed_thinking'])
def test_history_inputs_keep_ineligible_calls(tmp_path, monkeypatch, condition):
    messages = history()
    config = ToolOutputConfig(externalize_min_chars=0 if condition == 'disabled' else 1000)
    state = {'thread_data': {'outputs_path': str(tmp_path)}}
    if condition == 'pending':
        messages.pop(2)
    if condition == 'no_storage':
        state = {}
    if condition == 'write_failed':
        monkeypatch.setattr('deerflow.agents.middlewares.tool_output_budget_middleware._externalize', lambda *args, **kwargs: None)
    if condition == 'signed_thinking':
        messages[1].content = [{'type': 'thinking', 'thinking': 'private', 'signature': 'signature'}]
    assert _patch_model_inputs(messages, config, state) == messages


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
