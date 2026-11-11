# 本文件属于 DeerFlow Gateway Run 恢复测试模块：只验证持久化启动合同恢复为同一原生执行语义。
# 不负责 Office 消息排队、群聊存储、员工选择、第二恢复器或前端重连测试。
# 关联：gateway/deps.py 启动恢复 Worker，services.py 与 RunManager 继续原生 Run 生命周期。
"""Gateway restart recovery for durable DeerFlow runs."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import deerflow.runtime as runtime_module
from app.gateway import deps as gateway_deps
from app.gateway import services as gateway_services
from app.gateway.model_bindings import SELECTED_MODEL_NAME, ModelBindingError
from deerflow.persistence import engine as engine_module
from deerflow.persistence import thread_meta as thread_meta_module
from deerflow.runtime.checkpointer import async_provider as checkpointer_module
from deerflow.runtime.events import store as event_store_module
from deerflow.runtime.runs.execution import (
    CHECKPOINT_RUN_ID_METADATA_KEY,
    RunExecutionSpecV1,
    RunLaunchContractV1,
)
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import RunContext

MODEL_PROFILE_ID = 'profile-primary'
RUNTIME_MODEL_ID = 'mimo-v2.5'


@asynccontextmanager
async def _fake_context(value):
    yield value


class _FakeRunManager:
    """RunManager double that exposes persisted active rows unchanged."""

    instances: list[_FakeRunManager] = []
    restored_runs: list[SimpleNamespace] = []

    def __init__(self, *, store):
        self.store = store
        self.restore_calls: list[dict] = []
        self.shutdown_calls = 0
        _FakeRunManager.instances.append(self)

    async def restore_inflight_runs(self, *, before: str | None = None):
        self.restore_calls.append({"before": before})
        return self.restored_runs

    async def shutdown(self, *, timeout: float = 5.0) -> None:
        self.shutdown_calls += 1


class _FakeLocalProvider:
    async def get_user(self, user_id: str):
        return SimpleNamespace(
            id=user_id,
            system_role="user",
            oauth_provider="local",
            oauth_id=user_id,
        )


def _persisted_run(run_id: str, *, status: RunStatus = RunStatus.running):
    return SimpleNamespace(
        run_id=run_id,
        thread_id="thread-one",
        assistant_id="employee-one",
        user_id="owner-one",
        status=status,
        events_finalized=False,
        last_event_seq=7,
        task=None,
        metadata={},
        kwargs={
            "execution_spec": {
                "version": 1,
                "mode": "new",
                "input": {"messages": [{"role": "user", "content": "生成项目说明"}]},
            }
        },
    )


def _launch_contract() -> dict:
    return RunLaunchContractV1.build(
        model_profile_id=MODEL_PROFILE_ID,
        execution_spec=RunExecutionSpecV1(
            version=1,
            mode='new',
            input={'messages': [{'role': 'user', 'content': '生成项目说明'}]},
        ),
        config={},
        context={
            'model_name': 'stale-client-model',
            'runtime_model_id': RUNTIME_MODEL_ID,
        },
        stream_modes=['values', 'messages'],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    ).to_dict()




def _native_launch_contract() -> dict:
    return RunLaunchContractV1.build(
        model_profile_id=None,
        execution_spec=RunExecutionSpecV1(
            version=1,
            mode='new',
            input={'messages': [{'role': 'user', 'content': '????'}]},
        ),
        config={},
        context={'model_name': 'fake-test-model'},
        stream_modes=['values'],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    ).to_dict()


def _checkpoint(
    run_id: str,
    checkpoint_id: str,
    *,
    checkpoint_ns: str = '',
    checkpoint_map: dict | None = None,
):
    configurable = {
        'thread_id': 'thread-one',
        'checkpoint_ns': checkpoint_ns,
        'checkpoint_id': checkpoint_id,
    }
    if checkpoint_map is not None:
        configurable['checkpoint_map'] = checkpoint_map
    return SimpleNamespace(
        config={'configurable': configurable},
        metadata={CHECKPOINT_RUN_ID_METADATA_KEY: run_id},
    )


class _RecoveryCheckpointer:
    def __init__(self, checkpoints):
        self.checkpoints = checkpoints
        self.seen_config = None

    async def alist(self, config):
        self.seen_config = config
        for checkpoint in self.checkpoints:
            yield checkpoint


def _recovery_request(checkpointer, *, user_id: str = 'owner-one'):
    user = SimpleNamespace(
        id=user_id,
        system_role='user',
        oauth_provider='local',
        oauth_id=user_id,
    )
    run_context = RunContext(
        checkpointer=checkpointer,
        store=object(),
        event_store=object(),
        thread_store=object(),
        app_config=SimpleNamespace(
            name='base-config',
            get_model_config=lambda model_name: (
                object() if model_name == 'fake-test-model' else None
            ),
        ),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                stream_bridge=object(),
                run_manager=object(),
                checkpointer=checkpointer,
            )
        ),
        state=SimpleNamespace(user=user, auth_source='internal'),
        cookies={},
        headers={},
    )
    return request, run_context


def _install_model_binding(monkeypatch, run_context, *, runtime_model_id=RUNTIME_MODEL_ID):
    resolved_model = SimpleNamespace(model=runtime_model_id)
    resolved_config = SimpleNamespace(
        name='run-config',
        get_model_config=lambda name: (
            resolved_model if name == SELECTED_MODEL_NAME else None
        ),
    )
    calls = []

    def fake_resolve(base_config, profile_id, *, additional_profile_ids=()):
        calls.append((base_config, profile_id, tuple(additional_profile_ids)))
        return resolved_config

    monkeypatch.setattr(gateway_services, 'resolve_run_app_config', fake_resolve)
    return resolved_config, calls


def _install_runtime_fakes(monkeypatch):
    async def fake_init_engine_from_config(_database):
        return None

    async def fake_close_engine():
        return None

    async def fake_initialize_office_runtime_services(_app, _config):
        async def reconcile_workflows():
            return None
        _app.state.office_message_pipeline = SimpleNamespace(restore_waits=reconcile_workflows)
        class Projection:
            @staticmethod
            async def reconcile_startup(_active_employee_ids):
                return None

        _app.state.office_run_projection = Projection()

    monkeypatch.setattr(engine_module, "init_engine_from_config", fake_init_engine_from_config)
    monkeypatch.setattr(engine_module, "get_session_factory", lambda: None)
    monkeypatch.setattr(engine_module, "close_engine", fake_close_engine)
    monkeypatch.setattr(runtime_module, "make_stream_bridge", lambda _config: _fake_context(object()))
    monkeypatch.setattr(checkpointer_module, "make_checkpointer", lambda _config: _fake_context(object()))
    monkeypatch.setattr(runtime_module, "make_store", lambda _config: _fake_context(object()))
    monkeypatch.setattr(thread_meta_module, "make_thread_store", lambda _sf, _store: object())
    monkeypatch.setattr(event_store_module, "make_run_event_store", lambda _config: object())
    monkeypatch.setattr(gateway_deps, "RunManager", _FakeRunManager)
    monkeypatch.setattr(gateway_deps, "get_local_provider", lambda: _FakeLocalProvider())
    monkeypatch.setattr(
        gateway_deps,
        "_initialize_office_runtime_services",
        fake_initialize_office_runtime_services,
    )


def _config():
    return SimpleNamespace(
        database=SimpleNamespace(backend="sqlite"),
        run_events=SimpleNamespace(backend="memory"),
    )


@pytest.mark.anyio
async def test_gateway_restart_prepares_then_relaunches_the_original_run(monkeypatch):
    app = FastAPI()
    original = _persisted_run("run-original")
    _FakeRunManager.instances.clear()
    _FakeRunManager.restored_runs = [original]
    _install_runtime_fakes(monkeypatch)

    prepared_calls = []
    launched_calls = []

    async def fake_prepare(record, request, *, recovery=False):
        prepared_calls.append((record, request, recovery))
        return SimpleNamespace(record=record, request=request)

    def fake_launch(prepared):
        launched_calls.append(prepared)
        return prepared.record

    monkeypatch.setattr(gateway_services, "prepare_persisted_run", fake_prepare, raising=False)
    monkeypatch.setattr(gateway_services, "launch_prepared_run", fake_launch, raising=False)

    async with gateway_deps.langgraph_runtime(app, _config()):
        assert original.run_id == "run-original"
        assert original.status == RunStatus.running
        assert original.events_finalized is False
        assert original.last_event_seq == 7

    manager = _FakeRunManager.instances[0]
    assert len(manager.restore_calls) == 1
    assert prepared_calls[0][0] is original
    assert prepared_calls[0][1].state.user.id == "owner-one"
    assert prepared_calls[0][2] is True
    assert [item.record.run_id for item in launched_calls] == ["run-original"]


@pytest.mark.anyio
async def test_gateway_restart_aborts_before_launch_when_any_preflight_fails(monkeypatch):
    app = FastAPI()
    first = _persisted_run("run-first")
    second = _persisted_run("run-second", status=RunStatus.pending)
    _FakeRunManager.instances.clear()
    _FakeRunManager.restored_runs = [first, second]
    _install_runtime_fakes(monkeypatch)

    launched_calls = []

    async def fake_prepare(record, request, *, recovery=False):
        assert recovery is True
        if record.run_id == "run-second":
            raise RuntimeError("checkpoint ownership mismatch")
        return SimpleNamespace(record=record, request=request)

    def fake_launch(prepared):
        launched_calls.append(prepared.record.run_id)

    monkeypatch.setattr(gateway_services, "prepare_persisted_run", fake_prepare, raising=False)
    monkeypatch.setattr(gateway_services, "launch_prepared_run", fake_launch, raising=False)

    with pytest.raises(RuntimeError, match="checkpoint ownership mismatch"):
        async with gateway_deps.langgraph_runtime(app, _config()):
            pass

    assert launched_calls == []
    assert first.status == RunStatus.running
    assert second.status == RunStatus.pending
    assert first.task is None
    assert second.task is None
    assert first.events_finalized is False
    assert second.events_finalized is False


@pytest.mark.anyio
async def test_gateway_restores_office_leases_before_recovery_preflight(
    monkeypatch,
    tmp_path,
):
    from deerflow.office.projects import ProjectBinding, project_id_for_path
    from deerflow.office.run_gate import EmployeeRunGate

    app = FastAPI()
    old_project = tmp_path / "old-project"
    old_project.mkdir()
    current_project = tmp_path / "current-project"
    current_project.mkdir()
    old_binding = ProjectBinding(project_id_for_path(old_project), old_project)
    original = _persisted_run("run-office")
    original.metadata = {
        "office_employee_id": "employee-one",
        "office_conversation_kind": "group",
        "office_lease_id": "lease-persisted",
        "office_project_write_required": True,
        "office_queue_id": "queue-persisted",
        "office_task_summary": "continue old project work",
    }
    contract = _launch_contract()
    contract["trusted_context"] = old_binding.to_context()
    original.kwargs = {"launch_contract": contract}
    _FakeRunManager.instances.clear()
    _FakeRunManager.restored_runs = [original]
    _install_runtime_fakes(monkeypatch)

    employee_gate = EmployeeRunGate()

    class Projection:
        active_employee_ids: set[str] | None = None

        async def reconcile_startup(self, active_employee_ids):
            self.active_employee_ids = set(active_employee_ids)

    projection = Projection()

    class CurrentProjectMustNotBeRead:
        def get_binding(self):
            raise AssertionError("startup recovery read the current project binding")

    async def fake_initialize(app_under_test, _config):
        async def reconcile_workflows():
            return None
        app_under_test.state.office_message_pipeline = SimpleNamespace(restore_waits=reconcile_workflows)
        app_under_test.state.office_employee_run_gate = employee_gate
        app_under_test.state.office_project_service = CurrentProjectMustNotBeRead()
        app_under_test.state.office_run_projection = projection

    async def fake_prepare(record, request, *, recovery=False):
        assert recovery is True
        assert projection.active_employee_ids == {"employee-one"}
        admission = await employee_gate.get_admission("queue-persisted")
        assert admission is not None
        assert admission.lease_id == "lease-persisted"
        return SimpleNamespace(record=record, request=request)

    monkeypatch.setattr(
        gateway_deps,
        "_initialize_office_runtime_services",
        fake_initialize,
    )
    monkeypatch.setattr(gateway_services, "prepare_persisted_run", fake_prepare)
    monkeypatch.setattr(gateway_services, "launch_prepared_run", lambda prepared: prepared.record)

    async with gateway_deps.langgraph_runtime(app, _config()):
        assert await employee_gate.list_pending() == []


@pytest.mark.anyio
async def test_office_recovery_restores_employee_occupancy_without_write_locks(tmp_path) -> None:
    from deerflow.office.projects import ProjectBinding, project_id_for_path
    from deerflow.office.run_gate import EmployeeRunGate

    app = FastAPI()
    employee_gate = EmployeeRunGate()
    app.state.office_employee_run_gate = employee_gate
    project = tmp_path / "project-one"
    project.mkdir()
    binding = ProjectBinding(project_id_for_path(project), project)

    running = _persisted_run("run-running", status=RunStatus.running)
    pending = _persisted_run("run-pending", status=RunStatus.pending)
    for record, employee_id, lease_id in (
        (running, "employee-one", "lease-running"),
        (pending, "employee-two", "lease-pending"),
    ):
        record.metadata = {
            "office_employee_id": employee_id,
            "office_conversation_kind": "group",
            "office_lease_id": lease_id,
            "office_project_write_required": True,
        }
        contract = _launch_contract()
        contract["trusted_context"] = binding.to_context()
        record.kwargs = {"launch_contract": contract}

    active = await gateway_deps._restore_office_execution_leases(
        app,
        [running, pending],
    )

    assert active == {"employee-one", "employee-two"}
    assert await employee_gate.get_admission("run:run-running") is not None
    assert await employee_gate.get_admission("run:run-pending") is not None


@pytest.mark.anyio
async def test_office_recovery_never_restores_obsolete_whole_run_write_lease():
    from deerflow.office.run_gate import EmployeeRunGate

    app = FastAPI()
    employee_gate = EmployeeRunGate()
    app.state.office_employee_run_gate = employee_gate
    record = _persisted_run("run-invalid-project")
    record.metadata = {
        "office_employee_id": "employee-one",
        "office_conversation_kind": "group",
        "office_lease_id": "lease-invalid",
        "office_project_write_required": True,
    }
    record.kwargs = {"launch_contract": _launch_contract()}

    # Restoration concerns employee admission only; prepare_persisted_run validates
    # the launch contract before any tool can acquire operation resources.
    await gateway_deps._restore_office_execution_leases(app, [record])
    assert await employee_gate.get_admission('run:run-invalid-project') is not None


@pytest.mark.anyio
async def test_office_lease_recovery_rejects_missing_conversation_identity():
    from deerflow.office.run_gate import EmployeeRunGate

    app = FastAPI()
    app.state.office_employee_run_gate = EmployeeRunGate()
    record = _persisted_run("run-missing-conversation")
    record.metadata = {
        "office_employee_id": "employee-one",
        "office_lease_id": "lease-missing-conversation",
        "office_project_write_required": False,
    }

    with pytest.raises(RuntimeError, match="invalid Office recovery metadata"):
        await gateway_deps._restore_office_execution_leases(app, [record])


@pytest.mark.anyio
async def test_prepare_recovery_uses_owned_checkpoint_without_replaying_input(monkeypatch):
    owned = _checkpoint(
        'run-original',
        'checkpoint-owned',
        checkpoint_ns='agent:employee-one',
        checkpoint_map={'': 'checkpoint-root'},
    )
    checkpointer = _RecoveryCheckpointer(
        [_checkpoint('run-other', 'checkpoint-other'), owned]
    )
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-original')
    record.kwargs = {'launch_contract': _launch_contract()}
    resolved_config, model_calls = _install_model_binding(monkeypatch, run_context)

    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)
    monkeypatch.setattr(gateway_services, 'resolve_agent_factory', lambda _assistant_id: object())

    prepared = await gateway_services.prepare_persisted_run(
        record,
        request,
        recovery=True,
    )

    assert prepared.record is record
    assert prepared.owner_user.id == 'owner-one'
    assert prepared.ctx.app_config is resolved_config
    assert model_calls == [(run_context.app_config, MODEL_PROFILE_ID, ())]
    assert prepared.config['metadata']['runtime_model_id'] == RUNTIME_MODEL_ID
    assert prepared.graph_input is None
    assert prepared.recovery is True
    assert prepared.config['configurable'] == {
        'thread_id': 'thread-one',
        'checkpoint_ns': 'agent:employee-one',
        'checkpoint_id': 'checkpoint-owned',
        'checkpoint_map': {'': 'checkpoint-root'},
    }
    assert prepared.config['context']['agent_name'] == 'employee-one'
    assert prepared.config['context']['model_name'] == SELECTED_MODEL_NAME
    assert checkpointer.seen_config == {
        'configurable': {
            'thread_id': 'thread-one',
        }
    }


@pytest.mark.anyio
async def test_prepare_recovery_rehydrates_office_employee_model_profiles(
    monkeypatch,
) -> None:
    checkpointer = _RecoveryCheckpointer(
        [_checkpoint('run-original', 'checkpoint-owned')]
    )
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-original')
    record.kwargs = {'launch_contract': _launch_contract()}
    record.metadata = {
        'office_employee_registry_snapshot': [
            {'employee_id': 'frontend-a', 'model_profile_id': 'worker-profile'},
            {'employee_id': 'backend-b', 'model_profile_id': MODEL_PROFILE_ID},
        ]
    }
    _, model_calls = _install_model_binding(monkeypatch, run_context)
    monkeypatch.setattr(
        gateway_services,
        'get_run_context',
        lambda _request: run_context,
    )
    monkeypatch.setattr(
        gateway_services,
        'resolve_agent_factory',
        lambda _assistant_id: object(),
    )

    await gateway_services.prepare_persisted_run(
        record,
        request,
        recovery=True,
    )

    assert model_calls == [
        (
            run_context.app_config,
            MODEL_PROFILE_ID,
            ('worker-profile', MODEL_PROFILE_ID),
        )
    ]


@pytest.mark.anyio
async def test_prepare_persisted_run_rehydrates_trusted_office_project_context(monkeypatch, tmp_path):
    from deerflow.office.projects import project_id_for_path
    from deerflow.runtime.context_keys import (
        OFFICE_PROJECT_ID_CONTEXT_KEY,
        OFFICE_PROJECT_PATH_CONTEXT_KEY,
    )

    checkpointer = _RecoveryCheckpointer([])
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-office-context', status=RunStatus.pending)
    project_path = tmp_path / 'project'
    project_path.mkdir()
    launch_contract = _launch_contract()
    office_context = {
        OFFICE_PROJECT_ID_CONTEXT_KEY: project_id_for_path(project_path),
        OFFICE_PROJECT_PATH_CONTEXT_KEY: str(project_path),
        'office_work': {'user_id': record.user_id, 'project_id': project_id_for_path(project_path),
                        'thread_id': record.thread_id, 'work_id': 'work-one'},
        'office_work_context': {'latest_user_message': 'trusted work'},
    }
    launch_contract['trusted_context'] = office_context
    record.kwargs = {'launch_contract': launch_contract}
    _install_model_binding(monkeypatch, run_context)
    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)
    monkeypatch.setattr(gateway_services, 'resolve_agent_factory', lambda _assistant_id: object())

    prepared = await gateway_services.prepare_persisted_run(
        record,
        request,
        recovery=False,
    )

    for key, value in office_context.items():
        assert prepared.config['context'][key] == value
        assert key not in prepared.config['configurable']


@pytest.mark.anyio
async def test_prepare_persisted_run_ignores_untrusted_office_project_context(monkeypatch, tmp_path):
    from deerflow.office.projects import project_id_for_path
    from deerflow.runtime.context_keys import (
        OFFICE_PROJECT_ID_CONTEXT_KEY,
        OFFICE_PROJECT_PATH_CONTEXT_KEY,
    )

    project_path = tmp_path / 'project'
    project_path.mkdir()
    checkpointer = _RecoveryCheckpointer([])
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-unbacked-office-context', status=RunStatus.pending)
    launch_contract = _launch_contract()
    launch_contract['context'].update(
        {
            OFFICE_PROJECT_ID_CONTEXT_KEY: project_id_for_path(project_path),
            OFFICE_PROJECT_PATH_CONTEXT_KEY: str(project_path),
            'office_work': {'user_id': 'attacker', 'work_id': 'spoofed'},
            'office_work_context': {'latest_user_message': 'spoofed'},
        }
    )
    record.kwargs = {'launch_contract': launch_contract}
    _install_model_binding(monkeypatch, run_context)
    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)
    monkeypatch.setattr(gateway_services, 'resolve_agent_factory', lambda _assistant_id: object())

    prepared = await gateway_services.prepare_persisted_run(
        record,
        request,
        recovery=False,
    )

    for key in (
        OFFICE_PROJECT_ID_CONTEXT_KEY,
        OFFICE_PROJECT_PATH_CONTEXT_KEY,
        'office_work',
        'office_work_context',
    ):
        assert key not in prepared.config['context']
        assert key not in prepared.config['configurable']


@pytest.mark.anyio
async def test_prepare_recovery_without_profile_preserves_native_model(monkeypatch):
    owned = _checkpoint('run-original', 'checkpoint-owned')
    checkpointer = _RecoveryCheckpointer([owned])
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-original')
    record.kwargs = {'launch_contract': _native_launch_contract()}

    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)
    monkeypatch.setattr(gateway_services, 'resolve_agent_factory', lambda _assistant_id: object())

    def reject_profile_resolution(*_args, **_kwargs):
        raise AssertionError('native recovery must not resolve an Electron model profile')

    monkeypatch.setattr(
        gateway_services,
        'resolve_run_app_config',
        reject_profile_resolution,
    )

    prepared = await gateway_services.prepare_persisted_run(
        record,
        request,
        recovery=True,
    )

    assert prepared.ctx.app_config is run_context.app_config
    assert prepared.config['context']['model_name'] == 'fake-test-model'
    assert prepared.config['configurable']['checkpoint_id'] == 'checkpoint-owned'
    assert prepared.graph_input is None




@pytest.mark.anyio
async def test_prepare_recovery_rejects_missing_owned_checkpoint(monkeypatch):
    checkpointer = _RecoveryCheckpointer(
        [_checkpoint('run-other', 'checkpoint-other')]
    )
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-original')
    record.kwargs = {'launch_contract': _launch_contract()}
    _install_model_binding(monkeypatch, run_context)
    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)
    monkeypatch.setattr(gateway_services, 'resolve_agent_factory', lambda _assistant_id: object())

    with pytest.raises(RuntimeError, match='owned checkpoint'):
        await gateway_services.prepare_persisted_run(
            record,
            request,
            recovery=True,
        )


@pytest.mark.anyio
async def test_prepare_recovery_rejects_deleted_model_profile(monkeypatch):
    checkpointer = _RecoveryCheckpointer(
        [_checkpoint('run-original', 'checkpoint-owned')]
    )
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-original')
    record.kwargs = {'launch_contract': _launch_contract()}
    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)

    def reject_deleted_profile(_base_config, profile_id):
        assert profile_id == MODEL_PROFILE_ID
        raise ModelBindingError('profile was deleted')

    monkeypatch.setattr(
        gateway_services,
        'resolve_run_app_config',
        reject_deleted_profile,
    )

    with pytest.raises(
        RuntimeError,
        match='cannot resolve model profile.*profile was deleted',
    ):
        await gateway_services.prepare_persisted_run(
            record,
            request,
            recovery=True,
        )

    assert checkpointer.seen_config is None


@pytest.mark.anyio
async def test_prepare_recovery_rejects_silent_runtime_model_change(monkeypatch):
    checkpointer = _RecoveryCheckpointer(
        [_checkpoint('run-original', 'checkpoint-owned')]
    )
    request, run_context = _recovery_request(checkpointer)
    record = _persisted_run('run-original')
    record.kwargs = {'launch_contract': _launch_contract()}
    _install_model_binding(
        monkeypatch,
        run_context,
        runtime_model_id='replacement-model',
    )
    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)
    monkeypatch.setattr(gateway_services, 'resolve_agent_factory', lambda _assistant_id: object())

    with pytest.raises(RuntimeError, match='runtime model changed'):
        await gateway_services.prepare_persisted_run(
            record,
            request,
            recovery=True,
        )

    assert checkpointer.seen_config is None


@pytest.mark.anyio
async def test_prepare_recovery_rejects_owner_context_mismatch(monkeypatch):
    checkpointer = _RecoveryCheckpointer(
        [_checkpoint('run-original', 'checkpoint-owned')]
    )
    request, run_context = _recovery_request(
        checkpointer,
        user_id='wrong-owner',
    )
    record = _persisted_run('run-original')
    record.kwargs = {'launch_contract': _launch_contract()}
    monkeypatch.setattr(gateway_services, 'get_run_context', lambda _request: run_context)

    with pytest.raises(RuntimeError, match='owner context'):
        await gateway_services.prepare_persisted_run(
            record,
            request,
            recovery=True,
        )
