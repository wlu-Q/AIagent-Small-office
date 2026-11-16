"""Explicit project/worktree environments and reusable Git resources."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from deerflow.config.paths import get_paths
from deerflow.office.execution_facts import ExecutionReceiptStore, WorkIdentity

WORKSPACE_CONTEXT_KEY = 'office_execution_workspace'
REFERENCE_PREFIX = '/mnt/office-workspaces/'
MANAGED_DIRECTORY = '.office-worktrees'
_MAX_BYTES = 200 * 1024 * 1024
_MAX_FILES = 20000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _git(root, *args, data=None, env=None, check=True, return_process=False):
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    clean_env.update({'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
                      'GIT_AUTHOR_NAME': 'Small Office', 'GIT_AUTHOR_EMAIL': 'office@localhost',
                      'GIT_COMMITTER_NAME': 'Small Office', 'GIT_COMMITTER_EMAIL': 'office@localhost'})
    clean_env.update(env or {})
    result = subprocess.run(['git', '-c', 'core.hooksPath=NUL' if os.name == 'nt' else 'core.hooksPath=/dev/null',
                             '-c', 'core.fsmonitor=false', '-C', str(root), *args],
                            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=clean_env, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if check and result.returncode:
        raise ValueError(f'Git operation failed (exit {result.returncode}): '
                         + result.stderr.decode('utf-8', errors='replace')[:1500])
    return result if return_process else result.stdout


def working_git_options(root):
    options = ['-c', 'core.autocrlf=false', '-c', 'commit.gpgSign=false',
               '-c', 'merge.verifySignatures=false', '-c', 'rerere.enabled=false']
    for key in _git(root, 'config', '--name-only', '--get-regexp',
                    r'^(filter\..*\.(clean|smudge|process|required)|merge\..*\.driver)$',
                    check=False).decode().splitlines():
        options.extend(['-c', f'{key}=false' if key.endswith(('.required', '.driver')) else f'{key}='])
    return options


def unmerged_paths(root):
    entries = _git(root, 'ls-files', '--unmerged', '-z').split(b'\0')
    return sorted({entry.split(b'\t', 1)[1].decode('utf-8') for entry in entries if entry})


def validate_tree(root, ref):
    for entry in filter(None, _git(root, 'ls-tree', '-r', '-z', ref).split(b'\0')):
        meta, raw_name = entry.split(b'\t', 1)
        mode, kind, _ = meta.split()
        name = raw_name.decode('utf-8')
        path = Path(name)
        if (mode not in {b'100644', b'100755'} or kind != b'blob' or path.is_absolute()
                or '..' in path.parts or ':' in name or _secret_or_dependency(name)):
            raise ValueError(f'Result contains an unsupported or protected path: {name}')


def _secret_or_dependency(name):
    parts = Path(name).parts
    return any(p.lower() in {'node_modules', '.venv', 'venv', '.git', MANAGED_DIRECTORY} for p in parts) or any(
        p.lower() == '.env' or (p.lower().startswith('.env.') and p.lower() not in {'.env.example', '.env.sample'})
        or p.lower().endswith(('.pem', '.key', '.p12', '.pfx')) or p.lower() in {'id_rsa', 'id_ed25519'} for p in parts)


def _bound_project(value):
    root = Path(value or '')
    if not root.is_absolute() or not root.is_dir() or root.resolve(strict=True) != root:
        raise ValueError('Bound project must be an existing canonical absolute directory')
    if any(part.is_symlink() or part.is_junction() for part in (root, *root.parents)):
        raise ValueError('Bound project path changed')
    return root


def _exclude_managed_directory(root):
    top = _git(root, 'rev-parse', '--show-toplevel', check=False).decode().strip()
    if not top or Path(top).resolve() != root:
        return
    if _git(root, 'ls-files', '-z', '--', MANAGED_DIRECTORY):
        raise ValueError('Reserved .office-worktrees directory contains tracked files; relocate them explicitly')
    exclude = Path(_git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'info/exclude').decode().strip())
    if exclude.is_symlink() or exclude.is_junction() or exclude.parent.resolve() != exclude.parent:
        raise ValueError('Git local exclude path changed')
    existing = exclude.read_bytes() if exclude.exists() else b''
    rule = b'/.office-worktrees/'
    if rule not in existing.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open('ab') as stream:
            stream.write((b'\n' if existing and not existing.endswith(b'\n') else b'') + rule + b'\n')


def _repository(root):
    root = Path(root).resolve(strict=True)
    top = _git(root, 'rev-parse', '--show-toplevel', check=False).decode().strip()
    if not top:
        raise ValueError('当前项目不是 Git 仓库；创建独立工作区时需通过现有授权初始化 Git。')
    if Path(top).resolve() != root:
        raise ValueError('当前阶段只支持绑定 Git 仓库根目录；子目录绑定不会扩大为整个仓库。')
    if _git(root, 'config', '--bool', 'core.sparseCheckout', check=False).strip() == b'true':
        raise ValueError('Sparse checkout is not supported for task snapshots')
    return root


def _capture(root):
    head = _git(root, 'rev-parse', '--verify', 'HEAD', check=False).decode().strip() or None
    index_path = Path(_git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index').decode().strip())
    index = index_path.read_bytes() if index_path.exists() else b''
    stages = _git(root, 'ls-files', '--stage', '-z')
    modes = {}
    for entry in stages.split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1)
        mode, _, stage = meta.split()
        if stage != b'0':
            raise ValueError('Unmerged Git index; resolve and stage the conflict or abort the merge before saving')
        if _secret_or_dependency(name.decode('utf-8')):
            continue
        mode = mode.decode()
        if mode not in {'100644', '100755'}:
            raise ValueError(f'Current-file baseline does not support submodules or symlinks: {name.decode("utf-8")}')
        modes[name.decode('utf-8')] = mode
    names = sorted(set(filter(None, _git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z',
        '--', '.', f':(exclude,icase){MANAGED_DIRECTORY}/**').decode('utf-8').split('\0'))))
    files, total, excluded = {}, 0, []
    for name in names:
        if _secret_or_dependency(name):
            excluded.append(name)
            continue
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f'Current-file baseline contains an unsupported or escaping path: {name}')
        if len(files) >= _MAX_FILES:
            raise ValueError(f'Current-file baseline exceeds {_MAX_FILES} files; exclude generated files first')
        total += path.stat().st_size
        if total > _MAX_BYTES:
            raise ValueError('Current-file baseline exceeds 200 MiB; exclude generated files first')
        files[name] = (modes.get(name, '100644'), path.read_bytes())
    signature = _digest([head, hashlib.sha256(index).hexdigest(), stages.hex(), excluded,
                         [(n, m, hashlib.sha256(b).hexdigest()) for n, (m, b) in files.items()]])
    return head, index, files, signature, excluded


def _tree(root, files):
    # Empty private index: raw blobs preserve CRLF, encodings and staged/unstaged
    # distinctions without running clean/smudge filters or touching the real index.
    with tempfile.TemporaryDirectory(prefix='office-index-') as temp:
        env = {'GIT_INDEX_FILE': str(Path(temp) / 'index')}
        _git(root, 'read-tree', '--empty', env=env)
        entries = []
        blob_paths = []
        for index, (_, content) in enumerate(files.values()):
            blob = Path(temp) / f'blob-{index}'
            blob.write_bytes(content)
            blob_paths.append(json.dumps(blob.as_posix(), ensure_ascii=False))
        oids = (_git(root, 'hash-object', '-w', '--no-filters', '--stdin-paths',
                     data=('\n'.join(blob_paths) + '\n').encode('utf-8')).splitlines() if blob_paths else [])
        if len(oids) != len(files):
            raise ValueError('Git did not store every snapshot file')
        for (name, (mode, _)), oid in zip(files.items(), oids, strict=True):
            entries.append(mode.encode() + b' ' + oid + b'\t' + name.encode('utf-8') + b'\0')
        if entries:
            _git(root, 'update-index', '-z', '--index-info', env=env, data=b''.join(entries))
        return _git(root, 'write-tree', env=env).decode().strip()


def _commit(root, tree, parent, message):
    parents = parent if isinstance(parent, (list, tuple)) else [parent] if parent else []
    return _git(root, 'commit-tree', tree, *[arg for commit in parents for arg in ('-p', commit)], data=message.encode()).decode().strip()


def _project_snapshot(root, *, user_id, project_id):
    before = _capture(root)
    tree = _tree(root, before[2])
    ref = f'refs/small-office/{_digest(user_id)[:16]}/sources/{_digest(project_id)[:16]}'
    prior = _git(root, 'rev-parse', '--verify', ref, check=False).decode().strip()
    head = before[0]
    previous = prior or head
    commit = (previous if previous and _git(root, 'rev-parse', f'{previous}^{{tree}}').decode().strip() == tree
              else _commit(root, tree, previous, 'Small Office current project files'))
    if _capture(root)[3] != before[3]:
        raise ValueError('项目在快照期间发生变化，请重试；原文件和暂存区未被修改。')
    _git(root, 'update-ref', ref, commit)
    return dict(baseline_commit=commit, baseline_ref=ref, source_head=head,
                source_signature=before[3], excluded_files=before[4])


class ExecutionWorkspaceService:
    def __init__(self, *, paths=None, user_id):
        self.paths = paths or get_paths()
        self.store = ExecutionReceiptStore(paths=self.paths, user_id=user_id)
        self.user_id = user_id
        with self.store._connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(execution_workspaces)')}
        if 'batch_key' in columns:
            raise ValueError('Run migrate_workspace_identity.py in maintenance mode before using workspaces')

    def allocate(self, work, *, employee_id, project_path, owner_key, mode='new',
                 source_workspace_id=None):
        if work.user_id != self.user_id or not work.project_id or not employee_id or not owner_key:
            raise ValueError('Workspace use identity is incomplete')
        if mode not in {'new', 'continue', 'fork'} or (mode == 'new') != (source_workspace_id is None):
            raise ValueError('Choose new, or continue/fork with a source workspace')
        project_path = str(_bound_project(project_path))
        use_key = _digest([work.to_record(), employee_id, owner_key])
        selection = dict(mode=mode, source_workspace_id=source_workspace_id, project_path=project_path)
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT workspace_id,selection_json FROM workspace_uses WHERE use_key=?',
                             (use_key,)).fetchone()
            if row:
                if json.loads(row['selection_json']) != selection:
                    raise ValueError('Workspace use was replayed with a different selection')
                self.get(row['workspace_id'], work=work)
                return {'workspace_id': row['workspace_id']}
            source = self.get(source_workspace_id, work=work) if source_workspace_id else None
            if source and source['project_path'] != project_path:
                raise ValueError('Source workspace project binding differs')
            if mode == 'continue':
                workspace_id = source['workspace_id']
            else:
                workspace_id = uuid4().hex
                value = dict(work.to_record(), workspace_id=workspace_id, employee_id=employee_id,
                             owner_key=owner_key, project_path=project_path, root_path=None,
                             baseline_ref=None, baseline_commit=None,
                             receipt_key=None if owner_key.startswith('direct:') else owner_key,
                             source_workspace_id=source_workspace_id)
                self.task_directory(value)
                _exclude_managed_directory(Path(project_path))
                db.execute('INSERT INTO execution_workspaces(workspace_id,user_id,project_id,state_json) VALUES (?,?,?,?)',
                           (workspace_id, self.user_id, work.project_id, json.dumps(value)))
            db.execute('INSERT INTO workspace_uses VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (use_key, workspace_id, self.user_id, work.project_id, work.thread_id, work.work_id,
                        employee_id, owner_key, json.dumps(selection), None))
        return {'workspace_id': workspace_id}

    def get(self, workspace_id, *, work=None):
        with self.store._connect() as db:
            row = db.execute('SELECT state_json FROM execution_workspaces WHERE workspace_id=? AND user_id=?',
                             (workspace_id, self.user_id)).fetchone()
        if row is None:
            raise ValueError('Task workspace does not belong to this user')
        value = json.loads(row[0])
        if work is not None and (work.user_id != self.user_id or work.project_id != value['project_id']):
            raise ValueError('Workspace does not belong to this user/project')
        parent = self.task_directory(value)
        if value['root_path']:
            root = Path(value['root_path'])
            expected = parent / 'tree'
            if root != expected or root.resolve() != expected or not (root / '.git').is_file():
                raise ValueError('Task worktree ownership/path changed; refusing file access')
        return value

    @staticmethod
    def task_directory(value):
        root = _bound_project(value['project_path'])
        workspace_id = value['workspace_id']
        if not isinstance(workspace_id, str) or not re.fullmatch(r'[0-9a-f]{32}', workspace_id):
            raise ValueError('Invalid task workspace identity')
        parent = root / MANAGED_DIRECTORY / workspace_id
        for path in (parent.parent, parent, parent / 'tree', parent / 'outputs'):
            if (path.resolve() != path or path.is_symlink() or path.is_junction()
                    or (path.exists() and not path.is_dir())):
                raise ValueError('Task directory ownership/path changed')
        return parent

    @staticmethod
    def _save(db, value):
        db.execute('UPDATE execution_workspaces SET state_json=? WHERE workspace_id=?',
                   (json.dumps(value), value['workspace_id']))

    def prepare(self, workspace_id, *, work, context=None):
        value = self.get(workspace_id, work=work)
        if value['root_path']:
            return value
        if context is not None:
            self._initialize_repository(value, work, context)
        if value.get('source_workspace_id'):
            self.prepare(value['source_workspace_id'], work=work, context=context)
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            value = self.get(workspace_id, work=work)
            if value['root_path']:
                return value
            root = _repository(value['project_path'])
            _exclude_managed_directory(root)
            if value.get('source_workspace_id'):
                source = self._snapshot(self.get(value['source_workspace_id'], work=work))
                commit = source['result_commit']
                baseline = dict(baseline_commit=commit, baseline_ref=source['result_ref'],
                                source_head=commit, excluded_files=[])
            else:
                baseline = _project_snapshot(root, user_id=self.user_id, project_id=work.project_id)
            parent = self.task_directory(value)
            target = parent / 'tree'
            if target.exists():
                raise ValueError('An unfinished workspace creation exists; inspect it before retrying')
            parent.mkdir(parents=True, exist_ok=True)
            checkout = baseline.get('checkout_commit') or baseline['baseline_commit']
            _git(root, *working_git_options(root), 'worktree', 'add', '--detach', str(target), checkout)
            (parent / 'outputs').mkdir(exist_ok=True)
            value.update(baseline, checkout_commit=checkout, root_path=str(target))
            self._save(db, value)
            return value

    def _initialize_repository(self, value, work, context):
        root = Path(value['project_path'])
        if root.resolve(strict=True) != root or root.is_symlink() or root.is_junction():
            raise ValueError('Bound project path changed')
        if _git(root, 'rev-parse', '--show-toplevel', check=False).strip():
            return  # _repository performs supported-repository validation.
        from deerflow.office.execution_approvals import access_mode
        from deerflow.office.terminal import manager_for
        request_hash = _digest([work.to_record(), str(root), 'git init --template='])
        abort = context.get('abort_event')
        if access_mode(self.store) == 'approval':
            manager_for(self.store).approvals.wait(execution_id=f'init:{request_hash}', context=context,
                employee_id=context.get('agent_name') or context['office_employee_id'], command='git init --template=', cwd=str(root),
                request_hash=request_hash, cancelled=lambda: bool(abort and abort.is_set()))
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if abort and abort.is_set():
                raise PermissionError('Repository initialization cancelled')
            if root.resolve(strict=True) != root or root.is_junction() or root.is_symlink():
                raise ValueError('Bound project path changed while awaiting approval')
            if not _git(root, 'rev-parse', '--show-toplevel', check=False).strip():
                _git(root, 'init', '--template=')
            _repository(root)

    def _snapshot(self, value):
        if not value['root_path']:
            raise ValueError('Workspace has not been opened; no version to snapshot')
        root = Path(value['root_path'])
        before = _capture(root)
        tracked = _git(root, 'ls-files', '-z').decode().split('\0')
        paths = sorted(set(before[2]) | {name for name in tracked if name and not _secret_or_dependency(name)})
        options = working_git_options(root)
        if paths:
            _git(root, *options, '--literal-pathspecs', 'add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul',
                 data=b''.join(name.encode('utf-8') + b'\0' for name in paths))
        tree = _git(root, *options, 'write-tree').decode().strip()
        validate_tree(root, tree)
        merging = _git(root, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).strip()
        if merging or tree != _git(root, 'rev-parse', 'HEAD^{tree}').decode().strip():
            _git(root, *options, 'commit', '--no-gpg-sign', '-m', 'Small Office workspace changes')
        commit = _git(root, 'rev-parse', 'HEAD').decode().strip()
        ref = f"refs/small-office/{_digest(self.user_id)[:16]}/workspaces/{value['workspace_id']}"
        _git(root, 'update-ref', ref, commit)
        return {**value, 'result_commit': commit, 'result_ref': ref}

    def snapshot(self, workspace_id, *, work):
        self.prepare(workspace_id, work=work)
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._snapshot(self.get(workspace_id, work=work))

    def resolve_reference(self, path, *, work):
        raw = path.removeprefix(REFERENCE_PREFIX)
        wid, slash, rest = raw.partition('/')
        value = self.get(wid, work=work)
        family, _, relative = rest.partition('/')
        if not slash or family not in {'workspace', 'outputs'}:
            raise ValueError('Invalid task file reference')
        if family == 'workspace' and not value['root_path']:
            raise ValueError('Workspace has not been opened; no source-directory substitution is available')
        root = Path(value['root_path']) if family == 'workspace' else self.task_directory(value) / 'outputs'
        if family == 'outputs' and root.resolve() != root:
            raise ValueError('Task output directory ownership/path changed')
        candidate = root / relative
        if {'.git', MANAGED_DIRECTORY}.intersection(p.lower() for p in Path(relative).parts) or not candidate.resolve().is_relative_to(root.resolve()):
            raise ValueError('Task file reference escapes its working directory')
        return candidate.resolve()


def workspace_for_context(context, *, paths=None):
    ref = context.get(WORKSPACE_CONTEXT_KEY)
    if not ref:
        return None
    work = WorkIdentity.from_record(context.get('office_work'))
    if context.get('user_id', work.user_id) != work.user_id:
        raise ValueError('Workspace runtime user mismatch')
    if context.get('office_project_id') != work.project_id:
        raise ValueError('Workspace runtime project identity mismatch')
    service = ExecutionWorkspaceService(paths=paths, user_id=work.user_id)
    value = service.get(ref['workspace_id'], work=work)
    if value['project_path'] != str(_bound_project(context.get('office_project_path'))):
        raise ValueError('Workspace runtime project mismatch')
    return service, work, value


def resolve_thread_data(context, data, *, paths=None):
    owned = workspace_for_context(context or {}, paths=paths)
    if not owned:
        if (context or {}).get('office_work') and context.get('office_project_path'):
            clean = {key: value for key, value in (data or {}).items()
                     if key not in {'office_workspace_id', 'office_work'}}
            work = WorkIdentity.from_record(context['office_work'])
            selected_paths = paths or get_paths()
            return {**clean, 'thread_id': work.thread_id,
                    'workspace_path': str(_bound_project(context['office_project_path'])),
                    'outputs_path': str(selected_paths.sandbox_outputs_dir(work.thread_id, user_id=work.user_id)),
                    'office_work': work.to_record()}
        return data
    service, work, value = owned
    root = value['root_path'] or str(service.task_directory(value) / 'tree')
    outputs = service.task_directory(value) / 'outputs'
    return {**(data or {}), 'thread_id': work.thread_id, 'workspace_path': root,
            'outputs_path': str(outputs), 'office_workspace_id': value['workspace_id'], 'office_work': work.to_record()}


def result_reference(value):
    result = {key: value.get(key) for key in ('workspace_id', 'baseline_commit')}
    prefix = f"{REFERENCE_PREFIX}{value['workspace_id']}" if value.get('workspace_id') else '/mnt/user-data'
    result['workspace_path'] = prefix + '/workspace/'
    for key in ('execution_status', 'partial', 'stop_reason', 'tool_observations', 'omitted_tool_observations'):
        if key in value:
            result[key] = value[key]
    if 'execution_evidence' in value:
        result['execution_evidence'] = value['execution_evidence']
    return result


def workspace_guidance(ref):
    location = f'independent worktree {ref["workspace_id"]}' if ref else 'the bound project'
    return (f'Default execution environment: {location}. '
            'Use relevant evidence already available; inspect known files directly and search only for remaining gaps. '
            'Repeated identical results add no observed content, but changed files or new questions may require another search. '
            'File-tool paths /mnt/user-data/workspace/ and /mnt/user-data/outputs/ refer to the task environment. '
            "Read another workspace's existing files at /mnt/office-workspaces/<id>/workspace/. "
            'read_file, ls, glob, grep, apply_patch and powershell_run accept target="project" or an existing workspace ID for one operation. '
            'When assigned to finish and deliver changes held in a worktree, write the remaining changes back '
            'to the bound project, including changes left by an earlier task even if this task makes no new edits. '
            'Necessary writeback is part of the assigned delivery, not a separate task requiring another PM instruction. '
            'Existing permissions and user approval still apply. '
            'Use publish_task_result to write back the current worktree. To select another existing worktree, '
            'pass target=<workspace_id>. To merge other workspaces into it, also pass source_workspace_ids. '
            'Use real returned IDs; omit source_workspace_ids when none are needed. Direct edits in the bound project need no writeback. '
            'Resolve merge conflicts in the returned workspace, stage the resolution with Git, then call '
            'publish_task_result again, or abort the merge. '
            'User-facing installation and preview use the bound project. A service in a worktree is for testing '
            'that workspace; it does not replace delivery to the bound project. '
            'Ordinary PowerShell commands default to the task environment; preview_service defaults to the bound project. '
            'An explicit target selects the environment for that operation. '
            'Report only checks actually performed; HTTP accessibility, source inspection and logic tests '
            'do not establish successful browser interactions.')


def execution_environment(context, *, paths=None, target=None, prepare=False):
    work = WorkIdentity.from_record(context.get('office_work'))
    if context.get('user_id', work.user_id) != work.user_id or context.get('office_project_id') != work.project_id:
        raise ValueError('Execution environment ownership mismatch')
    root = str(_bound_project(context.get('office_project_path')))
    actor = context.get('agent_name') or context.get('office_employee_id')
    if not actor:
        raise ValueError('Execution requires the current employee identity')
    service = ExecutionWorkspaceService(paths=paths, user_id=work.user_id)
    workspace_id = target if target not in {None, 'project'} else None
    if target is None:
        workspace_id = (context.get(WORKSPACE_CONTEXT_KEY) or {}).get('workspace_id')
    if workspace_id:
        value = service.get(workspace_id, work=work)
        if value['project_path'] != root:
            raise ValueError('Execution environment project path differs')
        if prepare:
            value = service.prepare(workspace_id, work=work, context=context)
        value = {**value, 'root_path': value['root_path'] or str(service.task_directory(value) / 'tree')}
    else:
        value = dict(workspace_id=None, project_path=root, root_path=root)
    return service, work, {**value, **work.to_record(), 'employee_id': actor}
