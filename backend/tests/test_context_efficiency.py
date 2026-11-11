"""Context optimization must preserve execution truth, ownership and history."""
import json
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware
from deerflow.config.paths import Paths
from deerflow.config.tool_output_config import ToolOutputConfig
from deerflow.office.execution_facts import WorkIdentity
from deerflow.office.execution_workspace import ExecutionWorkspaceService
from deerflow.runtime.context_metrics import measure_context
from deerflow.tools.builtins.task_tool import _task_result_command


def test_metrics_are_content_free_do_not_mutate_and_track_actual_prefix():
    messages = [SystemMessage(content='private system'),
                HumanMessage(content='secret memory', additional_kwargs={'hide_from_ui': True, 'dynamic_context_kind': 'memory'}),
                HumanMessage(content='first request', id='same'),
                AIMessage(content='', tool_calls=[{'name': 'read_file', 'id': 'c1', 'args': {'path': 'private'}}]),
                ToolMessage(content='a' * 12000, tool_call_id='c1', name='read_file')]
    before = [m.model_dump() for m in messages]
    params = {'tools': [{'type': 'function', 'function': {'name': 'read_file', 'description': 'private schema'}}]}
    metrics, previous = measure_context([messages], params)
    assert metrics['sources']['memory']['chars'] == len('secret memory')
    assert metrics['sources']['tool']['chars'] == 12000
    assert metrics['tool_schema_chars'] > 0
    assert metrics['unchanged_prefix_messages'] is None
    assert 'private' not in json.dumps(metrics) and 'secret' not in json.dumps(metrics)
    extended = messages + [HumanMessage(content='same request legitimately repeated', id='same')]
    second, _ = measure_context([extended], params, previous)
    assert second['unchanged_prefix_messages'] == 5
    assert second['same_tool_schema'] is True
    assert second['duplicate_message_ids'] == 1
    assert [m.model_dump() for m in messages] == before
    changed, _ = measure_context([[SystemMessage(content='new rules'), *messages[1:]]], {}, previous)
    assert changed['unchanged_prefix_messages'] == 0
    assert changed['same_tool_schema'] is None


@pytest.mark.parametrize('status,stop_reason', [('completed', None), ('failed', 'token_capped'), ('cancelled', None), ('timed_out', None)])
def test_large_task_report_keeps_facts_and_reference_survives_restart(tmp_path, status, stop_reason):
    paths = Paths(str(tmp_path / 'data'))
    project = tmp_path / 'project'
    project.mkdir()
    work = WorkIdentity('alice', 'project', 'thread', 'work')
    svc = ExecutionWorkspaceService(paths=paths, user_id='alice')
    workspace = svc.allocate(work, employee_id='worker', owner_key='task', project_path=project)
    wid = workspace['workspace_id']
    outputs = svc.task_directory(svc.get(wid)) / 'outputs'
    workspace['execution_evidence'] = {'total': 2, 'failed': 1, 'details': [{'execution_id': 'exec-fail', 'exit_code': 2}]}
    report = 'worker report\n' + 'long details\n' * 3000 + 'not independently accepted'
    command = _task_result_command(tool_call_id='task-call', status=status, result=report,
                                   error='failed validation' if status != 'completed' else None,
                                   stop_reason=stop_reason, workspace_result=workspace)
    original = command.update['messages'][0]
    mw = ToolOutputBudgetMiddleware(ToolOutputConfig())
    request = SimpleNamespace(runtime=SimpleNamespace(state={'thread_data': {
        'outputs_path': str(outputs), 'office_workspace_id': wid}}))
    patched = mw.wrap_tool_call(request, lambda _: command)
    message = patched.update['messages'][0]
    assert message.tool_call_id == 'task-call'
    assert message.additional_kwargs == original.additional_kwargs
    assert f'"subagent_status": "{status}"' in message.content
    assert 'exec-fail' in message.content and '"exit_code": 2' in message.content
    assert len(message.content) < 9000
    from langchain_openai.chat_models.base import _convert_message_to_dict
    wire = _convert_message_to_dict(message)
    assert wire['content'] == message.content
    assert 'additional_kwargs' not in wire and 'artifact' not in wire
    assert report not in json.dumps(wire, ensure_ascii=False)
    assert 'Always edit using' not in original.content
    reference = re.search(r'saved to (\S+) \(', message.content).group(1)
    assert reference.startswith(f'/mnt/office-workspaces/{wid}/outputs/.tool-results/')
    recovered = ExecutionWorkspaceService(paths=paths, user_id='alice')
    assert recovered.resolve_reference(reference, work=work).read_text(encoding='utf-8') == original.content
    assert recovered.resolve_reference(reference, work=WorkIdentity('alice', 'project', 'thread', 'other-work')).is_file()
    with pytest.raises(ValueError):
        recovered.resolve_reference(reference, work=WorkIdentity('alice', 'other-project', 'thread', 'work'))
    with pytest.raises(ValueError):
        ExecutionWorkspaceService(paths=paths, user_id='bob').resolve_reference(reference, work=WorkIdentity('bob', 'project', 'thread', 'work'))
    assert [path.name for path in project.iterdir()] == ['.office-worktrees']
    assert not (outputs.parent / 'tree').exists()
    assert not (project / '.git').exists()


def test_failed_externalization_keeps_unknown_and_no_fake_reference(monkeypatch):
    from deerflow.agents.middlewares import tool_output_budget_middleware as module
    monkeypatch.setattr(module, '_externalize', lambda *a, **kw: None)
    mw = ToolOutputBudgetMiddleware(ToolOutputConfig())
    msg = ToolMessage(content='x' * 60000, tool_call_id='read', name='powershell_read',
                      artifact={'terminal_execution': {'execution_id': 'e1', 'status': 'unknown'}})
    request = SimpleNamespace(runtime=SimpleNamespace(state={'thread_data': {'outputs_path': 'unused'}}))
    result = mw.wrap_tool_call(request, lambda _: msg)
    assert '"status": "unknown"' in result.content
    assert 'Persistent storage unavailable' in result.content
    assert 'saved to' not in result.content
    assert result.artifact == msg.artifact


def test_preview_does_not_expand_a_long_last_line(tmp_path):
    # A backward newline snap used to re-include nearly the entire long line.
    content = 'header\n' + 'line\n' * 12000 + 'x' * 40000
    msg = ToolMessage(content=content, name='web_fetch', tool_call_id='long-line')
    request = SimpleNamespace(runtime=SimpleNamespace(state={'thread_data': {'outputs_path': str(tmp_path)}}))
    result = ToolOutputBudgetMiddleware().wrap_tool_call(request, lambda _: msg)
    assert len(result.content) < 4000
    assert next((tmp_path / '.tool-results').iterdir()).read_text(encoding='utf-8') == content


def test_generic_error_is_visible_on_the_model_wire(tmp_path):
    msg = ToolMessage(content='details\n' * 4000, name='web_fetch', tool_call_id='failed', status='error')
    request = SimpleNamespace(runtime=SimpleNamespace(state={'thread_data': {'outputs_path': str(tmp_path)}}))
    result = ToolOutputBudgetMiddleware().wrap_tool_call(request, lambda _: msg)
    assert result.status == 'error'
    assert '"tool_status": "error"' in result.content
