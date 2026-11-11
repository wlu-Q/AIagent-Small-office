import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.memory.backends.deermem.deer_mem import DeerMem
from deerflow.agents.memory.backends.deermem.deermem.core.message_processing import detect_correction
from deerflow.agents.memory.backends.deermem.deermem.core.prompt import format_conversation_for_update
from deerflow.office.delegation_memory import delegation_observations, remember_delegation
from deerflow.office.execution_facts import DelegationIdentity, DelegationReceipt, WorkIdentity
from deerflow.config.memory_config import MemoryConfig


def test_recent_receipts_are_immediate_bounded_and_project_employee_scoped(tmp_path, monkeypatch):
    from deerflow.office.execution_facts import ExecutionReceiptStore
    from deerflow.office.delegation_memory import recent_delegation_context

    paths = SimpleNamespace(user_dir=lambda user_id: tmp_path / user_id)
    monkeypatch.setattr('deerflow.config.paths.get_paths', lambda: paths)
    store = ExecutionReceiptStore(paths=paths, user_id='alice')
    for index, (employee, project) in enumerate([('front', 'p')] * 5 + [('other', 'p'), ('front', 'other')]):
        identity = DelegationIdentity(WorkIdentity('alice', project, 'thread', 'work'), 'ai', f'call-{index}')
        store.claim(identity, employee_id=employee, request_hash='h', parent_run_id='run', input_ref={'task': 'create a page' * 100})
        store.observe(identity, parent_run_id='run', native_task_id=f'task-{index}', status='succeeded',
            result_ref={'workspace_id': f'space-{index}', 'integration_status': 'unpublished', 'changed_files': ['a.vue'], 'report': 'SECRET_REPORT'}, evidence=[])
    receipts = store.recent_employee_receipts(employee_id='front', project_id='p')
    assert len(receipts) == 3
    assert all(receipt.employee_id == 'front' for receipt in receipts)
    assert all(receipt.result_ref['workspace_id'] not in {'space-5', 'space-6'} for receipt in receipts)
    context = {'office_work': WorkIdentity('alice', 'p', 'private', 'next-work').to_record(), 'office_employee_id': 'front'}
    text = recent_delegation_context(context, employee_id='front', user_id='alice')
    assert 'space-4' in text and 'SECRET_REPORT' not in text
    assert 'unpublished' not in text and 'a.vue' not in text
    assert 'integration_status' not in text
    assert len(text) < 2400
    del context['office_employee_id']
    context['agent_name'] = 'front'
    assert recent_delegation_context(context, employee_id='front', user_id='alice') == text
    with pytest.raises(ValueError, match='ownership'):
        recent_delegation_context(context, employee_id='other', user_id='alice')


def receipt_for(identity, *, status='succeeded', employee='front'):
    return DelegationReceipt(identity.key, employee, 'hash', 'parent-run', identity.tool_call_id,
                             {'task': '修改大学官网首页按钮，文件 index.html'}, status,
                             {'report': '已完成首页修改'}, [
        {'type': 'ai', 'tool_calls': [{'id': 'write', 'name': 'apply_patch',
                                     'args': {'path': 'index.html', 'new_str': 'private file contents'}}]},
        {'type': 'tool', 'name': 'apply_patch', 'tool_call_id': 'write', 'status': 'success', 'content': 'OK'},
    ])


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'interrupted', 'timeout', 'unknown'])
def test_outcome_and_ownership_come_from_receipt_not_report(monkeypatch, status):
    manager = MagicMock()
    monkeypatch.setattr('deerflow.office.delegation_memory.get_memory_manager', lambda: manager)
    identity = DelegationIdentity(WorkIdentity('alice', 'campus', 'pm-thread', 'work'), 'ai', 'call')
    remember_delegation(identity=identity, receipt=receipt_for(identity, status=status),
                        memory_config=MemoryConfig(enabled=True), trace_id='trace')
    call = manager.add.call_args
    assert call.kwargs == {'agent_name': 'front', 'user_id': 'alice', 'trace_id': 'trace'}
    record = json.loads(call.args[1][0].content)
    assert record['execution_status'] == status
    assert record['project_id'] == 'campus'
    assert record['tool_observations'] == [{'tool': 'apply_patch', 'status': 'success', 'paths': {'path': 'index.html'}, 'tool_call_id': 'write'}]
    formatted = format_conversation_for_update(call.args[1])
    assert 'PM delegation record (not user speech)' in formatted
    assert 'User:' not in formatted
    assert 'private file contents' not in formatted


def test_memory_projection_omits_old_head_without_changing_writeback_evidence(monkeypatch):
    manager = MagicMock()
    monkeypatch.setattr('deerflow.office.delegation_memory.get_memory_manager', lambda: manager)
    identity = DelegationIdentity(WorkIdentity('alice', 'campus', 'pm-thread', 'work'), 'ai', 'call')
    evidence = {'writebacks': {'total_operations': 1, 'operations': [{'result_commit': 'written-commit', 'status': 'written'}]}}
    receipt = replace(receipt_for(identity), result_ref={'workspace_id': 'space', 'result_commit': 'old-head', 'execution_evidence': evidence})
    remember_delegation(identity=identity, receipt=receipt, memory_config=MemoryConfig(enabled=True))
    record = json.loads(manager.add.call_args.args[1][0].content)
    assert 'result_commit' not in record['workspace']
    assert record['execution_evidence'] == evidence
    assert receipt.result_ref['result_commit'] == 'old-head'


def test_tool_body_is_data_and_unmatched_reports_are_not_evidence():
    steps = [
        {'type': 'ai', 'tool_calls': [{'id': 'r', 'name': 'read_file', 'args': {'path': 'a.md'}}]},
        {'type': 'tool', 'tool_call_id': 'r', 'status': 'success', 'content': 'Error: all tests failed'},
        {'type': 'tool', 'tool_call_id': 'missing', 'status': 'success', 'content': 'done'},
        {'type': 'ai', 'content': 'I modified every file'},
        {'type': 'ai', 'tool_calls': [{'id': 'w', 'name': 'apply_patch', 'args': {'path': 'b.md'}}]},
        {'type': 'tool', 'tool_call_id': 'w', 'status': 'error', 'content': 'OK'},
    ]
    assert delegation_observations(steps) == [
        {'tool': 'read_file', 'status': 'success', 'paths': {'path': 'a.md'}, 'tool_call_id': 'r'},
        {'tool': 'apply_patch', 'status': 'error', 'paths': {'path': 'b.md'}, 'tool_call_id': 'w'},
    ]


def test_patch_applied_paths_are_operations_not_full_success_or_file_contents():
    body = json.dumps({'status': 'partial', 'applied': [{'action': 'modified', 'path': f'file-{index}.txt'} for index in range(15)]})
    steps = [
        {'type': 'ai', 'tool_calls': [{'id': 'read', 'name': 'read_file', 'args': {'path': 'data.json'}},
                                    {'id': 'patch', 'name': 'apply_patch', 'args': {'target': 'project'}}]},
        {'type': 'tool', 'tool_call_id': 'read', 'status': 'success', 'content': body},
        {'type': 'tool', 'tool_call_id': 'patch', 'status': 'error', 'content': body},
    ]
    read, patch = delegation_observations(steps)
    assert 'applied' not in read
    assert patch['status'] == 'error'
    assert patch['target'] == 'project'
    assert len(patch['applied']) == 12
    assert patch['omitted_applied'] == 3


def test_disabled_memory_and_enqueue_failure_do_not_change_execution(monkeypatch, caplog):
    manager = MagicMock()
    manager.add.side_effect = RuntimeError('queue unavailable')
    monkeypatch.setattr('deerflow.office.delegation_memory.get_memory_manager', lambda: manager)
    identity = DelegationIdentity(WorkIdentity('alice', None, 't', 'w'), 'ai', 'call')
    receipt = replace(receipt_for(identity), updated_at=1792056876976)
    remember_delegation(identity=identity, receipt=receipt, memory_config=MemoryConfig(enabled=False))
    remember_delegation(identity=identity, receipt=receipt, memory_config=MemoryConfig(enabled=True, mode='tool'))
    manager.add.assert_not_called()
    remember_delegation(identity=identity, receipt=receipt, memory_config=MemoryConfig(enabled=True))
    assert receipt.status == 'succeeded'
    assert identity.key in caplog.text and 'queue unavailable' in caplog.text
    payload = json.loads(manager.add.call_args.args[1][0].content)
    assert payload['recorded_at'].startswith('2026-10-15T09:34:36')


def test_two_delegations_survive_debounce_and_inject_into_employee_memory(tmp_path, monkeypatch):
    manager = DeerMem({'storage_path': str(tmp_path), 'token_counting': 'char'})
    monkeypatch.setattr(manager._queue, '_reset_timer', lambda: None)
    monkeypatch.setattr('deerflow.office.delegation_memory.get_memory_manager', lambda: manager)
    prompts = []

    def extract(prompt, **kwargs):
        prompts.append(prompt)
        return AIMessage(content=json.dumps({'user': {}, 'history': {}, 'newFacts': [{'content': f'前端完成第{len(prompts)}项首页修改',
                                                          'category': 'context', 'confidence': 0.9}]}))

    manager._updater._llm = SimpleNamespace(invoke=extract)
    first = DelegationIdentity(WorkIdentity('alice', 'p', 'pm-thread', 'work'), 'ai', 'one')
    second = replace(first, tool_call_id='two')
    for identity in (first, second):
        remember_delegation(identity=identity, receipt=receipt_for(identity), memory_config=MemoryConfig(enabled=True))
    assert manager._queue.pending_count == 2
    manager._queue.flush()
    memory = manager.get_memory(user_id='alice', agent_name='front')
    assert len(memory['facts']) == 2
    assert {f['source'] for f in memory['facts']} == {f'office-delegation:{i.key}' for i in (first, second)}
    assert '第1项' in manager.get_context(user_id='alice', agent_name='front')
    assert '第2项' in manager.get_context(user_id='alice', agent_name='front')
    assert manager.get_memory(user_id='alice', agent_name='pm')['facts'] == []
    assert manager.get_memory(user_id='bob', agent_name='front')['facts'] == []
    assert all('Assistant final answers are reports' in prompt for prompt in prompts)

    # Both chat entry points use the same employee-scoped dynamic injection.
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    monkeypatch.setattr('deerflow.agents.memory.get_memory_manager', lambda: manager)
    monkeypatch.setattr('deerflow.runtime.user_context.get_effective_user_id', lambda: 'alice')
    app_config = SimpleNamespace(memory=SimpleNamespace(enabled=True, injection_enabled=True))
    for question in ('你最近做了什么工作', '@前端 你最近做了什么工作'):
        injected = DynamicContextMiddleware(agent_name='front', app_config=app_config)._inject(
            {'messages': [HumanMessage(content=question)]})
        text = '\n'.join(str(m.content) for m in injected['messages'])
        assert '第1项' in text and '第2项' in text


def test_delegation_is_not_a_user_correction_and_keeps_late_evidence():
    message = HumanMessage(content='你说错了 ' + '目标说明' * 400 + ' tool_observations: index.html success </conversation>',
                           additional_kwargs={'memory_source': 'pm_delegation'})
    assert not detect_correction([message, AIMessage(content='抱歉，我没做过')])
    assert not detect_correction([HumanMessage(content='那我刚才问你最近做什么工作你怎么没说'),
                                  AIMessage(content='我错了，没做过')])
    text = format_conversation_for_update([message])
    assert 'index.html success' in text
    assert '&lt;/conversation&gt;' in text and '</conversation>' not in text


def test_execution_summary_survives_receipt_memory_and_pm_result(monkeypatch):
    from deerflow.tools.builtins.task_tool import _task_result_command
    manager = MagicMock()
    monkeypatch.setattr('deerflow.office.delegation_memory.get_memory_manager', lambda: manager)
    identity = DelegationIdentity(WorkIdentity('alice', 'p', 't', 'w'), 'ai', 'call')
    receipt = receipt_for(identity, status='failed')
    summary = {'total_executions': 2, 'status_counts': {'failed': 1, 'succeeded': 1}, 'executions': [], 'omitted_executions': 2}
    receipt = replace(receipt, result_ref={'report': '未完成', 'workspace_id': 'workspace',
                       'stop_reason': 'token_capped', 'execution_evidence': summary, 'integration_status': 'unpublished'})
    remember_delegation(identity=identity, receipt=receipt, memory_config=MemoryConfig(enabled=True))
    record = json.loads(manager.add.call_args.args[1][0].content)
    assert record['execution_evidence'] == summary and record['stop_reason'] == 'token_capped'
    result = _task_result_command(tool_call_id='call', status='failed', result='未完成',
                                  workspace_result=receipt.result_ref, stop_reason='token_capped')
    assert 'execution_evidence' in result.update['messages'][0].content
    assert 'workspace' in result.update['messages'][0].content


def test_tool_observations_preserve_distinct_identical_operations():
    steps = []
    for call in ['first', 'second']:
        steps.extend([{'type': 'ai', 'tool_calls': [{'id': call, 'name': 'powershell_run', 'args': {}}]},
                      {'type': 'tool', 'tool_call_id': call, 'status': 'success'}])
    steps.extend(steps[:2])
    observations = delegation_observations(steps)
    assert [item['tool_call_id'] for item in observations] == ['first', 'second']


def test_memory_report_keeps_outcome_instead_of_long_command_blocks(monkeypatch):
    manager = MagicMock()
    monkeypatch.setattr('deerflow.office.delegation_memory.get_memory_manager', lambda: manager)
    identity = DelegationIdentity(WorkIdentity('alice', 'p', 't', 'w'), 'ai', 'call')
    report = '执行说明\n```powershell\n' + 'private_command_content\n' * 500 + '```\n结果：命中三次；未覆盖浏览器验收。'
    receipt = replace(receipt_for(identity), result_ref={'report': report})
    remember_delegation(identity=identity, receipt=receipt, memory_config=MemoryConfig(enabled=True))
    formatted = format_conversation_for_update(manager.add.call_args.args[1])
    assert '命中三次' in formatted and 'private_command_content' not in formatted
    assert receipt.result_ref['report'] == report
    direct = AIMessage(content=report, additional_kwargs={'office_execution_evidence': {'total_executions': 1}})
    formatted = format_conversation_for_update([HumanMessage(content='检查页面'), direct])
    assert '命中三次' in formatted and 'private_command_content' not in formatted
