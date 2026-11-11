"""Real Git invariants: no mocked staging area, checkout or snapshot contents."""
import concurrent.futures
from pathlib import Path

import pytest

from deerflow.config.paths import Paths
from deerflow.office.execution_facts import WorkIdentity
from deerflow.office.execution_workspace import ExecutionWorkspaceService, REFERENCE_PREFIX, _git


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / '中文项目'
    root.mkdir()
    _git(root, 'init')
    (root / 'index.txt').write_bytes(b'original\r\n')
    (root / 'deleted.txt').write_text('delete me')
    (root / 'renamed.txt').write_text('rename me')
    (root / '.gitignore').write_text('.env\nnode_modules/\n')
    _git(root, 'add', '.')
    _git(root, 'commit', '-m', 'initial')
    return root


def service(tmp_path, root):
    owner = WorkIdentity('alice', 'project-test', 'thread-test', 'work-test')
    svc = ExecutionWorkspaceService(paths=Paths(str(tmp_path / 'data')), user_id='alice')
    def allocate(employee='a'):
        return svc.allocate(owner, employee_id=employee, owner_key=employee, project_path=root)['workspace_id']
    return svc, owner, allocate


def test_snapshot_preserves_real_index_and_all_dirty_file_kinds(tmp_path, repository):
    root = repository
    (root / 'index.txt').write_bytes(b'staged\r\n')
    _git(root, 'add', 'index.txt')
    (root / 'index.txt').write_bytes(b'unstaged\r\n')
    (root / 'new.txt').write_bytes('新文件'.encode())
    (root / 'deleted.txt').unlink()
    (root / 'renamed.txt').rename(root / 'new-name.txt')
    (root / '.env').write_text('secret')
    (root / 'node_modules').mkdir()
    (root / 'node_modules' / 'dep.txt').write_text('dependency')
    staged_blob = _git(root, 'show', ':index.txt')
    real_index = (root / '.git/index').read_bytes()
    status = _git(root, 'status', '--porcelain=v1', '-z')
    head = _git(root, 'rev-parse', 'HEAD')
    svc, work, alloc = service(tmp_path, root)
    wid = alloc()
    assert svc.get(wid)['root_path'] is None
    value = svc.prepare(wid, work=work)
    task = Path(value['root_path'])
    assert task == root / '.office-worktrees' / wid / 'tree'
    assert svc.task_directory(value) / 'outputs' == task.parent / 'outputs'
    assert (task / 'index.txt').read_bytes() == b'unstaged\r\n'
    assert (task / 'new.txt').read_bytes() == '新文件'.encode()
    assert not (task / 'deleted.txt').exists()
    assert not (task / 'renamed.txt').exists()
    assert (task / 'new-name.txt').read_text() == 'rename me'
    assert not (task / '.env').exists()
    assert not (task / 'node_modules').exists()
    assert (root / '.git/index').read_bytes() == real_index
    assert _git(root, 'status', '--porcelain=v1', '-z') == status
    assert _git(root, 'rev-parse', 'HEAD') == head
    assert _git(root, 'show', ':index.txt') == staged_blob
    assert (root / 'index.txt').read_bytes() == b'unstaged\r\n'


def test_parallel_same_source_then_resume(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    ids = [alloc('a'), alloc('b')]
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        values = list(pool.map(lambda wid: svc.prepare(wid, work=work), ids))
    assert values[0]['baseline_commit'] == values[1]['baseline_commit']
    assert values[0]['root_path'] != values[1]['root_path']
    for i, value in enumerate(values):
        (Path(value['root_path']) / 'index.txt').write_text(str(i))
        result = svc.snapshot(value['workspace_id'], work=work)
        assert result['result_commit'] != result['baseline_commit']
        assert 'integration_status' not in result
    assert (repository / 'index.txt').read_bytes() == b'original\r\n'
    recovered = ExecutionWorkspaceService(paths=svc.paths, user_id='alice')
    assert recovered.prepare(ids[0], work=work)['root_path'] == values[0]['root_path']
    assert (Path(recovered.get(ids[0])['root_path']) / 'index.txt').read_text() == '0'


def test_no_write_means_no_tree_and_reference_ownership(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    assert not (svc.paths.user_dir('alice') / 'execution-workspaces').exists()
    ref = f'{REFERENCE_PREFIX}{wid}/workspace/index.txt'
    with pytest.raises(ValueError, match='not been opened'):
        svc.resolve_reference(ref, work=work)
    assert not (svc.paths.user_dir('alice') / 'execution-workspaces').exists()
    svc.prepare(wid, work=work)
    ref = f'{REFERENCE_PREFIX}{wid}/workspace/index.txt'
    assert svc.resolve_reference(ref, work=work).read_bytes() == b'original\r\n'
    assert svc.resolve_reference(ref, work=WorkIdentity('alice', 'project-test', 'thread-other', 'work-other')).is_file()
    with pytest.raises(ValueError, match='project'):
        svc.resolve_reference(ref, work=WorkIdentity('alice', 'project-other', 'thread-other', 'work-other'))
    with pytest.raises(ValueError, match='user'):
        ExecutionWorkspaceService(paths=svc.paths, user_id='bob').resolve_reference(ref, work=work)
    for path in ('../outside', '.git', '../../tree/.git'):
        with pytest.raises(ValueError):
            svc.resolve_reference(f'{REFERENCE_PREFIX}{wid}/workspace/{path}', work=work)


def test_non_git_and_subdirectory_refuse_without_modifying_source(tmp_path, repository):
    plain = tmp_path / 'plain'
    plain.mkdir()
    for root in (plain, repository / 'subdir'):
        root.mkdir(exist_ok=True)
        svc, work, alloc = service(tmp_path / root.name, root)
        with pytest.raises(ValueError):
            svc.prepare(alloc(), work=work)
        assert not (root / '.git').exists()


def test_source_change_during_snapshot_is_detected(tmp_path, repository, monkeypatch):
    from deerflow.office import execution_workspace as module
    svc, work, alloc = service(tmp_path, repository)
    original = module._tree
    def race(root, files):
        tree = original(root, files)
        (repository / 'index.txt').write_text('edited by user')
        return tree
    monkeypatch.setattr(module, '_tree', race)
    with pytest.raises(ValueError, match='变化'):
        svc.prepare(alloc(), work=work)
    assert (repository / 'index.txt').read_text() == 'edited by user'


def test_raw_batch_objects_preserve_binary_unicode_and_skip_git_filters(tmp_path, repository):
    # The source is intentionally configured with a failing filter. Snapshot
    # reads/writes must use the actual bytes without invoking that command.
    _git(repository, 'config', 'filter.office.clean', 'exit 99')
    _git(repository, 'config', 'filter.office.smudge', 'exit 99')
    _git(repository, 'config', 'filter.office.required', 'true')
    (repository / '.gitattributes').write_text('* filter=office\n')
    payloads = {'空 格/空.txt': b'', '空 格/二进制.bin': bytes(range(256)) * 8,
                '换行.txt': b'CRLF\r\nLF\nno newline', '[draft] #1.txt': b'hello'}
    for name, content in payloads.items():
        target = repository / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(content)
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    root = Path(svc.prepare(wid, work=work)['root_path'])
    for name, content in payloads.items():
        assert (root / name).read_bytes() == content
    result = svc.snapshot(wid, work=work)
    assert result['result_commit'] == result['baseline_commit']
    assert svc.snapshot(wid, work=work)['result_commit'] == result['result_commit']


def test_missing_worktree_fails_closed_without_recreating_it(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    root = Path(svc.prepare(wid, work=work)['root_path'])
    preserved = root.with_name('preserved-tree')
    root.rename(preserved)
    with pytest.raises(ValueError, match='ownership/path'):
        svc.prepare(wid, work=work)
    with pytest.raises(ValueError, match='ownership/path'):
        svc.resolve_reference(f'{REFERENCE_PREFIX}{wid}/workspace/index.txt', work=work)
    assert not root.exists()
    assert (preserved / 'index.txt').read_bytes() == b'original\r\n'


def test_output_reference_is_scoped_and_rejects_replaced_directory(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    outputs = svc.task_directory(svc.get(wid)) / 'outputs'
    outputs.mkdir(parents=True)
    (outputs / 'report.md').write_text('task result')
    ref = f'{REFERENCE_PREFIX}{wid}/outputs/report.md'
    assert svc.resolve_reference(ref, work=work).read_text() == 'task result'
    for path in ('../tree/.git', '../../other/report.md', '.Git/config'):
        with pytest.raises(ValueError):
            svc.resolve_reference(f'{REFERENCE_PREFIX}{wid}/outputs/{path}', work=work)
    outputs.rename(outputs.with_name('preserved-outputs'))
    try:
        outputs.symlink_to(repository, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'OS does not permit test symlinks: {exc}')
    with pytest.raises(ValueError, match='ownership/path'):
        svc.resolve_reference(ref, work=work)


def test_unborn_repository_and_result_failure_keep_real_files(tmp_path):
    root = tmp_path / 'new-project'
    root.mkdir()
    _git(root, 'init')
    (root / 'README.md').write_bytes(b'not committed\r\n')
    svc, work, alloc = service(tmp_path, root)
    wid = alloc()
    value = svc.prepare(wid, work=work)
    task = Path(value['root_path'])
    assert value['source_head'] is None
    assert (task / 'README.md').read_bytes() == b'not committed\r\n'
    assert not (root / '.git/index').exists()
    (task / 'README.md').write_text('kept after failed snapshot')
    assert (task / 'README.md').read_text() == 'kept after failed snapshot'
    assert svc.snapshot(wid, work=work)['result_commit']
    assert 'integration_status' not in svc.get(wid, work=work)
    assert not _git(root, 'rev-parse', '--verify', 'HEAD', check=False).strip()


def test_new_workspace_does_not_snapshot_or_search_other_tasks(tmp_path, repository):
    from deerflow.office.execution_workspace import _capture, resolve_thread_data
    from deerflow.office.projects import OfficeProjectService
    from deerflow.sandbox.local.list_dir import list_dir
    from deerflow.sandbox.search import find_glob_matches, find_grep_matches
    from deerflow.sandbox.tools import _resolve_and_validate_user_data_path, validate_local_tool_path

    exclude = repository / '.git/info/exclude'
    original = exclude.read_bytes() + b'\nuser-local-exclusion\n'
    exclude.write_bytes(original)
    original_ignore = (repository / '.gitignore').read_bytes()
    svc, work, alloc = service(tmp_path, repository)
    first = svc.prepare(alloc('a'), work=work)
    (Path(first['root_path']) / 'secret-task.txt').write_text('private-task-content')
    second = svc.prepare(alloc('b'), work=work)
    assert not (Path(second['root_path']) / '.office-worktrees').exists()
    assert exclude.read_bytes() == original + b'/.office-worktrees/\n'
    assert (repository / '.gitignore').read_bytes() == original_ignore
    assert all('.office-worktrees' not in name for name in _capture(repository)[2])
    assert not any('.office-worktrees' in entry for entry in list_dir(str(repository), 10))
    matches, _ = find_glob_matches(repository, '**/*', include_dirs=True)
    assert not any('.office-worktrees' in entry for entry in matches)
    result = find_grep_matches(repository, 'private-task-content')
    matches = result.matches
    assert matches == []
    unprepared = alloc('reader')
    context = {'user_id': work.user_id, 'office_work': work.to_record(), 'office_project_id': work.project_id,
               'office_project_path': str(repository), 'office_execution_workspace': {'workspace_id': unprepared}}
    data = resolve_thread_data(context, {}, paths=svc.paths)
    assert Path(data['workspace_path']) == repository / '.office-worktrees' / unprepared / 'tree'
    assert Path(data['outputs_path']) == repository / '.office-worktrees' / unprepared / 'outputs'
    for readonly in (True, False):
        with pytest.raises(PermissionError):
            validate_local_tool_path(f'/mnt/user-data/workspace/.office-worktrees/{first["workspace_id"]}/tree/secret-task.txt', data, read_only=readonly)
    assert _resolve_and_validate_user_data_path('/mnt/user-data/outputs/report.md', data) == str(Path(data['outputs_path']) / 'report.md')
    with pytest.raises(ValueError):
        svc.resolve_reference(f'{REFERENCE_PREFIX}{unprepared}/workspace/.office-worktrees/{first["workspace_id"]}/tree/secret-task.txt', work=work)
    projects = OfficeProjectService(paths=svc.paths, user_id='alice')
    projects.bind(repository)
    with pytest.raises(ValueError, match='scoped'):
        projects.read_files([f'.office-worktrees/{first["workspace_id"]}/tree/secret-task.txt'])


@pytest.mark.parametrize('binding', ['', '.', 'relative-project'])
def test_binding_never_falls_back_to_process_directory(tmp_path, binding):
    svc, work, _ = service(tmp_path, tmp_path)
    with pytest.raises(ValueError, match='absolute'):
        svc.allocate(work, employee_id='a', owner_key='a', project_path=binding)


def test_deleted_binding_and_legacy_external_tree_fail_closed(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    value = svc.get(wid)
    legacy = svc.paths.user_dir('alice') / 'execution-workspaces' / wid / 'tree'
    legacy.mkdir(parents=True)
    (legacy / '.git').write_text('legacy metadata')
    with svc.store._connect() as db:
        svc._save(db, {**value, 'root_path': str(legacy)})
    with pytest.raises(ValueError, match='ownership/path'):
        svc.prepare(wid, work=work)
    assert not (repository / '.office-worktrees').exists()
    assert (legacy / '.git').read_text() == 'legacy metadata'
    with svc.store._connect() as db:
        svc._save(db, value)
    repository.rename(repository.with_name('renamed-project'))
    with pytest.raises(ValueError, match='Bound project'):
        svc.get(wid)
    assert not repository.exists()


def test_tracked_management_directory_is_not_hidden_or_removed(tmp_path, repository):
    managed = repository / '.office-worktrees'
    managed.mkdir()
    (managed / 'user.txt').write_text('keep user file')
    _git(repository, 'add', '.office-worktrees/user.txt')
    index = (repository / '.git/index').read_bytes()
    svc, _, alloc = service(tmp_path, repository)
    with pytest.raises(ValueError, match='tracked'):
        alloc()
    assert (repository / '.git/index').read_bytes() == index
    assert (managed / 'user.txt').read_text() == 'keep user file'
    assert not (svc.paths.user_dir('alice') / 'execution-workspaces').exists()


def test_continue_uses_same_workspace_across_work_thread_and_employee(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    value = svc.prepare(wid, work=work)
    root = Path(value['root_path'])
    (root / 'index.txt').write_text('continue this work')
    (root / 'node_modules').mkdir()
    (root / 'node_modules/installed.txt').write_text('keep installation')
    next_work = WorkIdentity('alice', work.project_id, 'another-thread', 'next-work')
    recovered = ExecutionWorkspaceService(paths=svc.paths, user_id='alice')
    selected = recovered.allocate(next_work, employee_id='b', owner_key='direct:b', project_path=repository,
                                  mode='continue', source_workspace_id=wid)
    assert selected == {'workspace_id': wid}
    assert recovered.prepare(wid, work=next_work)['root_path'] == str(root)
    assert (root / 'index.txt').read_text() == 'continue this work'
    assert (root / 'node_modules/installed.txt').read_text() == 'keep installation'
    with svc.store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM execution_workspaces').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM workspace_uses').fetchone()[0] == 2
    assert recovered.allocate(next_work, employee_id='b', owner_key='direct:b', project_path=repository,
                              mode='continue', source_workspace_id=wid) == selected
    with pytest.raises(ValueError, match='different selection'):
        recovered.allocate(next_work, employee_id='b', owner_key='direct:b', project_path=repository)


def test_forks_share_actual_scaffold_ancestry_and_merge_without_false_conflicts(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    source_id = alloc()
    source = svc.prepare(source_id, work=work)
    source_root = Path(source['root_path'])
    (source_root / 'App.vue').write_text('<template>scaffold</template>')
    (source_root / 'node_modules').mkdir()
    (source_root / 'node_modules/installed.txt').write_text('do not copy dependencies')
    ids = [svc.allocate(work, employee_id=employee, owner_key=employee, project_path=repository,
                        mode='fork', source_workspace_id=source_id)['workspace_id'] for employee in ('b', 'c')]
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        values = list(pool.map(lambda wid: svc.prepare(wid, work=work), ids))
    assert values[0]['baseline_commit'] == values[1]['baseline_commit']
    for value, name in zip(values, ('Home.vue', 'Events.vue'), strict=True):
        root = Path(value['root_path'])
        assert (root / 'App.vue').read_text() == '<template>scaffold</template>'
        assert not (root / 'node_modules').exists()
        (root / name).write_text(name)
    first, second = [svc.snapshot(wid, work=work) for wid in ids]
    base = _git(repository, 'merge-base', first['result_commit'], second['result_commit']).decode().strip()
    assert base == values[0]['baseline_commit']
    merged_tree = _git(repository, 'merge-tree', '--write-tree', first['result_commit'], second['result_commit']).decode().strip()
    assert _git(repository, 'show', f'{merged_tree}:Home.vue') == b'Home.vue'
    assert _git(repository, 'show', f'{merged_tree}:Events.vue') == b'Events.vue'
    assert not (repository / 'App.vue').exists()


def test_snapshots_follow_actual_git_commits_and_do_not_repeat_base_parent(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    value = svc.prepare(wid, work=work)
    root = Path(value['root_path'])
    source_head = _git(repository, 'rev-parse', 'HEAD')
    source_index = (repository / '.git/index').read_bytes()
    (root / 'index.txt').write_text('first change')
    first = svc.snapshot(wid, work=work)['result_commit']
    assert _git(root, 'rev-parse', f'{first}^').decode().strip() == value['checkout_commit']
    (root / 'native.txt').write_text('employee git commit')
    _git(root, 'add', '.')
    _git(root, 'commit', '-m', 'employee commit')
    native = _git(root, 'rev-parse', 'HEAD').decode().strip()
    (root / 'index.txt').write_text('second change')
    second = svc.snapshot(wid, work=work)['result_commit']
    assert _git(root, 'rev-parse', f'{second}^').decode().strip() == native
    assert _git(root, 'rev-parse', f'{native}^').decode().strip() == first
    assert svc.snapshot(wid, work=work)['result_commit'] == second
    assert not _git(root, 'status', '--porcelain').strip()
    assert _git(repository, 'rev-parse', 'HEAD') == source_head
    assert (repository / '.git/index').read_bytes() == source_index


def test_workspace_lookup_does_not_snapshot_or_store_delivery_state(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    value = svc.prepare(wid, work=work)
    root = Path(value['root_path'])
    before = _git(root, 'rev-parse', 'HEAD')
    (root / 'index.txt').write_text('unfinished edit')
    result = svc.get(wid, work=work)
    assert 'result_commit' not in result
    assert _git(root, 'rev-parse', 'HEAD') == before
    assert 'integration_status' not in result
    assert (root / 'index.txt').read_text() == 'unfinished edit'
    assert _git(root, 'rev-parse', 'HEAD') == before


def test_reads_and_edits_use_the_same_prepared_directory(tmp_path, repository, monkeypatch):
    from types import SimpleNamespace
    from deerflow.office.execution_workspace import resolve_thread_data
    from deerflow.sandbox.tools import get_thread_data, ensure_thread_directories_exist
    svc, work, alloc = service(tmp_path, repository)
    wid = alloc()
    context = dict(user_id='alice', office_work=work.to_record(), office_project_id=work.project_id,
                   office_project_path=str(repository), office_execution_workspace={'workspace_id': wid})
    first = resolve_thread_data(context, {}, paths=svc.paths)
    root = Path(first['workspace_path'])
    assert root != repository
    assert not root.exists()
    monkeypatch.setattr('deerflow.office.execution_workspace.get_paths', lambda: svc.paths)
    runtime = SimpleNamespace(context=context, state={'thread_data': first, 'sandbox': {'sandbox_id': 'local:stage1'}})
    assert get_thread_data(runtime)['workspace_path'] == str(root)
    assert not root.exists()
    ensure_thread_directories_exist(runtime)
    (repository / 'index.txt').write_text('user changed source after task started')
    assert (root / 'index.txt').read_bytes() == b'original\r\n'
    (root / 'index.txt').write_text('employee edit')
    assert resolve_thread_data(context, {}, paths=svc.paths)['workspace_path'] == str(root)
    assert (repository / 'index.txt').read_text() == 'user changed source after task started'


def test_new_workspaces_use_current_source_not_an_old_batch(tmp_path, repository):
    svc, work, alloc = service(tmp_path, repository)
    first = svc.prepare(alloc('a'), work=work)
    (repository / 'index.txt').write_text('new source')
    second = svc.prepare(alloc('b'), work=work)
    assert first['baseline_commit'] != second['baseline_commit']
    assert (Path(first['root_path']) / 'index.txt').read_bytes() == b'original\r\n'
    assert (Path(second['root_path']) / 'index.txt').read_text() == 'new source'
    with svc.store._connect() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='execution_baselines'").fetchone() is None


@pytest.mark.parametrize('mode,source', [('unknown', None), ('new', 'wrong'), ('continue', None), ('fork', None)])
def test_invalid_workspace_selection_is_not_reinterpreted(tmp_path, repository, mode, source):
    svc, work, _ = service(tmp_path, repository)
    with pytest.raises(ValueError, match='Choose'):
        svc.allocate(work, employee_id='a', owner_key='a', project_path=repository,
                     mode=mode, source_workspace_id=source)


def test_read_only_context_without_workspace_does_not_create_one(tmp_path, repository):
    from deerflow.office.execution_workspace import resolve_thread_data
    data = dict(workspace_path=str(repository), thread_id='read-only')
    assert resolve_thread_data({'office_project_path': str(repository)}, data) == data
    assert not (repository / '.office-worktrees').exists()
