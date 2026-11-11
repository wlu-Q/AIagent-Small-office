import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from deerflow.office.delivery_tool import publish_task_result
from deerflow.office.delegation_memory import writeback_evidence
from deerflow.office.execution_workspace import _git, unmerged_paths
from deerflow.sandbox import tools
from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.patch_tool import apply_patch_tool
from patch_helpers import add_patch, update_patch
from test_native_merge_writeback import conflicting_workspaces
from test_workspace_delivery import delivery_setup as delivery_setup
from test_execution_workspace import repository as repository


@pytest.fixture
def runtime(delivery_setup, monkeypatch):
    service, work, _, project, _ = delivery_setup
    sandbox = LocalSandbox('local:alice:test')
    monkeypatch.setattr(tools, 'ensure_sandbox_initialized', lambda runtime: sandbox)
    async def initialized(runtime):
        return sandbox
    monkeypatch.setattr(tools, 'ensure_sandbox_initialized_async', initialized)
    return SimpleNamespace(context={
        'user_id': work.user_id, 'office_project_id': work.project_id, 'office_work': work.to_record(),
        'office_project_path': str(project), 'office_employee_id': 'integrator', 'agent_name': 'integrator',
        'run_id': 'integration', 'abort_event': threading.Event(),
    }, state={'thread_data': {'workspace_path': str(project), 'outputs_path': str(service.paths.base_dir / 'outputs')}},
        tool_call_id='writeback')


def publish(runtime, target=None, sources=None):
    return publish_task_result.func(runtime=runtime, target=target, source_workspace_ids=sources)


@pytest.mark.asyncio
@pytest.mark.parametrize('asynchronous', [False, True])
async def test_current_employee_edits_and_integrates_existing_worktrees(delivery_setup, runtime, asynchronous):
    service, work, allocate, project, _ = delivery_setup
    first, second = allocate('first'), allocate('second')
    (project / 'index.txt').write_text('user staged\n')
    _git(project, 'add', 'index.txt')
    (project / 'index.txt').write_text('user unstaged\n')
    (project / 'local.txt').write_text('untracked\n')
    (project / 'deleted.txt').unlink()
    index = (project / '.git/index').read_bytes()
    head = _git(project, 'rev-parse', 'HEAD')
    original_data = copy.deepcopy(runtime.state['thread_data'])
    for selected, name in [(first, 'first.txt'), (second, 'second.txt')]:
        args = dict(runtime=runtime, target=selected, patch=add_patch('/mnt/user-data/workspace/' + name, name))
        result = await apply_patch_tool.coroutine(**args) if asynchronous else apply_patch_tool.func(**args)
        assert result.status == 'success', result.content
        assert not (project / name).exists()
    message = await publish_task_result.coroutine(runtime=runtime, target=first, source_workspace_ids=[second]) if asynchronous else publish(runtime, first, [second])
    result = json.loads(message.content)
    assert result['status'] == 'written', result
    assert set(result['written_files']) == {'first.txt', 'second.txt'}
    assert (project / 'first.txt').read_text().strip() == 'first.txt'
    assert (project / 'second.txt').read_text().strip() == 'second.txt'
    first_root = service.get(first, work=work)['root_path']
    second_head = _git(service.get(second, work=work)['root_path'], 'rev-parse', 'HEAD').decode().strip()
    assert _git(first_root, 'merge-base', '--is-ancestor', second_head, 'HEAD', return_process=True).returncode == 0
    assert (project / '.git/index').read_bytes() == index
    assert _git(project, 'rev-parse', 'HEAD') == head
    assert (project / 'index.txt').read_text() == 'user unstaged\n'
    assert (project / 'local.txt').read_text() == 'untracked\n'
    assert not (project / 'deleted.txt').exists()
    assert runtime.state['thread_data'] == original_data
    assert 'office_execution_workspace' not in runtime.context
    assert publish(runtime, first).artifact['workspace_writeback']['status'] == 'unchanged'


def test_conflict_is_resolved_in_target_not_replaced_in_project(delivery_setup, runtime):
    _, _, _, project, _ = delivery_setup
    first_id, _, first, _, _ = conflicting_workspaces(delivery_setup)
    conflict = publish(runtime, first_id)
    assert conflict.status == 'error'
    assert conflict.artifact['workspace_writeback']['conflicts'] == ['index.txt']
    old = (first / 'index.txt').read_text()
    patched = apply_patch_tool.func(runtime=runtime, target=first_id,
        patch=update_patch('/mnt/user-data/workspace/index.txt', old, 'first and second\n'))
    assert patched.status == 'success', patched.content
    assert publish(runtime, first_id).artifact['workspace_writeback']['status'] == 'conflict'
    assert (project / 'index.txt').read_text() == 'original\n'
    _git(first, 'add', 'index.txt')
    result = publish(runtime, first_id)
    assert result.artifact['workspace_writeback']['status'] == 'written'
    assert not unmerged_paths(first)
    assert _git(first, 'rev-parse', '--verify', 'MERGE_HEAD', check=False, return_process=True).returncode != 0
    assert (project / 'index.txt').read_text() == 'first and second\n'


def test_target_project_and_default_directory_remain_distinct(delivery_setup, runtime):
    service, work, allocate, project, _ = delivery_setup
    workspace_id = allocate()
    runtime.context['office_execution_workspace'] = service.prepare(workspace_id, work=work)
    for target, name in [('project', 'direct.txt'), (None, 'private.txt')]:
        result = apply_patch_tool.func(runtime=runtime, target=target, patch=add_patch('/mnt/user-data/workspace/' + name, name))
        assert result.status == 'success', result.content
    assert (project / 'direct.txt').exists() and not (project / 'private.txt').exists()
    assert publish(runtime).artifact['workspace_writeback']['status'] == 'written'
    assert (project / 'private.txt').exists()


@pytest.mark.parametrize('invalid', ['missing-workspace', 'different-project', 'different-user'])
def test_targets_do_not_expand_ownership(delivery_setup, runtime, invalid):
    service, work, allocate, project, _ = delivery_setup
    workspace_id = allocate()
    target = workspace_id
    if invalid == 'missing-workspace':
        target = 'missing'
    elif invalid == 'different-project':
        runtime.context['office_work'] = {**work.to_record(), 'project_id': 'other'}
        runtime.context['office_project_id'] = 'other'
    else:
        runtime.context['user_id'] = 'other'
    patch = apply_patch_tool.func(runtime=runtime, target=target, patch=add_patch('/mnt/user-data/workspace/forbidden.txt', 'bad'))
    assert patch.status == 'error'
    assert publish(runtime, target).status == 'error'
    assert not (project / 'forbidden.txt').exists()


@pytest.mark.parametrize('path', ['/mnt/user-data/workspace/../escape.txt', '/mnt/user-data/workspace/.git/config'])
def test_target_keeps_path_protection(delivery_setup, runtime, path):
    _, _, allocate, _, _ = delivery_setup
    assert apply_patch_tool.func(runtime=runtime, target=allocate(), patch=add_patch(path, 'bad')).status == 'error'


def test_missing_publication_target_is_actionable_not_a_copy_fallback(runtime):
    result = publish(runtime)
    assert result.status == 'error'
    assert 'target=<existing workspace ID>' in result.content


def test_writeback_evidence_uses_paired_tool_artifacts_not_report_or_file_text(delivery_setup, runtime):
    _, _, allocate, _, _ = delivery_setup
    workspace_id = allocate()
    message = publish(runtime, workspace_id)
    call = AIMessage(content='Everything passed', tool_calls=[dict(name='publish_task_result', id='writeback', args={'target': workspace_id})])
    spoof = message.model_copy(update={'name': 'read_file', 'tool_call_id': 'file'})
    steps = [call.model_dump(), message.model_dump(), spoof.model_dump(), message.model_dump()]
    summary = writeback_evidence(steps)
    assert summary['total_operations'] == 1
    assert summary['operations'][0]['status'] == 'unchanged'
    assert summary['operations'][0]['workspace_id'] == workspace_id
    assert writeback_evidence([call.model_dump()])['total_operations'] == 0
    assert writeback_evidence([message.model_dump()])['total_operations'] == 0


def test_writeback_evidence_preserves_failures_and_bounds_history():
    from langchain_core.messages import ToolMessage
    from deerflow.agents.memory.backends.deermem.deermem.core.prompt import _count_tokens

    steps = []
    for number in range(10):
        call_id = f'call-{number}'
        status = 'conflict' if number == 9 else 'written'
        steps.extend([
            AIMessage(content='', tool_calls=[dict(name='publish_task_result', id=call_id, args={})]).model_dump(),
            ToolMessage(content='Not parsed as evidence', name='publish_task_result', tool_call_id=call_id,
                artifact={'workspace_writeback': dict(workspace_id='owned', status=status,
                    conflicts=['README.md'] if status == 'conflict' else [], written_files=[])}).model_dump(),
        ])
    result = writeback_evidence(steps)
    assert result['total_operations'] == 10
    assert result['omitted_operations'] == 6
    assert len(result['operations']) == 4
    assert result['operations'][-1]['status'] == 'conflict'
    assert result['operations'][-1]['conflicts'] == ['README.md']
    assert _count_tokens(json.dumps(result)) <= 800


def test_writeback_error_artifact_reaches_pm_without_becoming_success(runtime):
    from deerflow.tools.builtins.task_tool import _task_result_command

    failed = publish(runtime)
    call = AIMessage(content='I finished', tool_calls=[dict(name='publish_task_result', id='writeback', args={})])
    summary = writeback_evidence([call.model_dump(), failed.model_dump()])
    assert summary['operations'][0]['status'] == 'error'
    message = _task_result_command(tool_call_id='parent', status='completed', result='I finished',
        workspace_result={'workspace_id': None, 'execution_evidence': {'writebacks': summary}}).update['messages'][0]
    facts = json.loads(message.content.split('Task workspace facts: ')[1])
    assert facts['execution_evidence']['writebacks']['operations'][0]['status'] == 'error'
