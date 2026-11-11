# 本文件属于 Office 当前项目测试模块：只验证单项目绑定、路径边界、摘要和 Gateway 项目接口。
# 不负责 Agent 执行、文件写入、员工排队、对话上下文或多项目调度测试。
# 关联：office/projects.py 是项目事实来源，run_launch.py 在启动时读取其快照。
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import office
from deerflow.config.paths import Paths
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.message_pipeline import OfficeMessagePipeline
from deerflow.office.messages import OfficeMessageCommand
from deerflow.office.projects import OfficeProjectService, project_id_for_path
from deerflow.office.run_gate import EmployeeRunGate


def build_service(tmp_path: Path) -> OfficeProjectService:
    return OfficeProjectService(
        paths=Paths(tmp_path / '.deer-flow'),
        user_id='default',
        now=lambda: 1_785_751_200_000,
    )


def test_file_location_is_bounded_and_requires_an_existing_file(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    target = project / '中文说明.md'
    target.write_text('# hello', encoding='utf-8')
    service = build_service(tmp_path)
    service.bind(project)
    assert service.resolve_file_location('中文说明.md') == str(target.resolve())
    assert service.resolve_file_location(str(target)) == str(target.resolve())
    for value in ('../outside.txt', '//server/share/file', '', '\x00bad'):
        with pytest.raises(ValueError):
            service.resolve_file_location(value)
    for value in ('missing.md', '.'):
        with pytest.raises(FileNotFoundError):
            service.resolve_file_location(value)


def test_file_location_reuses_thread_virtual_path_guard(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    target = service.paths.resolve_virtual_path('thread-a', '/mnt/user-data/outputs/result.md', user_id=service.user_id)
    target.parent.mkdir(parents=True)
    target.write_text('result', encoding='utf-8')
    assert service.resolve_file_location('/mnt/user-data/outputs/result.md', owned_thread_id='thread-a') == str(target)
    with pytest.raises(ValueError, match='所属员工'):
        service.resolve_file_location('/mnt/user-data/outputs/result.md')
    with pytest.raises(ValueError, match='traversal'):
        service.resolve_file_location('/mnt/user-data/../../../outside.md', owned_thread_id='thread-a')
    with pytest.raises(FileNotFoundError):
        service.resolve_file_location('/mnt/user-data/outputs/result.md', owned_thread_id='thread-b')


def test_pm_previews_scoped_worktree_without_owning_a_worktree(tmp_path):
    from deerflow.office.execution_facts import WorkIdentity
    from deerflow.office.execution_workspace import ExecutionWorkspaceService, REFERENCE_PREFIX, _git
    project = tmp_path / 'project'
    project.mkdir()
    _git(project, 'init')
    (project / 'README.md').write_text('bound version')
    projects = build_service(tmp_path)
    projects.bind(project)
    work = WorkIdentity('default', project_id_for_path(project), 'pm-thread', 'work')
    workspaces = ExecutionWorkspaceService(paths=projects.paths, user_id='default')
    ref = workspaces.allocate(work, employee_id='front', owner_key='task', project_path=project)
    tree = Path(workspaces.prepare(ref['workspace_id'], work=work)['root_path'])
    (tree / 'README.md').write_text('employee version')
    context = dict(office_work=work.to_record(), office_project_id=work.project_id, office_project_path=str(project))
    scoped = f"{REFERENCE_PREFIX}{ref['workspace_id']}/workspace/README.md"
    actual = projects.resolve_file_location(scoped, owned_thread_id=work.thread_id, workspace_context=context)
    assert Path(actual).read_text() == 'employee version'
    assert Path(projects.resolve_file_location('/mnt/user-data/workspace/README.md',
        owned_thread_id=work.thread_id, workspace_context=context)).read_text() == 'bound version'
    for changed in ({**context, 'office_work': {**work.to_record(), 'user_id': 'other'}},
                    {**context, 'office_work': {**work.to_record(), 'project_id': 'other'}}, {}):
        with pytest.raises(ValueError):
            projects.resolve_file_location(scoped, owned_thread_id=work.thread_id, workspace_context=changed)


def test_file_location_rejects_symlink_escape(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    outside = tmp_path / 'private.txt'
    outside.write_text('private', encoding='utf-8')
    try:
        (project / 'link.txt').symlink_to(outside)
    except OSError:
        pytest.skip('Host does not permit symlink creation')
    service = build_service(tmp_path)
    service.bind(project)
    with pytest.raises(ValueError, match='inside the bound project'):
        service.resolve_file_location('link.txt')


def test_file_resolve_route_checks_run_ownership_and_payload(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'ok.md').write_text('ok', encoding='utf-8')
    service.bind(project)

    class View:
        async def resolve_run(self, employee_id, run_id):
            if (employee_id, run_id) != ('employee-a', 'run-a'):
                raise FileNotFoundError('Run not found')
            return employee_id, SimpleNamespace(thread_id='thread-a')

    app = FastAPI()
    app.include_router(office.router)
    app.dependency_overrides[office.get_office_project_service] = lambda: service
    app.dependency_overrides[office.get_office_run_view] = lambda: View()
    with TestClient(app) as client:
        assert client.post('/api/office/files/resolve', json={'path': 'ok.md'}).status_code == 200
        assert client.post('/api/office/files/resolve', json={'path': '../private'}).status_code == 400
        assert client.post('/api/office/files/resolve', json={'path': 'ok.md', 'runId': 'run-a'}).status_code == 400
        assert client.post('/api/office/files/resolve', json={'path': 'ok.md', 'employeeId': 'wrong', 'runId': 'run-a'}).status_code == 404
        assert client.post('/api/office/files/resolve', json={'path': 'ok.md', 'extra': True}).status_code == 400


def test_project_binding_is_persistent_and_idempotent(tmp_path: Path) -> None:
    project = tmp_path / '中文项目'
    (project / 'src').mkdir(parents=True)
    (project / 'src' / 'app.py').write_text('print(1)\n', encoding='utf-8')
    (project / 'node_modules').mkdir()

    service = build_service(tmp_path)
    first = service.bind(project)
    first_state = service.state_path.read_text(encoding='utf-8')
    second = service.bind(project)
    restored = build_service(tmp_path).get_summary()

    assert first == second == restored
    assert first['projectId'] == project_id_for_path(project)
    assert first['path'] == str(project.resolve())
    assert first['tree'] == [
        {'name': 'src', 'type': 'directory', 'children': [{'name': 'app.py', 'type': 'file'}]}
    ]
    assert service.state_path.read_text(encoding='utf-8') == first_state
    assert json.loads(first_state)['binding']['boundAt'] == 1_785_751_200_000


def test_conditional_bind_never_overwrites_an_existing_project(tmp_path: Path) -> None:
    first_project = tmp_path / 'first'
    second_project = tmp_path / 'second'
    first_project.mkdir()
    second_project.mkdir()
    service = build_service(tmp_path)
    first = service.bind(first_project)

    result = service.bind(second_project, if_unbound=True)
    missing_result = service.bind(tmp_path / 'deleted-legacy-project', if_unbound=True)

    assert result == first
    assert missing_result == first
    assert service.get_summary() == first


def test_selected_path_survives_restart_without_changing_project_identity(tmp_path: Path) -> None:
    project = tmp_path / 'actual'
    project.mkdir()
    (project / 'page.html').write_text('hello', encoding='utf-8')
    selected = tmp_path / 'selected'
    try:
        selected.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip('Host does not permit directory symlinks')
    service = build_service(tmp_path)
    original = service.bind(project)
    original_binding = service.get_binding()
    original_bound_at = json.loads(service.state_path.read_text(encoding='utf-8'))['binding']['boundAt']
    service._now = lambda: original_bound_at + 1000
    rebound = service.bind(selected)
    restored = build_service(tmp_path)

    assert rebound == restored.get_summary()
    assert rebound['displayPath'] == str(selected)
    assert rebound['path'] == str(project.resolve())
    assert rebound['projectId'] == original['projectId']
    assert restored.get_binding() == original_binding
    assert restored.get_binding().to_context() == original_binding.to_context()
    assert restored.get_context()['path'] == str(project.resolve())
    assert restored.resolve_file_location('page.html') == str((project / 'page.html').resolve())
    with pytest.raises(ValueError, match='inside the bound project'):
        restored.resolve_file_location('../outside.txt')
    assert json.loads(service.state_path.read_text(encoding='utf-8'))['binding']['displayPath'] == str(selected)
    assert json.loads(service.state_path.read_text(encoding='utf-8'))['binding']['boundAt'] == original_bound_at


def test_existing_binding_requires_reselection_to_record_display_path(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    service = build_service(tmp_path)
    original = service.bind(project)
    payload = json.loads(service.state_path.read_text(encoding='utf-8'))
    payload['binding'].pop('displayPath', None)
    service.state_path.write_text(json.dumps(payload), encoding='utf-8')
    before = service.state_path.read_bytes()

    assert service.get_summary()['displayPath'] is None
    assert service.get_binding().project_id == original['projectId']
    assert service.state_path.read_bytes() == before
    assert service.bind(project)['displayPath'] == str(project)


def test_display_path_cannot_point_to_a_different_project(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    other = tmp_path / 'other'
    project.mkdir()
    other.mkdir()
    service = build_service(tmp_path)
    service.bind(project)
    payload = json.loads(service.state_path.read_text(encoding='utf-8'))
    payload['binding']['displayPath'] = str(other)
    service.state_path.write_text(json.dumps(payload), encoding='utf-8')

    with pytest.raises(ValueError, match='显示路径'):
        service.get_summary()
    assert service.get_binding().path == project.resolve()


def test_conditional_bind_preserves_an_unreadable_existing_record(tmp_path: Path) -> None:
    current_project = tmp_path / 'current'
    legacy_project = tmp_path / 'legacy'
    current_project.mkdir()
    legacy_project.mkdir()
    service = build_service(tmp_path)
    service.bind(current_project)
    service.state_path.write_text('{broken json', encoding='utf-8')

    with pytest.raises(ValueError, match='cannot be read'):
        service.bind(legacy_project, if_unbound=True)

    assert service.state_path.read_text(encoding='utf-8') == '{broken json'


def test_project_binding_never_returns_a_deleted_path(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    service = build_service(tmp_path)
    service.bind(project)
    project.rmdir()

    with pytest.raises(ValueError, match='does not exist'):
        service.get_summary()


def test_project_summary_rejects_an_unreadable_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    service = build_service(tmp_path)
    service.bind(project)
    original_iterdir = Path.iterdir

    def denied_iterdir(path: Path):
        if path == project.resolve():
            raise PermissionError('denied')
        return original_iterdir(path)

    monkeypatch.setattr(Path, 'iterdir', denied_iterdir)

    with pytest.raises(ValueError, match='cannot be read'):
        service.get_summary()


def test_unreadable_project_is_not_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    service = build_service(tmp_path)
    original_iterdir = Path.iterdir

    def denied_iterdir(path: Path):
        if path == project.resolve():
            raise PermissionError('denied')
        return original_iterdir(path)

    monkeypatch.setattr(Path, 'iterdir', denied_iterdir)

    with pytest.raises(ValueError, match='cannot be read'):
        service.bind(project)

    assert not service.state_path.exists()


def test_project_summary_does_not_recurse_into_a_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / 'project'
    junction = project / 'linked-workspace'
    junction.mkdir(parents=True)
    (junction / 'outside.txt').write_text('outside\n', encoding='utf-8')
    monkeypatch.setattr(
        'deerflow.office.projects.os.path.isjunction',
        lambda path: Path(path) == junction,
    )

    summary = build_service(tmp_path).bind(project)

    assert summary['tree'] == [{'name': 'linked-workspace', 'type': 'directory'}]


def test_gateway_reuses_the_process_project_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_service(tmp_path)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(office_project_service=service),
        ),
    )
    monkeypatch.setattr(office, 'get_effective_user_id', lambda: 'default')

    assert office.get_office_project_service(request) is service


def test_office_project_routes_bind_and_restore(tmp_path: Path) -> None:
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'notes.txt').write_text('wave-one gateway\\n', encoding='utf-8')
    service = build_service(tmp_path)
    app = FastAPI()
    app.include_router(office.router)
    app.dependency_overrides[office.get_office_project_service] = lambda: service

    with TestClient(app) as client:
        assert client.get('/api/office/project').json() == {'workspace': None}
        response = client.put('/api/office/project', json={'path': str(project)})
        assert response.status_code == 200
        workspace = response.json()['workspace']
        assert workspace['projectId'] == project_id_for_path(project)
        assert client.get('/api/office/project/context').json()['context']['projectId'] == workspace['projectId']
        assert client.post('/api/office/project/files/read', json={'paths': ['notes.txt']}).json()['files'][0]['exists']
        assert client.get('/api/office/project/search?query=wave-one').json()['results'][0]['path'] == 'notes.txt'
        assert client.get('/api/office/project').json() == {'workspace': workspace}
        other = tmp_path / 'other'
        other.mkdir()
        conditional = client.put(
            '/api/office/project',
            json={'path': str(other), 'ifUnbound': True},
        )
        assert conditional.json() == {'workspace': workspace}
        assert client.put('/api/office/project', json={'path': 'relative/project'}).status_code == 400


@pytest.mark.asyncio
async def test_employee_run_uses_the_current_office_project_by_default(tmp_path: Path) -> None:
    class EmployeeService:
        user_id = "default"

        @staticmethod
        async def list_employees() -> list[dict[str, object]]:
            return [EmployeeService.get_employee_summary("employee-one")]

        @staticmethod
        def validate_employee_id(employee_id: str) -> str:
            return employee_id

        @staticmethod
        def get_employee_summary(employee_id: str) -> dict[str, object]:
            return {
                'id': employee_id,
                'enabled': True,
                'modelProfileId': 'profile-primary',
            }

        @staticmethod
        def thread_id(employee_id: str, kind: str) -> str:
            return f'office__default__{employee_id}__{kind}'

        @staticmethod
        def get_capability_index(_employee_id: str, *, model_name=None) -> dict[str, object]:
            return {
                'authorizedToolGroups': ['file:read'],
                'effectiveTools': ['file:read'],
                'unavailableAuthorizedToolGroups': [],
                'runtimeAvailableTools': ['read_file'],
                'toolAvailabilityReasons': {},
                'authority': {'can_delegate': False},
            }

    project = tmp_path / 'project'
    project.mkdir()
    project_service = build_service(tmp_path)
    project_service.bind(project)
    captured = {}

    class RunLauncher:
        async def launch(self, employee_id, _model_profile_id, _execution_spec, **kwargs):
            captured['binding'] = kwargs.get('project_binding')
            return SimpleNamespace(
                run_id='run-project-binding',
                thread_id=kwargs['thread_id'],
                assistant_id=employee_id,
            )

    class RunView:
        @staticmethod
        async def find_message_run(*_args):
            return None

        @staticmethod
        def serialize_run(_employee_id, record):
            return {'runId': record.run_id}

        @staticmethod
        def task_summary(_record):
            return None

    pipeline = OfficeMessagePipeline(
        user_id="default",
        employee_service=EmployeeService(),
        group_store=GroupMessageStore(
            paths=Paths(tmp_path / ".deer-flow"),
            user_id="default",
        ),
        employee_run_gate=EmployeeRunGate(),
        run_launcher=RunLauncher(),
        run_view=RunView(),
        project_service=project_service,
    )

    await pipeline.send(
        "message-project-binding",
        OfficeMessageCommand(
            "group",
            "完成任务",
            None,
            ("employee-one",),
        ),
        start_run=object(),
        request=object(),
    )

    assert captured['binding'].project_id == project_id_for_path(project)
    assert captured['binding'].path == project.resolve()
