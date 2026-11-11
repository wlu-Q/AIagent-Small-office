"""Native worktree merges and file-only writeback to the bound project."""
from pathlib import Path

from deerflow.office.execution_workspace import (
    _digest, _git, _project_snapshot, _secret_or_dependency,
    unmerged_paths, validate_tree, working_git_options,
)


def _changed_paths(root, before, after):
    return list(filter(None, _git(root, 'diff', '--name-only', '--no-renames', '-z', before, after, '--').decode().split('\0')))


def _patch(root, before, after):
    return _git(root, 'diff', '--binary', '--full-index', '--no-renames', '--no-ext-diff', '--no-textconv',
                '--src-prefix=a/', '--dst-prefix=b/', before, after, '--')


def _apply_tree(root, before, after):
    patch = _patch(root, before, after)
    if not patch:
        return
    for name in _changed_paths(root, before, after):
        path = root / name
        if (not path.resolve().is_relative_to(root) or _secret_or_dependency(name)
                or any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents))):
            raise ValueError('Writeback refuses symbolic links, junctions or protected paths')
    result = _git(root, *working_git_options(root), 'apply', '--binary', '--whitespace=nowarn',
                  data=patch, check=False, return_process=True)
    if result.returncode:
        raise ValueError('Git apply failed: ' + result.stderr.decode('utf-8', errors='replace')[:1500])


def _merge(root, commit):
    validate_tree(root, commit)
    result = _git(root, *working_git_options(root), 'merge', '--no-edit', '--no-gpg-sign', '--no-verify-signatures',
                  commit, check=False, return_process=True)
    conflicts = unmerged_paths(root)
    if result.returncode and not conflicts:
        raise ValueError('Git merge failed: ' + result.stderr.decode('utf-8', errors='replace')[:1500])
    return conflicts


def _conflict(value, paths):
    return dict(workspace_id=value['workspace_id'], status='conflict', conflicts=paths, written_files=[])


def _proposal(service, work, value):
    source = _project_snapshot(Path(value['project_path']), user_id=service.user_id, project_id=work.project_id)
    root = Path(value['root_path'])
    conflicts = _merge(root, source['baseline_commit'])
    commit = _git(root, 'rev-parse', 'HEAD').decode().strip()
    return source, commit, conflicts


def _deliver(value, source, commit, context):
    abort = context.get('abort_event')
    if abort and abort.is_set():
        raise PermissionError('Writeback cancelled')
    root = Path(value['project_path'])
    validate_tree(root, commit)
    changed = _changed_paths(root, source['baseline_commit'], commit)
    _apply_tree(root, source['baseline_commit'], commit)
    _git(root, 'update-ref', source['baseline_ref'], commit, source['baseline_commit'])
    return dict(workspace_id=value['workspace_id'], status='written' if changed else 'unchanged',
                result_commit=commit, written_files=changed, conflicts=[])


def publish(service, work, workspace_id, *, context, source_workspace_ids=()):
    from deerflow.office.execution_approvals import access_mode
    from deerflow.office.terminal import manager_for

    value = service.prepare(workspace_id, work=work, context=context)
    for source_id in source_workspace_ids:
        source = service.get(source_id, work=work)
        if source['project_path'] != value['project_path']:
            raise ValueError('Source workspace belongs to a different bound project')
        service.prepare(source_id, work=work, context=context)
    root = Path(value['project_path'])
    abort = context.get('abort_event')
    if abort and abort.is_set():
        raise PermissionError('Writeback cancelled')
    with service.store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        value = service.get(workspace_id, work=work)
        sources = [service.get(source_id, work=work) for source_id in source_workspace_ids]
        for candidate in [value, *sources]:
            conflicts = unmerged_paths(Path(candidate['root_path']))
            if conflicts:
                return _conflict(candidate, conflicts)
        service._snapshot(value)
        for other in sources:
            saved = service._snapshot(other)
            conflicts = _merge(Path(value['root_path']), saved['result_commit'])
            if conflicts:
                return _conflict(value, conflicts)
        source, commit, conflicts = _proposal(service, work, value)
        if conflicts:
            return _conflict(value, conflicts)
        patch = _patch(root, source['baseline_commit'], commit)
        if not patch or access_mode(service.store) != 'approval':
            return _deliver(value, source, commit, context)
        changed = _changed_paths(root, source['baseline_commit'], commit)
    request_hash = _digest([workspace_id, str(root), patch.hex()])
    manager_for(service.store).approvals.wait(
        execution_id=f'publish:{workspace_id}:{request_hash}', context=context,
        employee_id=context.get('agent_name') or context['office_employee_id'],
        command='写回工作区改动到绑定项目：\n' + '\n'.join(changed), cwd=str(root),
        request_hash=request_hash, cancelled=lambda: bool(abort and abort.is_set()))
    with service.store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        value = service.get(workspace_id, work=work)
        conflicts = unmerged_paths(Path(value['root_path']))
        if conflicts:
            return _conflict(value, conflicts)
        service._snapshot(value)
        source, commit, conflicts = _proposal(service, work, value)
        if conflicts:
            return _conflict(value, conflicts)
        if _patch(root, source['baseline_commit'], commit) != patch:
            raise ValueError('Writeback changes differ from the approved patch; request approval for the updated changes')
        return _deliver(value, source, commit, context)
