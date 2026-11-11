from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from deerflow.config.paths import Paths
from deerflow.office.execution_facts import DelegationIdentity, ExecutionReceiptStore, WorkIdentity


def test_receipt_atomic_replay_resume_and_conflict(tmp_path):
    store = ExecutionReceiptStore(paths=Paths(tmp_path), user_id='alice')
    identity = DelegationIdentity(WorkIdentity('alice', 'p', 't', 'w'), 'ai', 'call')
    def claim(parent='r'):
        return store.claim(identity, employee_id='front', request_hash='hash', parent_run_id=parent, input_ref={'goal': 'original'})
    with ThreadPoolExecutor(8) as pool:
        claims = list(pool.map(lambda _: claim(), range(16)))
    assert sum(first for _, first in claims) == 1
    store.observe(identity, parent_run_id='r', native_task_id='call', status='succeeded', result_ref={'report': 'done'}, evidence=[])
    restored = ExecutionReceiptStore(paths=Paths(tmp_path), user_id='alice')
    receipt, first = restored.claim(identity, employee_id='front', request_hash='hash', parent_run_id='resume', input_ref={'goal': 'new answer'})
    assert not first and receipt.input_ref == {'goal': 'original'} and receipt.result_ref == {'report': 'done'}
    with pytest.raises(ValueError):
        restored.claim(identity, employee_id='front', request_hash='changed', parent_run_id='resume', input_ref={})
    with pytest.raises(ValueError):
        restored.observe(identity, parent_run_id='resume', native_task_id='call', status='failed', result_ref=None, evidence=[])
    assert restored.claim(replace(identity, tool_call_id='new'), employee_id='front', request_hash='hash', parent_run_id='resume', input_ref={})[1]


def test_receipt_unknown_never_releases_identity(tmp_path):
    store = ExecutionReceiptStore(paths=Paths(tmp_path), user_id='alice')
    identity = DelegationIdentity(WorkIdentity('alice', None, 't', 'w'), 'ai', 'call')
    args = dict(employee_id='front', request_hash='h', parent_run_id='r', input_ref={})
    store.claim(identity, **args)
    store.mark_unresolved_unknown()
    receipt, first = store.claim(identity, **args)
    assert not first and receipt.status == 'unknown'
    with pytest.raises(ValueError):
        store.get(replace(identity, work=replace(identity.work, user_id='bob')))


def test_read_only_delegation_does_not_allocate_a_development_workspace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from deerflow.config import paths
    from deerflow.office.delegation import claim_delegation
    from deerflow.office.work_context import build_work_context

    monkeypatch.setattr(paths, '_paths', Paths(tmp_path / 'data'))
    project = tmp_path / 'project'
    project.mkdir()
    work = WorkIdentity('alice', 'project', 'thread', 'work')
    args = dict(description='Read', prompt='介绍一下项目', subagent_type='reader')
    runtime = SimpleNamespace(context=dict(office_work=work.to_record(), office_project_id='project',
        office_project_path=str(project), thread_id='thread', run_id='run',
        office_work_context=build_work_context(**work.to_record(), latest_user_message='看看项目')),
        config={'metadata': {'office_employee_id': 'pm'}},
        state={'messages': [AIMessage(content='', id='message', tool_calls=[{'id': 'call', 'name': 'task', 'args': args}])]})
    _, _, receipt, first, _ = claim_delegation(runtime, user_id='alice', employee_id='reader', tool_call_id='call',
        package_snapshot={'tool_names': ['read_file', 'report_to_pm']}, **args)
    assert first and receipt.input_ref['workspace'] is None
    assert list(project.iterdir()) == []


def test_native_claim_keeps_first_background_across_resume_and_allows_additional_employee(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage

    from deerflow.config import paths
    from deerflow.office.delegation import claim_delegation
    from deerflow.office.work_context import build_work_context

    monkeypatch.setattr(paths, '_paths', Paths(tmp_path))
    work = WorkIdentity('alice', 'p', 't', 'w')
    args = {'description': 'Review', 'prompt': 'Review the proposed API', 'subagent_type': 'reviewer'}
    runtime = SimpleNamespace(context={'office_work': work.to_record(), 'office_project_id': 'p',
        'thread_id': 't', 'run_id': 'first', 'office_work_context': build_work_context(**work.to_record(), latest_user_message='Build UI')},
        config={'metadata': {'office_employee_id': 'pm', 'office_preselected_employee_ids': ['front']}},
        state={'messages': [AIMessage(content='', id='persisted-ai', tool_calls=[{'id': 'call', 'name': 'task', 'args': args}])]})
    # A preselected frontend employee is not a whitelist forbidding an additional reviewer.
    store, identity, original, first, prompt = claim_delegation(runtime, user_id='alice', employee_id='reviewer', tool_call_id='call', **args)
    assert first and 'Review the proposed API' in prompt
    assert original.input_ref['selected_employee_ids'] == ['front']
    store.observe(identity, parent_run_id='first', native_task_id='native', status='succeeded', result_ref={'report': 'reviewed'}, evidence=[])
    runtime.context['run_id'] = 'resumed'
    runtime.context['office_work_context']['latest_user_message'] = 'New clarification'
    _, _, replay, first, _ = claim_delegation(runtime, user_id='alice', employee_id='reviewer', tool_call_id='call', **args)
    assert not first and replay.input_ref == original.input_ref
    assert replay.result_ref == {'report': 'reviewed'}
    runtime.state['messages'][0].id = None
    with pytest.raises(ValueError, match='persisted AI'):
        claim_delegation(runtime, user_id='alice', employee_id='reviewer', tool_call_id='call', **args)


@pytest.mark.parametrize('tools', [['read_file'], ['read_file', 'apply_patch', 'powershell_run']])
def test_explicit_environment_choices_do_not_follow_tool_permissions(tmp_path, monkeypatch, tools):
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from deerflow.config import paths
    from deerflow.office.delegation import claim_delegation
    from deerflow.office.execution_workspace import ExecutionWorkspaceService
    from deerflow.office.work_context import build_work_context

    data = Paths(tmp_path / 'data')
    monkeypatch.setattr(paths, '_paths', data)
    project = tmp_path / 'project'
    project.mkdir()
    work = WorkIdentity('alice', 'p', 'thread', 'work')
    runtime = SimpleNamespace(context=dict(office_work=work.to_record(), office_project_id='p',
        office_project_path=str(project), thread_id='thread', run_id='run',
        office_work_context=build_work_context(**work.to_record(), latest_user_message='做个活动页')),
        config={'metadata': {'office_employee_id': 'pm'}}, state={})

    def claim(call_id, employee='front', **selection):
        args = dict(description='Build page', prompt='完成活动页', subagent_type=employee, **selection)
        runtime.state['messages'] = [AIMessage(content='', id=call_id, tool_calls=[dict(id=call_id, name='task', args=args)])]
        return claim_delegation(runtime, user_id='alice', employee_id=employee, tool_call_id=call_id,
                                package_snapshot={'tool_names': tools}, **args)[2]

    assert claim('direct').input_ref['workspace'] is None
    assert list(project.iterdir()) == []
    isolated = claim('isolated', workspace_mode='new').input_ref['workspace']
    assert isolated == claim('isolated', workspace_mode='new').input_ref['workspace']
    service = ExecutionWorkspaceService(paths=data, user_id='alice')
    assert service.get(isolated['workspace_id'])['root_path'] is None
    assert not (project / '.git').exists()
    continued = claim('continue', employee='second', workspace_mode='continue', workspace_id=isolated['workspace_id'])
    assert continued.input_ref['workspace'] == isolated
    forked = claim('fork', workspace_mode='fork', workspace_id=isolated['workspace_id']).input_ref['workspace']
    assert forked != isolated
    assert service.get(forked['workspace_id'])['source_workspace_id'] == isolated['workspace_id']
    runtime.context['office_execution_workspace'] = isolated
    assert claim('inherit').input_ref['workspace'] == isolated
    for selection in ({'workspace_mode': 'new', 'workspace_id': isolated['workspace_id']},
                      {'workspace_mode': 'continue'}, {'workspace_mode': 'fork'}, {'workspace_mode': 'invalid'}):
        with pytest.raises(ValueError):
            claim('bad', **selection)
