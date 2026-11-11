# 本文件属于 Office 当前项目模块：只负责单一项目绑定、项目边界校验、工作区摘要、读取和搜索。
# 不负责 Agent 执行、文件写入、Run 排队、对话上下文或项目级任务调度。
# 关联：execution_workspace.py 保持项目绑定与任务目录分离，Gateway 项目接口投影本模块事实。
from __future__ import annotations

from deerflow.office.user_errors import OfficeInputError, OfficeNotFoundError

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deerflow.config.paths import Paths
from deerflow.runtime.context_keys import (
    OFFICE_PROJECT_ID_CONTEXT_KEY,
    OFFICE_PROJECT_PATH_CONTEXT_KEY,
)

_PROJECT_ID_PATTERN = re.compile(r'^project-[0-9a-f]{24}$')
_PROJECT_STORE_VERSION = 1
_IGNORED_TREE_NAMES = {
    '.git',
    '.office-worktrees',
    '.idea',
    '.venv',
    '.vscode',
    '__pycache__',
    'build',
    'coverage',
    'dist',
    'node_modules',
}
_MAX_TREE_DEPTH = 3
_MAX_TREE_CHILDREN = 18
_MAX_READ_FILES = 20
_MAX_FILE_BYTES = 512 * 1024
_MAX_SEARCH_FILES = 2000
_MAX_SEARCH_RESULTS = 50


@dataclass(frozen=True, slots=True)
class ProjectBinding:
    '''Canonical project identity persisted by the Office seam.'''

    project_id: str
    path: Path
    display_path: str | None = field(default=None, compare=False)

    def to_context(self) -> dict[str, str]:
        return {
            OFFICE_PROJECT_ID_CONTEXT_KEY: self.project_id,
            OFFICE_PROJECT_PATH_CONTEXT_KEY: str(self.path),
        }


def _canonical_project_path(value: str | os.PathLike[str]) -> Path:
    raw = str(value or '').strip()
    if not raw:
        raise ValueError('projectPath must be a non-empty string')
    requested = Path(raw).expanduser()
    if not requested.is_absolute():
        raise ValueError('projectPath must be absolute')
    try:
        resolved = requested.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f'project path does not exist: {requested}') from exc
    if not resolved.is_dir():
        raise ValueError(f'project path is not a directory: {resolved}')
    return resolved


def _project_path_identity(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _visible_entries(root: Path) -> list[Path]:
    try:
        entries = [entry for entry in root.iterdir() if entry.name not in _IGNORED_TREE_NAMES]
    except OSError as exc:
        raise ValueError(f'project directory cannot be read: {root}') from exc
    return sorted(entries, key=lambda item: (not item.is_dir(), item.name.lower()))


def _build_workspace_tree(root: Path, depth: int = _MAX_TREE_DEPTH) -> list[dict[str, Any]]:
    entries = _visible_entries(root)
    nodes: list[dict[str, Any]] = []
    for entry in entries[:_MAX_TREE_CHILDREN]:
        is_directory = entry.is_dir()
        node: dict[str, Any] = {
            'name': entry.name,
            'type': 'directory' if is_directory else 'file',
        }
        if (
            is_directory
            and not entry.is_symlink()
            and not os.path.isjunction(entry)
            and depth > 1
        ):
            children = _build_workspace_tree(entry, depth - 1)
            if children:
                node['children'] = children
        nodes.append(node)

    hidden_count = max(0, len(entries) - _MAX_TREE_CHILDREN)
    if hidden_count:
        nodes.append({'name': f'... 还有 {hidden_count} 项', 'type': 'more'})
    return nodes


def _workspace_summary(binding: ProjectBinding) -> dict[str, Any]:
    if binding.display_path is not None and _canonical_project_path(binding.display_path) != binding.path:
        raise OfficeInputError('项目显示路径已指向其他目录，请重新绑定项目', code='errors.the_project_path_points_to_another_folder_bind_the_project_again')
    tree = _build_workspace_tree(binding.path)
    return {
        'projectId': binding.project_id,
        'path': str(binding.path),
        'displayPath': binding.display_path,
        'name': binding.path.name or str(binding.path),
        'kind': 'empty' if not tree else 'existing',
        'tree': tree,
    }


def _project_file(binding: ProjectBinding, value: Any) -> tuple[Path, str]:
    raw = str(value or '').strip()
    if not raw or chr(0) in raw:
        raise ValueError('workspace path must be a non-empty string')
    requested = Path(raw)
    if requested.drive and not requested.is_absolute():
        raise ValueError('workspace path must stay inside the bound project')
    candidate = requested if requested.is_absolute() else binding.path / requested
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(binding.path)
    except ValueError as exc:
        raise ValueError('workspace path must stay inside the bound project') from exc
    if '.office-worktrees' in {part.lower() for part in relative.parts}:
        raise ValueError('Task files require a scoped workspace reference')
    return resolved, relative.as_posix()


def project_id_for_path(value: str | os.PathLike[str]) -> str:
    '''Derive the stable Office project ID for one canonical directory.'''

    path = _canonical_project_path(value)
    digest = hashlib.sha256(_project_path_identity(path).encode('utf-8')).hexdigest()
    return f'project-{digest[:24]}'


class OfficeProjectService:
    '''Own the single current project binding for one Office user.'''

    def __init__(
        self,
        *,
        paths: Paths,
        user_id: str,
        now: Callable[[], int] | None = None,
    ) -> None:
        self.user_id = user_id
        self.paths = paths
        self.state_path = paths.user_dir(user_id) / 'office-project.v1.json'
        self._now = now or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()

    def _read_record(self) -> tuple[ProjectBinding, int] | None:
        if not self.state_path.exists():
            return None
        try:
            payload = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('office project binding cannot be read') from exc
        if not isinstance(payload, Mapping) or payload.get('version') != _PROJECT_STORE_VERSION:
            raise ValueError('office project binding version is invalid')
        raw_binding = payload.get('binding')
        if not isinstance(raw_binding, Mapping):
            raise ValueError('office project binding is invalid')

        path = _canonical_project_path(str(raw_binding.get('path') or ''))
        project_id = str(raw_binding.get('projectId') or '').strip()
        if project_id != project_id_for_path(path):
            raise ValueError('office project binding id does not match its path')
        bound_at = raw_binding.get('boundAt')
        if not isinstance(bound_at, int) or bound_at <= 0:
            raise ValueError('office project binding timestamp is invalid')
        display_path = raw_binding.get('displayPath')
        if display_path is not None and (not isinstance(display_path, str) or not Path(display_path).is_absolute()):
            raise ValueError('office project display path is invalid')
        return ProjectBinding(project_id=project_id, path=path, display_path=display_path), bound_at

    def get_binding(self) -> ProjectBinding | None:
        '''Return the validated current binding, if one exists.'''

        with self._lock:
            record = self._read_record()
            return record[0] if record is not None else None

    def get_summary(self) -> dict[str, Any] | None:
        '''Return the current project summary without exposing a stale path.'''

        binding = self.get_binding()
        return _workspace_summary(binding) if binding is not None else None

    def _require_binding(self) -> ProjectBinding:
        binding = self.get_binding()
        if binding is None:
            raise FileNotFoundError('No project is bound')
        return binding

    def get_context(self, *, task: Any = None, actor: Any = None) -> dict[str, Any]:
        binding = self._require_binding()
        summary = _workspace_summary(binding)
        top_entries = [node['name'] for node in summary['tree'] if node['type'] != 'more']
        return {
            'projectId': binding.project_id,
            'path': str(binding.path),
            'name': summary['name'],
            'kind': summary['kind'],
            'summary': '当前绑定项目：' + summary['name'] + '（' + str(binding.path) + '）',
            'topEntries': top_entries,
            'files': [],
            'searchHits': [],
            'task': str(task or '').strip(),
            'actor': str(actor or '').strip(),
        }

    def resolve_file_location(self, value: str, *, owned_thread_id: str | None = None,
                              expected_project_id: str | None = None, workspace_context: dict | None = None) -> str:
        '''Resolve a file in the bound project or an already authorized Run thread.'''
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or chr(0) in value:
            raise OfficeInputError('文件路径无效', code='errors.invalid_file_path')
        raw = value.strip().replace('\\', '/')
        if raw.startswith('//'):
            raise OfficeInputError('不支持网络文件位置', code='errors.network_file_locations_are_unsupported')
        from deerflow.office.execution_workspace import REFERENCE_PREFIX, ExecutionWorkspaceService, workspace_for_context
        owned = workspace_for_context(workspace_context or {}, paths=self.paths)
        if raw.startswith(REFERENCE_PREFIX) or owned:
            if owned:
                service, work, workspace = owned
            else:
                from deerflow.office.execution_facts import WorkIdentity
                work = WorkIdentity.from_record((workspace_context or {}).get('office_work'))
                service = ExecutionWorkspaceService(paths=self.paths, user_id=self.user_id)
                workspace = None
            if work.user_id != self.user_id or work.thread_id != owned_thread_id:
                raise ValueError('Task preview owner mismatch')
            binding = self._require_binding()
            if binding.project_id != work.project_id or expected_project_id not in (None, work.project_id):
                raise OfficeInputError('文件所属项目与当前项目不同，请切换回原项目后查看', code='errors.this_file_belongs_to_another_project_switch_back_to_view_it')
            if not raw.startswith(REFERENCE_PREFIX):
                if raw.startswith('/mnt/user-data/uploads/'):
                    resolved = self.paths.resolve_virtual_path(owned_thread_id, raw, user_id=self.user_id)
                elif workspace['root_path']:
                    scoped = raw.removeprefix('/mnt/user-data/') if raw.startswith('/mnt/user-data/') else 'workspace/' + raw
                    resolved = service.resolve_reference(f"{REFERENCE_PREFIX}{workspace['workspace_id']}/{scoped}", work=work)
                else:
                    raise ValueError('Workspace has not been opened')
            else:
                resolved = service.resolve_reference(raw, work=work)
            if not resolved.is_file():
                raise OfficeNotFoundError('文件不存在或不是普通文件', code='errors.file_does_not_exist_or_is_not_a_regular_file')
            return str(resolved)
        if raw.startswith('/mnt/user-data/'):
            if not owned_thread_id:
                raise OfficeInputError('运行产物必须提供所属员工和 Run', code='errors.run_outputs_require_their_employee_and_run_ids')
        if raw == '/mnt/user-data/workspace' or raw.startswith('/mnt/user-data/workspace/'):
            raw = raw[len('/mnt/user-data/workspace'):].lstrip('/') or '.'
        if raw.startswith('/mnt/user-data/'):
            resolved = self.paths.resolve_virtual_path(owned_thread_id, raw, user_id=self.user_id)
        else:
            binding = self._require_binding()
            if expected_project_id is not None and binding.project_id != expected_project_id:
                raise OfficeInputError('文件所属项目与当前项目不同，请切换回原项目后查看', code='errors.this_file_belongs_to_another_project_switch_back_to_view_it')
            resolved, _ = _project_file(binding, raw)
        if not resolved.is_file():
            raise OfficeNotFoundError('文件不存在或不是普通文件', code='errors.file_does_not_exist_or_is_not_a_regular_file')
        return str(resolved)

    def read_files(self, paths: Any) -> list[dict[str, Any]]:
        if not isinstance(paths, list) or len(paths) > _MAX_READ_FILES:
            raise ValueError(f'paths must be a list with at most {_MAX_READ_FILES} items')
        binding = self._require_binding()
        result: list[dict[str, Any]] = []
        for value in paths:
            file_path, relative = _project_file(binding, value)
            if not file_path.exists():
                result.append({'path': relative, 'exists': False, 'content': '', 'truncated': False})
                continue
            if not file_path.is_file():
                raise ValueError(f'workspace path is not a file: {relative}')
            try:
                payload = file_path.read_bytes()[: _MAX_FILE_BYTES + 1]
            except OSError as exc:
                raise ValueError(f'workspace file cannot be read: {relative}') from exc
            truncated = len(payload) > _MAX_FILE_BYTES
            content = payload[:_MAX_FILE_BYTES].decode('utf-8', errors='replace')
            result.append({
                'path': relative,
                'exists': True,
                'content': content,
                'truncated': truncated,
            })
        return result

    def search(self, query: Any, *, limit: Any = 8) -> list[dict[str, Any]]:
        normalized_query = str(query or '').strip()
        if not normalized_query:
            raise ValueError('query must be a non-empty string')
        if len(normalized_query) > 256:
            raise ValueError('query is too long')
        if not isinstance(limit, int) or not 1 <= limit <= _MAX_SEARCH_RESULTS:
            raise ValueError(f'limit must be between 1 and {_MAX_SEARCH_RESULTS}')
        binding = self._require_binding()
        needle = normalized_query.casefold()
        results: list[dict[str, Any]] = []
        scanned = 0

        for current_root, dir_names, file_names in os.walk(binding.path):
            root_path = Path(current_root)
            dir_names[:] = [
                name
                for name in dir_names
                if name not in _IGNORED_TREE_NAMES
                and not (root_path / name).is_symlink()
                and not os.path.isjunction(root_path / name)
            ]
            for file_name in sorted(file_names):
                if scanned >= _MAX_SEARCH_FILES or len(results) >= limit:
                    return results
                if (root_path / file_name).is_symlink():
                    continue
                scanned += 1
                file_path, relative = _project_file(binding, root_path / file_name)
                try:
                    if file_path.stat().st_size > _MAX_FILE_BYTES:
                        continue
                    payload = file_path.read_bytes()
                except OSError:
                    continue
                if bytes((0,)) in payload:
                    continue
                text = payload.decode('utf-8', errors='replace')
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if needle in line.casefold():
                        results.append({
                            'path': relative,
                            'lineNumber': line_number,
                            'lineText': line[:500],
                        })
                        if len(results) >= limit:
                            return results
        return results

    def bind(
        self,
        project_path: str | os.PathLike[str],
        *,
        if_unbound: bool = False,
    ) -> dict[str, Any]:
        '''Persist one canonical project path and return its current summary.'''

        with self._lock:
            try:
                current = self._read_record()
            except ValueError:
                if if_unbound:
                    raise
                current = None
            if current is not None and if_unbound:
                return _workspace_summary(current[0])

            path = _canonical_project_path(project_path)
            binding = ProjectBinding(
                project_id=project_id_for_path(path), path=path,
                display_path=str(Path(str(project_path).strip()).expanduser()),
            )
            summary = _workspace_summary(binding)
            same_project = current is not None and current[0] == binding
            if same_project and current[0].display_path == binding.display_path:
                return summary

            payload = {
                'version': _PROJECT_STORE_VERSION,
                'binding': {
                    'projectId': binding.project_id,
                    'path': str(binding.path),
                    'displayPath': binding.display_path,
                    'boundAt': current[1] if same_project else self._now(),
                },
            }
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.state_path.with_name(
                f'.{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp'
            )
            try:
                temporary_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8',
                )
                os.replace(temporary_path, self.state_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        return summary


def project_binding_from_context(context: Mapping[str, Any] | None) -> ProjectBinding | None:
    if not isinstance(context, Mapping):
        return None
    raw_id = context.get(OFFICE_PROJECT_ID_CONTEXT_KEY)
    raw_path = context.get(OFFICE_PROJECT_PATH_CONTEXT_KEY)
    if raw_id is None and raw_path is None:
        return None
    if raw_id is None or raw_path is None:
        raise ValueError('runtime project context is incomplete')
    project_id = str(raw_id).strip()
    if not _PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError('runtime project id is invalid')
    path = _canonical_project_path(str(raw_path))
    if project_id != project_id_for_path(path):
        raise ValueError('runtime project path no longer matches its project id')
    return ProjectBinding(project_id=project_id, path=path)
