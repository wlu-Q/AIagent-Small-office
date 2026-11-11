from pathlib import Path

import pytest

from deerflow.office import execution_workspace as workspace, workspace_delivery as delivery
from deerflow.office.execution_workspace import _git, unmerged_paths
from test_execution_workspace import repository as repository
from test_workspace_delivery import delivery_setup as delivery_setup


def test_task_return_keeps_location_without_claiming_head_as_result(delivery_setup):
    from deerflow.tools.builtins.task_tool import _task_result_command
    import json

    service, work, allocate, _, _ = delivery_setup
    workspace_id = allocate()
    value = service.prepare(workspace_id, work=work)
    reference = workspace.result_reference(value)
    message = _task_result_command(tool_call_id='return', status='completed', result='done', workspace_result=reference).update['messages'][0]
    facts = json.loads(message.content.split('Task workspace facts: ')[1])
    assert 'result_commit' not in facts and 'result_commit' not in reference
    assert facts['baseline_commit'] == reference['baseline_commit'] == value['baseline_commit']
    assert facts['workspace_path'] == reference['workspace_path']


def conflicting_workspaces(setup):
    service, work, allocate, project, _ = setup
    first_id, second_id = allocate('first'), allocate('second')
    first = Path(service.prepare(first_id, work=work)['root_path'])
    second = Path(service.prepare(second_id, work=work)['root_path'])
    (first / 'index.txt').write_text('first\n')
    (second / 'index.txt').write_text('second\n')
    service.snapshot(first_id, work=work)
    other = service.snapshot(second_id, work=work)
    merged = _git(first, 'merge', '--no-edit', other['result_commit'], check=False, return_process=True)
    assert merged.returncode == 1
    return first_id, second_id, first, second, project


def test_unresolved_native_index_cannot_be_saved_forked_or_written_back(delivery_setup):
    service, work, _, _, _ = delivery_setup
    first_id, _, first, _, project = conflicting_workspaces(delivery_setup)
    head = _git(first, 'rev-parse', 'HEAD')
    stages = _git(first, 'ls-files', '--stage', '-z')
    merge_head = _git(first, 'rev-parse', 'MERGE_HEAD')
    with pytest.raises(ValueError, match='Unmerged Git index'):
        service.snapshot(first_id, work=work)
    fork = service.allocate(work, employee_id='third', project_path=project, owner_key='fork',
                            mode='fork', source_workspace_id=first_id)
    with pytest.raises(ValueError, match='Unmerged Git index'):
        service.prepare(fork['workspace_id'], work=work)
    for _ in range(2):
        result = delivery.publish(service, work, first_id, context={})
        assert result == dict(workspace_id=first_id, status='conflict', conflicts=['index.txt'], written_files=[])
        assert _git(first, 'rev-parse', 'HEAD') == head
        assert _git(first, 'ls-files', '--stage', '-z') == stages
        assert _git(first, 'rev-parse', 'MERGE_HEAD') == merge_head
        assert (project / 'index.txt').read_bytes() == b'original\r\n'
    (first / 'index.txt').write_text('resolved but not staged\n')
    assert delivery.publish(service, work, first_id, context={})['status'] == 'conflict'


@pytest.mark.parametrize('choice,expected', [('ours', 'first\n'), ('theirs', 'second\n')])
def test_native_resolution_keeps_merge_parents(delivery_setup, choice, expected):
    service, work, _, _, _ = delivery_setup
    first_id, _, first, second, project = conflicting_workspaces(delivery_setup)
    parents = {_git(first, 'rev-parse', 'HEAD').strip(), _git(second, 'rev-parse', 'HEAD').strip()}
    _git(first, 'checkout', f'--{choice}', '--', 'index.txt')
    _git(first, 'add', 'index.txt')
    result = delivery.publish(service, work, first_id, context={})
    assert result['status'] == 'written'
    assert set(_git(first, 'rev-list', '--parents', '-n', '1', 'HEAD').split()[1:]) == parents
    assert not unmerged_paths(first)
    assert not _git(first, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).strip()
    assert (project / 'index.txt').read_text() == expected


def test_abort_retains_saved_work_and_does_not_touch_bound_project(delivery_setup):
    service, work, _, _, _ = delivery_setup
    first_id, _, first, _, project = conflicting_workspaces(delivery_setup)
    head = _git(first, 'rev-parse', 'HEAD')
    _git(first, 'merge', '--abort')
    assert _git(first, 'rev-parse', 'HEAD') == head
    assert not unmerged_paths(first)
    assert (first / 'index.txt').read_text() == 'first\n'
    assert (project / 'index.txt').read_bytes() == b'original\r\n'
    assert delivery.publish(service, work, first_id, context={})['status'] == 'written'


def test_modify_delete_conflict_retains_index_and_user_content(delivery_setup):
    service, work, allocate, project, _ = delivery_setup
    first_id, second_id = allocate('first'), allocate('second')
    first = Path(service.prepare(first_id, work=work)['root_path'])
    second = Path(service.prepare(second_id, work=work)['root_path'])
    (first / 'index.txt').unlink()
    (second / 'index.txt').write_text('modified\n')
    result = delivery.publish(service, work, first_id, context={}, source_workspace_ids=[second_id])
    assert result['status'] == 'conflict'
    assert unmerged_paths(first) == ['index.txt']
    assert (project / 'index.txt').read_bytes() == b'original\r\n'
    _git(first, 'checkout', '--theirs', '--', 'index.txt')
    _git(first, 'add', 'index.txt')
    assert delivery.publish(service, work, first_id, context={})['status'] == 'written'
    assert (project / 'index.txt').read_text() == 'modified\n'


def test_marker_text_in_normal_file_is_not_a_conflict(delivery_setup):
    service, work, allocate, project, _ = delivery_setup
    workspace_id = allocate()
    root = Path(service.prepare(workspace_id, work=work)['root_path'])
    text = 'Git conflict documentation:\n<<<<<<< ours\n=======\n>>>>>>> theirs\n'
    (root / 'example.md').write_text(text)
    result = delivery.publish(service, work, workspace_id, context={})
    assert result['status'] == 'written'
    assert (project / 'example.md').read_text() == text


def test_excluded_and_deleted_files_do_not_consume_baseline_limit(tmp_path, monkeypatch):
    _git(tmp_path, 'init', '--template=')
    (tmp_path / 'removed.txt').write_text('remove')
    _git(tmp_path, 'add', 'removed.txt')
    (tmp_path / 'removed.txt').unlink()
    (tmp_path / 'source.txt').write_text('source')
    (tmp_path / 'node_modules').mkdir()
    for name in ('first', 'second'):
        (tmp_path / 'node_modules' / name).write_text('dependency')
    (tmp_path / '.env').write_text('PRIVATE_TEST_VALUE')
    monkeypatch.setattr(workspace, '_MAX_FILES', 1)
    assert list(workspace._capture(tmp_path)[2]) == ['source.txt']


def test_fixed_git_operations_do_not_execute_configured_extensions(delivery_setup):
    service, work, allocate, project, _ = delivery_setup
    (project / '.gitattributes').write_text('*.txt filter=untrusted\n')
    for key, value in [('filter.untrusted.clean', 'this-command-must-not-run'),
                       ('filter.untrusted.smudge', 'this-command-must-not-run'),
                       ('filter.untrusted.required', 'true'), ('commit.gpgSign', 'true'),
                       ('gpg.program', 'this-command-must-not-run')]:
        _git(project, 'config', key, value)
    workspace_id = allocate()
    root = Path(service.prepare(workspace_id, work=work)['root_path'])
    (root / 'index.txt').write_text('safe\n')
    result = delivery.publish(service, work, workspace_id, context={})
    assert result['status'] == 'written'
    assert (project / 'index.txt').read_text() == 'safe\n'
