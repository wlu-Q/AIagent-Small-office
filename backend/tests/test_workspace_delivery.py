"""Actual Git merges/publication, including user edits and failed transactions."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_execution_workspace import repository as repository, service
from deerflow.office.execution_facts import DelegationIdentity, WorkIdentity
from deerflow.office.execution_workspace import _git
from deerflow.office import workspace_delivery as delivery, terminal


@pytest.fixture
def delivery_setup(tmp_path, repository, monkeypatch):
    svc, work, allocate = service(tmp_path, repository)
    monkeypatch.setattr('deerflow.office.execution_workspace.get_paths', lambda: svc.paths)
    setting = svc.paths.user_dir('alice') / 'office-bootstrap.yaml'
    setting.write_text('terminal_access_mode: full_access\n')
    yield svc, work, allocate, repository, setting
    terminal.close_terminals()


def test_publish_preserves_dirty_user_index_and_receipts(delivery_setup):
    svc, work, alloc, root, _ = delivery_setup
    (root / 'index.txt').write_bytes(b'user staged\r\n')
    _git(root, 'add', 'index.txt')
    (root / 'index.txt').write_bytes(b'user unstaged\r\n')
    (root / 'personal.txt').write_text('user new')
    index = (root / '.git/index').read_bytes()
    head = _git(root, 'rev-parse', 'HEAD')
    wid = alloc(); task = Path(svc.prepare(wid, work=work)['root_path'])
    (task / 'index.txt').write_bytes(b'user unstaged\r\nAI line\r\n')
    (task / 'deleted.txt').unlink()
    (task / 'renamed.txt').rename(task / 'moved.txt')
    result = delivery.publish(svc, work, wid, context={'run_id': 'r', 'office_employee_id': 'writer'})
    assert result['status'] == 'written'
    assert 'validation_status' not in result
    assert (root / '.git/index').read_bytes() == index
    assert _git(root, 'rev-parse', 'HEAD') == head
    assert (root / 'personal.txt').read_text() == 'user new'
    assert (root / 'index.txt').read_bytes() == b'user unstaged\r\nAI line\r\n'
    assert not (root / 'deleted.txt').exists()
    assert (root / 'moved.txt').read_text() == 'rename me'
    assert 'integration_status' not in svc.get(wid, work=work)
    repeated = delivery.publish(svc, work, wid, context={})
    assert repeated['result_commit'] == result['result_commit']
    assert repeated['written_files'] == []


def test_user_changed_same_file_conflict_keeps_source_until_resolved(delivery_setup):
    svc, work, alloc, root, _ = delivery_setup
    wid = alloc(); task = Path(svc.prepare(wid, work=work)['root_path'])
    (task / 'index.txt').write_text('AI version\n')
    (root / 'index.txt').write_text('user version\n')
    first = delivery.publish(svc, work, wid, context={})
    assert first['status'] == 'conflict'
    assert first['conflicts'] == ['index.txt']
    assert (root / 'index.txt').read_text() == 'user version\n'
    assert '<<<<<<<' in (task / 'index.txt').read_text()
    assert delivery.publish(svc, work, wid, context={})['status'] == 'conflict'
    assert _git(task, 'ls-files', '-u')
    (task / 'index.txt').write_text('user version\nAI version\n')
    _git(task, 'add', 'index.txt')
    assert delivery.publish(svc, work, wid, context={})['status'] == 'written'


def test_clean_concurrent_edits_publish_once_without_revalidation(delivery_setup):
    svc, work, alloc, root, _ = delivery_setup
    wid = alloc(); task = Path(svc.prepare(wid, work=work)['root_path'])
    (task / 'index.txt').write_text('AI\n')
    (root / 'personal.txt').write_text('user\n')
    result = delivery.publish(svc, work, wid, context={})
    assert result['status'] == 'written'
    assert (root / 'index.txt').read_text() == 'AI\n'
    assert (task / 'personal.txt').read_text() == 'user\n'
    assert delivery.publish(svc, work, wid, context={})['status'] == 'unchanged'
    assert (root / 'personal.txt').read_text() == 'user\n'


def test_git_patch_mismatch_leaves_all_other_user_files_untouched(delivery_setup, monkeypatch):
    svc, work, alloc, root, _ = delivery_setup
    wid = alloc(); task = Path(svc.prepare(wid, work=work)['root_path'])
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    (task / 'index.txt').write_text('AI')
    (task / 'renamed.txt').write_text('AI')
    real = delivery._git
    def changed_before_apply(directory, *args, **kwargs):
        if Path(directory) == root and 'apply' in args:
            (root / 'renamed.txt').write_bytes(b'new user edit')
        return real(directory, *args, **kwargs)
    monkeypatch.setattr(delivery, '_git', changed_before_apply)
    with pytest.raises(ValueError, match='Git apply failed'):
        delivery.publish(svc, work, wid, context={})
    assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == {**before, 'renamed.txt': b'new user edit'}
    assert not (svc.paths.user_dir('alice') / 'execution-workspaces' / wid / 'publication').exists()


@pytest.mark.parametrize('status,reason', [('failed', None), ('timeout', 'round_timeout'),
                                         ('succeeded', 'token_capped'), ('succeeded', 'turn_capped')])
def test_continue_preserves_partial_files_and_original_receipt(delivery_setup, status, reason):
    svc, work, _, root, _ = delivery_setup
    identity = DelegationIdentity(work, 'model', 'partial')
    svc.store.claim(identity, employee_id='first', request_hash='hash', parent_run_id='run', input_ref={})
    original = svc.allocate(work, employee_id='first', owner_key=identity.key, project_path=root)
    original_id = original['workspace_id']
    tree = Path(svc.prepare(original_id, work=work)['root_path'])
    (tree / 'partial.txt').write_text('Existing work, keep it')
    saved = {**svc.get(original_id), 'stop_reason': reason, 'report': 'Header complete'}
    svc.store.observe(identity, parent_run_id='run', native_task_id='partial', status=status, result_ref=saved, evidence=[])
    next_work = WorkIdentity(work.user_id, work.project_id, 'next-thread', 'next-work')
    continued = svc.allocate(next_work, employee_id='second', owner_key='next', project_path=root,
                             mode='continue', source_workspace_id=original_id)
    assert continued['workspace_id'] == original_id
    actual = svc.prepare(original_id, work=next_work)
    assert Path(actual['root_path']) == tree
    assert (tree / 'partial.txt').read_text() == 'Existing work, keep it'
    assert svc.store.get(identity).status == status
    assert svc.store.get(identity).result_ref == saved
    assert not (root / 'partial.txt').exists()
    (tree / 'footer.txt').write_text('New work')
    assert delivery.publish(svc, next_work, original_id, context={'office_employee_id': 'second'})['status'] == 'written'
    assert (root / 'partial.txt').read_text() == 'Existing work, keep it'
    assert (root / 'footer.txt').read_text() == 'New work'


def test_publication_approval_preserves_unrelated_changes_during_wait(delivery_setup):
    svc, work, alloc, root, setting = delivery_setup
    setting.write_text('terminal_access_mode: approval\n')
    wid = alloc(); task = Path(svc.prepare(wid, work=work)['root_path'])
    (task / 'index.txt').write_text('AI\n')
    approvals = terminal.manager_for(svc.store).approvals
    with ThreadPoolExecutor() as pool:
        future = pool.submit(delivery.publish, svc, work, wid, context={'run_id': 'r', 'office_employee_id': 'writer'})
        deadline = time.monotonic() + 10
        while not approvals.pending():
            assert time.monotonic() < deadline
            time.sleep(.02)
        request = approvals.pending()[0]
        (root / 'personal.txt').write_text('concurrent edit')
        approvals.decide(request['approval_id'], request['request_hash'], 'approve')
        assert future.result(10)['status'] == 'written'
    assert (root / 'index.txt').read_text() == 'AI\n'
    assert (root / 'personal.txt').read_text() == 'concurrent edit'


def test_non_git_initialization_leaves_user_files_and_index_unstaged(delivery_setup, tmp_path):
    svc, work, _, _, _ = delivery_setup
    root = tmp_path / 'new-project'; root.mkdir()
    (root / 'user.txt').write_bytes(b'original\r\n')
    ref = svc.allocate(work, employee_id='writer', owner_key='new', project_path=root)
    task = svc.prepare(ref['workspace_id'], work=work, context={'run_id': 'r', 'office_employee_id': 'writer'})
    assert (root / 'user.txt').read_bytes() == b'original\r\n'
    assert not (root / '.git/index').exists()
    assert not _git(root, 'rev-parse', '--verify', 'HEAD', check=False).strip()
    assert (Path(task['root_path']) / 'user.txt').read_bytes() == b'original\r\n'


def test_publication_tool_reports_denial_without_retry(delivery_setup, monkeypatch):
    from types import SimpleNamespace
    from deerflow.office.delivery_tool import publish_task_result
    from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY
    svc, work, alloc, _, _ = delivery_setup
    wid = alloc()
    value = svc.prepare(wid, work=work)
    monkeypatch.setattr('deerflow.office.execution_workspace.execution_environment', lambda context, **kwargs: (svc, work, value))
    def denied(*args, **kwargs):
        raise PermissionError('User rejected this request; do not retry it automatically')
    monkeypatch.setattr(delivery, 'publish', denied)
    result = publish_task_result.func(SimpleNamespace(context={}, tool_call_id='denied', state={}))
    assert result.status == 'error'
    assert result.additional_kwargs[TOOL_META_KEY]['recoverable_by_model'] is False
    assert result.additional_kwargs[TOOL_META_KEY]['recommended_next_action'] == 'stop'


def test_parallel_publications_preserve_both_ancestries(delivery_setup):
    svc, work, alloc, project, _ = delivery_setup
    first_id, second_id = alloc('first'), alloc('second')
    first_root = Path(svc.prepare(first_id, work=work)['root_path'])
    second_root = Path(svc.prepare(second_id, work=work)['root_path'])
    (first_root / 'Home.vue').write_text('home')
    (second_root / 'Events.vue').write_text('events')
    original_index = (project / '.git/index').read_bytes()
    original_head = _git(project, 'rev-parse', 'HEAD')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda identity: delivery.publish(svc, work, identity, context={}), [first_id, second_id]))
    assert all(result['status'] == 'written' for result in results)
    assert (project / 'Home.vue').read_text() == 'home'
    assert (project / 'Events.vue').read_text() == 'events'
    assert (project / '.git/index').read_bytes() == original_index
    assert _git(project, 'rev-parse', 'HEAD') == original_head
    later_id = alloc('later')
    later = svc.prepare(later_id, work=work)
    for result in results:
        _git(project, 'merge-base', '--is-ancestor', result['result_commit'], later['baseline_commit'])
    assert (Path(later['root_path']) / 'Home.vue').read_text() == 'home'


def test_second_publication_and_user_commit_keep_real_history(delivery_setup):
    svc, work, alloc, project, _ = delivery_setup
    workspace_id = alloc()
    task = Path(svc.prepare(workspace_id, work=work)['root_path'])
    (task / 'index.txt').write_text('first version\n')
    first = delivery.publish(svc, work, workspace_id, context={})
    (project / 'user.txt').write_text('user contribution')
    _git(project, 'add', 'user.txt')
    _git(project, 'commit', '-m', 'user commit')
    head = _git(project, 'rev-parse', 'HEAD').decode().strip()
    index = (project / '.git/index').read_bytes()
    (task / 'index.txt').write_text('second version\n')
    second = delivery.publish(svc, work, workspace_id, context={})
    assert second['status'] == 'written'
    assert (project / 'index.txt').read_text() == 'second version\n'
    assert (project / 'user.txt').read_text() == 'user contribution'
    assert (project / '.git/index').read_bytes() == index
    assert _git(project, 'rev-parse', 'HEAD').decode().strip() == head
    _git(project, 'merge-base', '--is-ancestor', first['result_commit'], second['result_commit'])
    assert _git(task, 'show', 'HEAD:user.txt').decode() == 'user contribution'
    assert _git(project, 'merge-base', '--is-ancestor', head, second['result_commit'], check=False, return_process=True).returncode == 1
    repeated = delivery.publish(svc, work, workspace_id, context={})
    assert repeated['result_commit'] == second['result_commit']
    assert repeated['written_files'] == []


def test_merge_sources_without_task_receipts_or_validation_ids(delivery_setup):
    svc, work, alloc, project, _ = delivery_setup
    first_id, second_id = alloc('first'), alloc('second')
    first = Path(svc.prepare(first_id, work=work)['root_path'])
    second = Path(svc.prepare(second_id, work=work)['root_path'])
    (first / 'home.txt').write_text('home')
    (second / 'events.txt').write_text('events')
    result = delivery.publish(svc, work, first_id, context={}, source_workspace_ids=[second_id])
    assert result['status'] == 'written'
    assert set(result['written_files']) == {'home.txt', 'events.txt'}
    assert (first / 'events.txt').read_text() == 'events'
    assert not (second / 'home.txt').exists()
    assert (project / 'home.txt').read_text() == 'home'
    assert (project / 'events.txt').read_text() == 'events'
    with svc.store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM delegation_receipts').fetchone()[0] == 0


def test_source_conflict_is_resolved_in_the_current_workspace(delivery_setup):
    svc, work, alloc, project, _ = delivery_setup
    first_id, second_id = alloc('first'), alloc('second')
    first = Path(svc.prepare(first_id, work=work)['root_path'])
    second = Path(svc.prepare(second_id, work=work)['root_path'])
    (first / 'index.txt').write_text('first\n')
    (second / 'index.txt').write_text('second\n')
    result = delivery.publish(svc, work, first_id, context={}, source_workspace_ids=[second_id])
    assert result['status'] == 'conflict'
    assert (project / 'index.txt').read_bytes() == b'original\r\n'
    assert '<<<<<<<' in (first / 'index.txt').read_text()
    (first / 'index.txt').write_text('first and second\n')
    _git(first, 'add', 'index.txt')
    result = delivery.publish(svc, work, first_id, context={}, source_workspace_ids=[second_id])
    assert result['status'] == 'written'
    assert (project / 'index.txt').read_text() == 'first and second\n'
    _git(project, 'merge-base', '--is-ancestor', _git(second, 'rev-parse', 'HEAD').decode().strip(), result['result_commit'])


def test_binary_add_change_delete_rename_and_unicode_paths(delivery_setup):
    svc, work, alloc, project, _ = delivery_setup
    (project / 'change.bin').write_bytes(b'\0old\xff')
    (project / 'remove.bin').write_bytes(b'\0remove\xff')
    (project / 'rename.bin').write_bytes(b'\0rename\xff')
    _git(project, 'add', '.')
    _git(project, 'commit', '-m', 'binary input')
    index = (project / '.git/index').read_bytes()
    workspace_id = alloc()
    task = Path(svc.prepare(workspace_id, work=work)['root_path'])
    (task / 'change.bin').write_bytes(b'\0new\xfe')
    (task / 'remove.bin').unlink()
    (task / 'rename.bin').rename(task / '中文 空格.bin')
    (task / 'new.bin').write_bytes(b'\0new-file\xfd')
    result = delivery.publish(svc, work, workspace_id, context={'office_employee_id': 'writer', 'run_id': 'current'})
    assert result['status'] == 'written'
    assert (project / 'change.bin').read_bytes() == b'\0new\xfe'
    assert not (project / 'remove.bin').exists()
    assert not (project / 'rename.bin').exists()
    assert (project / '中文 空格.bin').read_bytes() == b'\0rename\xff'
    assert (project / 'new.bin').read_bytes() == b'\0new-file\xfd'
    assert (project / '.git/index').read_bytes() == index


def test_untracked_filename_collision_does_not_overwrite_user(delivery_setup):
    svc, work, alloc, project, _ = delivery_setup
    workspace_id = alloc()
    task = Path(svc.prepare(workspace_id, work=work)['root_path'])
    (task / 'new.txt').write_text('employee\n')
    (project / 'new.txt').write_text('user\n')
    result = delivery.publish(svc, work, workspace_id, context={})
    assert result['status'] == 'conflict'
    assert 'new.txt' in result['conflicts']
    assert (project / 'new.txt').read_text() == 'user\n'


def test_publication_does_not_finish_or_require_idle_processes(delivery_setup, monkeypatch):
    svc, work, alloc, project, _ = delivery_setup
    workspace_id = alloc()
    task = Path(svc.prepare(workspace_id, work=work)['root_path'])
    (task / 'index.txt').write_text('ready')
    value = svc.get(workspace_id)
    value['live_execution_ids'] = ['preview']
    with svc.store._connect() as db:
        svc._save(db, value)
    monkeypatch.setattr(terminal.manager_for(svc.store), 'finish_run', lambda *args, **kwargs: pytest.fail('Publication must not stop processes'))
    assert delivery.publish(svc, work, workspace_id, context={})['status'] == 'written'
    assert (project / 'index.txt').read_text() == 'ready'


def test_edit_only_tool_can_deliver_without_terminal_validation(delivery_setup, monkeypatch):
    from types import SimpleNamespace
    from deerflow.office.delivery_tool import publish_task_result
    svc, work, alloc, project, _ = delivery_setup
    workspace_id = alloc()
    value = svc.prepare(workspace_id, work=work)
    (Path(value['root_path']) / 'index.txt').write_text('edited without terminal')
    monkeypatch.setattr('deerflow.office.execution_workspace.execution_environment', lambda context, **kwargs: (svc, work, value))
    result = publish_task_result.func(SimpleNamespace(context={}, tool_call_id='publish', state={}))
    assert result.status == 'success'
    output = json.loads(result.content)
    assert output['written_files'] == ['index.txt']
    assert 'validation' not in result.content
    assert (project / 'index.txt').read_text() == 'edited without terminal'


def test_publication_rejects_other_project_sources(delivery_setup, tmp_path):
    svc, work, alloc, project, _ = delivery_setup
    workspace_id = alloc()
    other_project = tmp_path / 'other'
    other_project.mkdir()
    other_work = WorkIdentity(work.user_id, 'different-project', 'thread', 'work')
    other_id = svc.allocate(other_work, employee_id='other', owner_key='other', project_path=other_project)['workspace_id']
    with pytest.raises(ValueError, match='user/project'):
        delivery.publish(svc, work, workspace_id, context={}, source_workspace_ids=[other_id])
    assert (project / 'index.txt').read_bytes() == b'original\r\n'


def test_overlapping_user_change_during_approval_returns_real_conflict(delivery_setup, monkeypatch):
    svc, work, alloc, project, setting = delivery_setup
    setting.write_text('terminal_access_mode: approval\n')
    workspace_id = alloc()
    task = Path(svc.prepare(workspace_id, work=work)['root_path'])
    (task / 'index.txt').write_text('employee\n')
    def approve_after_user_edit(**kwargs):
        (project / 'index.txt').write_text('user edit while waiting\n')
    monkeypatch.setattr(terminal.manager_for(svc.store).approvals, 'wait', approve_after_user_edit)
    result = delivery.publish(svc, work, workspace_id, context={'office_employee_id': 'writer', 'run_id': 'current'})
    assert result['status'] == 'conflict'
    assert (project / 'index.txt').read_text() == 'user edit while waiting\n'
    assert '<<<<<<<' in (task / 'index.txt').read_text()


def test_native_merge_failure_does_not_copy_files_as_fallback(delivery_setup, monkeypatch):
    import subprocess
    svc, work, alloc, project, _ = delivery_setup
    workspace_id = alloc()
    task = Path(svc.prepare(workspace_id, work=work)['root_path'])
    (task / 'index.txt').write_text('employee\n')
    actual = delivery._git
    def failed_merge(root, *args, **kwargs):
        if 'merge' in args:
            return subprocess.CompletedProcess(args, 2, stdout=b'', stderr=b'merge failed')
        return actual(root, *args, **kwargs)
    monkeypatch.setattr(delivery, '_git', failed_merge)
    with pytest.raises(ValueError, match='Git merge failed'):
        delivery.publish(svc, work, workspace_id, context={})
    assert (project / 'index.txt').read_bytes() == b'original\r\n'
    assert (task / 'index.txt').read_text() == 'employee\n'


def test_binary_conflict_stays_out_of_user_project_until_resolution(delivery_setup):
    svc, work, alloc, project, _ = delivery_setup
    (project / 'image.bin').write_bytes(b'\0initial')
    workspace_id = alloc()
    task = Path(svc.prepare(workspace_id, work=work)['root_path'])
    (task / 'image.bin').write_bytes(b'\0employee')
    (project / 'image.bin').write_bytes(b'\0user')
    result = delivery.publish(svc, work, workspace_id, context={})
    assert result['status'] == 'conflict'
    assert 'image.bin' in result['conflicts']
    assert (project / 'image.bin').read_bytes() == b'\0user'
    (task / 'image.bin').write_bytes(b'\0resolved')
    _git(task, 'add', 'image.bin')
    assert delivery.publish(svc, work, workspace_id, context={})['status'] == 'written'
    assert (project / 'image.bin').read_bytes() == b'\0resolved'
