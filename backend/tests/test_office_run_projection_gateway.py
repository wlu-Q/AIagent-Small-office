# 本文件属于 Office Run 投影 Gateway 接线测试模块：只验证进程级窄服务装配和生命周期 hook 复用。
# 不负责消息路由、员工 FIFO、模型推理、RunStore 实现或前端状态展示测试。
# 关联：gateway/deps.py、office/run_projection.py 与 DeerFlow 生命周期 hook 共同组成投影接线。
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.gateway import deps as gateway_deps
from app.gateway.routers import office
from deerflow.office.run_gate import (
    get_process_employee_run_gate,
)
from deerflow.runtime.runs.manager import CancelOutcome

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


@pytest.mark.anyio
async def test_gateway_registers_bootstrap_service_before_exposing_office_services(
    monkeypatch,
    tmp_path,
) -> None:
    events: list[object] = []

    class EmployeeService:
        def __init__(self, **kwargs):
            self.user_id = kwargs["user_id"]
            self.paths = object()
            self.model_profile_exists = kwargs["model_profile_exists"]
            events.append("employee-service")

        async def ensure_builtin_pm(self, *, default_model_profile_id):
            events.append(("pm", default_model_profile_id))

    class BootstrapService:
        def __init__(self, **kwargs):
            assert kwargs["employee_service"].user_id == "default"
            self.project_service = kwargs["project_service"]
            assert isinstance(self.project_service, ProjectService)
            events.append("bootstrap-service")

    class ProjectService:
        def __init__(self, **_kwargs):
            events.append("project-service")

    class GroupStore:
        def __init__(self, **_kwargs):
            pass

    class Projection:
        def __init__(self, **_kwargs):
            events.append("projection")

    current_config = SimpleNamespace(
        models=[SimpleNamespace(name="profile-primary")],
        get_model_config=lambda name: object() if name == "profile-primary" else None,
    )
    app = FastAPI()
    app.state.thread_store = object()
    app.state.checkpointer = object()
    app.state.run_manager = object()
    app.state.run_event_store = object()

    from deerflow.config.paths import Paths

    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: Paths(tmp_path))
    monkeypatch.setattr("deerflow.office.employees.OfficeEmployeeService", EmployeeService)
    monkeypatch.setattr("deerflow.office.bootstrap.OfficeBootstrapService", BootstrapService)
    monkeypatch.setattr("deerflow.office.group_messages.GroupMessageStore", GroupStore)
    monkeypatch.setattr("deerflow.office.projects.OfficeProjectService", ProjectService)
    monkeypatch.setattr(
        "deerflow.office.run_projection.OfficeRunProjection",
        Projection,
    )
    monkeypatch.setattr(
        "deerflow.office.run_view.OfficeRunView",
        lambda **_kwargs: SimpleNamespace(user_id="default"),
    )
    monkeypatch.setattr(
        "deerflow.office.run_launch.OfficeRunLauncher",
        lambda **kwargs: SimpleNamespace(employee_service=kwargs["employee_service"]),
    )
    monkeypatch.setattr(gateway_deps, "get_app_config", lambda: current_config)
    monkeypatch.setattr(
        gateway_deps,
        "model_profile_exists",
        lambda config, name: config is current_config and name == "profile-primary",
    )

    await gateway_deps._initialize_office_runtime_services(app, current_config)

    service = app.state.office_employee_service
    assert events.index("employee-service") < events.index("bootstrap-service")
    assert events.index("project-service") < events.index("bootstrap-service")
    assert events.index("bootstrap-service") < events.index("projection")
    assert isinstance(app.state.office_bootstrap_service, BootstrapService)
    assert app.state.office_bootstrap_service.project_service is app.state.office_project_service
    assert service.model_profile_exists("profile-primary") is True
    assert service.model_profile_exists("missing") is False
    assert get_process_employee_run_gate() is app.state.office_employee_run_gate
    assert task_tool_module._office_employee_gate() is app.state.office_employee_run_gate

def test_gateway_exposes_only_the_narrow_office_run_services(monkeypatch):
    employee_gate = object()
    employee_service = SimpleNamespace(user_id="default")
    run_launcher = SimpleNamespace(employee_service=employee_service)
    run_view = SimpleNamespace(user_id="default")
    message_pipeline = SimpleNamespace(user_id="default")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                office_employee_run_gate=employee_gate,
                office_run_launcher=run_launcher,
                office_run_view=run_view,
                office_message_pipeline=message_pipeline,
            )
        )
    )
    monkeypatch.setattr(office, "get_effective_user_id", lambda: "default")

    assert office.get_office_employee_run_gate(request) is employee_gate
    assert office.get_office_run_view(request) is run_view
    assert office.get_office_message_pipeline(request) is message_pipeline
    assert not hasattr(office, "get_office_run_launcher")
    assert not hasattr(office, "get_office_run_service")


def test_legacy_mixed_office_run_modules_are_physically_absent() -> None:
    from deerflow.office import messages

    assert importlib.util.find_spec("deerflow.office.runs") is None
    assert not hasattr(messages, "MessageDispatchRequest")
    assert not hasattr(messages, "OfficeMessageService")


def test_office_run_routes_use_view_and_native_run_manager(monkeypatch) -> None:
    monkeypatch.setattr(office, "get_effective_user_id", lambda: "default")
    record = SimpleNamespace(run_id="run-one", status="running")

    class View:
        user_id = "default"

        @staticmethod
        async def list_runs(employee_id, kind, *, limit):
            assert (employee_id, kind, limit) == (
                "employee-one",
                "private",
                10,
            )
            return {
                "runs": [
                    {
                        "runId": "run-completed",
                        "employeeId": employee_id,
                        "status": "success",
                        "eventsFinalized": True,
                    }
                ]
            }

        @staticmethod
        async def get_run(employee_id, run_id):
            assert (employee_id, run_id) == ("employee-one", "run-one")
            return {"runId": run_id, "employeeId": employee_id, "status": "running"}

        @staticmethod
        async def list_events(employee_id, run_id, *, after_seq, limit):
            assert (employee_id, run_id, after_seq, limit) == (
                "employee-one",
                "run-one",
                3,
                10,
            )
            return {"events": [{"seq": 4}], "nextAfterSeq": 4}

        @staticmethod
        async def resolve_run(employee_id, run_id):
            assert (employee_id, run_id) == ("employee-one", "run-one")
            return employee_id, record

        @staticmethod
        def is_pm(_employee_id):
            return False

        @staticmethod
        def serialize_run(employee_id, current):
            return {
                "runId": current.run_id,
                "employeeId": employee_id,
                "status": "interrupted",
            }

    class RunManager:
        cancel_calls: list[tuple[str, str, str]] = []

        async def cancel(self, run_id, *, action, user_id):
            self.cancel_calls.append((run_id, action, user_id))
            return CancelOutcome.cancelled

        @staticmethod
        async def get(run_id, *, user_id):
            assert (run_id, user_id) == ("run-one", "default")
            return record

    run_manager = RunManager()
    app = FastAPI()
    app.include_router(office.router)
    app.dependency_overrides[office.get_office_run_view] = View
    app.state.run_manager = run_manager
    app.state.office_message_pipeline = SimpleNamespace(user_id="default", cancel_wait=AsyncMock())
    app.state.office_employee_run_gate = SimpleNamespace(cancel_parent=None)

    with TestClient(app) as client:
        runs = client.get(
            "/api/office/employees/employee-one/runs",
            params={"conversationKind": "private", "limit": 10},
        )
        detail = client.get("/api/office/employees/employee-one/runs/run-one")
        events = client.get(
            "/api/office/employees/employee-one/runs/run-one/events",
            params={"after_seq": 3, "limit": 10},
        )
        cancelled = client.post(
            "/api/office/employees/employee-one/runs/run-one/cancel"
        )
        legacy_create = client.post(
            "/api/office/employees/employee-one/runs",
            json={"message": "legacy"},
        )

    assert runs.json() == {
        "runs": [
            {
                "runId": "run-completed",
                "employeeId": "employee-one",
                "status": "success",
                "eventsFinalized": True,
            }
        ]
    }
    assert detail.json() == {
        "runId": "run-one",
        "employeeId": "employee-one",
        "status": "running",
    }
    assert events.json() == {"events": [{"seq": 4}], "nextAfterSeq": 4}
    assert cancelled.json() == {
        "runId": "run-one",
        "employeeId": "employee-one",
        "status": "interrupted",
    }
    assert run_manager.cancel_calls == [("run-one", "interrupt", "default")]
    assert legacy_create.status_code == 405


@pytest.mark.anyio
async def test_cancelling_pm_run_removes_only_its_waiting_delegations(monkeypatch) -> None:
    monkeypatch.setattr(office, "get_effective_user_id", lambda: "default")
    order: list[str] = []
    record = SimpleNamespace(run_id="run-pm", status="running")

    class View:
        user_id = "default"

        @staticmethod
        async def resolve_run(employee_id, run_id):
            assert (employee_id, run_id) == ("pm", "run-pm")
            return employee_id, record

        @staticmethod
        def is_pm(employee_id):
            return employee_id == "pm"

        @staticmethod
        def serialize_run(employee_id, current):
            return {"runId": current.run_id, "employeeId": employee_id}

    class Gate:
        async def cancel_parent(self, parent_run_id):
            order.append(f"queue:{parent_run_id}")
            return [SimpleNamespace(queue_id="delegated-waiting")]

    class RunManager:
        async def cancel(self, run_id, *, action, user_id):
            order.append(f"run:{run_id}:{action}:{user_id}")
            return CancelOutcome.cancelled

        async def get(self, run_id, *, user_id):
            return record

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(run_manager=RunManager(), office_message_pipeline=SimpleNamespace(user_id="default", cancel_wait=AsyncMock())))
    )

    result = await office.cancel_employee_run(
        "pm",
        "run-pm",
        request,
        view=View(),
        gate=Gate(),
    )

    assert result == {"runId": "run-pm", "employeeId": "pm"}
    assert order == [
        "run:run-pm:interrupt:default",
        "queue:run-pm",
    ]


@pytest.mark.anyio
async def test_cancelling_pm_run_keeps_waiting_delegations_when_run_cancel_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(office, "get_effective_user_id", lambda: "default")
    order: list[str] = []
    record = SimpleNamespace(run_id="run-pm", status="running")

    class View:
        user_id = "default"

        @staticmethod
        async def resolve_run(employee_id, run_id):
            assert (employee_id, run_id) == ("pm", "run-pm")
            return employee_id, record

        @staticmethod
        def is_pm(employee_id):
            return employee_id == "pm"

        @staticmethod
        def serialize_run(employee_id, current):
            return {"runId": current.run_id, "employeeId": employee_id}

    class Gate:
        async def cancel_parent(self, parent_run_id):
            order.append(f"queue:{parent_run_id}")
            return [SimpleNamespace(queue_id="delegated-waiting")]

    class RunManager:
        async def cancel(self, run_id, *, action, user_id):
            order.append(f"run:{run_id}:{action}:{user_id}")
            return CancelOutcome.unknown

        async def get(self, run_id, *, user_id):
            return record

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(run_manager=RunManager(), office_message_pipeline=SimpleNamespace(user_id="default", cancel_wait=AsyncMock())))
    )

    with pytest.raises(HTTPException, match="cannot be cancelled locally"):
        await office.cancel_employee_run(
            "pm",
            "run-pm",
            request,
            view=View(),
            gate=Gate(),
        )

    assert order == ["run:run-pm:interrupt:default"]


def _request(*, projection=None, scheduler=None, employee_service=None):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=object(),
                store=object(),
                run_event_store=object(),
                run_events_config=object(),
                thread_store=object(),
                office_run_projection=projection,
                scheduled_task_service=scheduler,
                office_employee_service=employee_service,
            )
        )
    )


@pytest.mark.anyio
async def test_get_run_context_combines_office_and_scheduler_hooks(monkeypatch):
    events: list[str] = []

    class Projection:
        async def handle_run_starting(self, _record):
            events.append("office-starting")

        async def handle_run_started(self, _record):
            events.append("office-started")

        async def handle_run_input_checkpointed(self, _record):
            events.append("office-checkpointed")

        async def handle_run_completed(self, _record):
            events.append("office-completed")

    class Scheduler:
        async def handle_run_completion(self, _record):
            events.append("scheduler-completed")

    monkeypatch.setattr(gateway_deps, "get_config", lambda: object())
    context = gateway_deps.get_run_context(
        _request(projection=Projection(), scheduler=Scheduler())
    )
    record = SimpleNamespace(run_id="run-one")

    assert context.on_run_starting is None
    await context.on_run_started(record)
    await context.on_run_input_checkpointed(record)
    await context.on_run_completed(record)

    assert events == [
        "office-started",
        "office-checkpointed",
        "office-completed",
        "scheduler-completed",
    ]


@pytest.mark.anyio
async def test_run_context_hook_failure_does_not_block_remaining_hooks(monkeypatch):
    events: list[str] = []

    class Projection:
        async def handle_run_completed(self, _record):
            events.append("office-failed")
            raise RuntimeError("projection failed")

    class Scheduler:
        async def handle_run_completion(self, _record):
            events.append("scheduler-completed")

    monkeypatch.setattr(gateway_deps, "get_config", lambda: object())
    context = gateway_deps.get_run_context(
        _request(projection=Projection(), scheduler=Scheduler())
    )

    await context.on_run_completed(SimpleNamespace(run_id="run-one"))

    assert events == ["office-failed", "scheduler-completed"]


def test_office_router_reuses_gateway_employee_service(monkeypatch):
    service = SimpleNamespace(user_id="default")
    monkeypatch.setattr(office, "get_effective_user_id", lambda: "default")

    assert office.get_office_employee_service(
        _request(employee_service=service)
    ) is service


def test_office_router_rejects_missing_or_cross_user_service(monkeypatch):
    monkeypatch.setattr(office, "get_effective_user_id", lambda: "default")

    with pytest.raises(HTTPException) as missing:
        office.get_office_employee_service(_request())
    assert missing.value.status_code == 503

    with pytest.raises(HTTPException) as mismatch:
        office.get_office_employee_service(
            _request(employee_service=SimpleNamespace(user_id="another-user"))
        )
    assert mismatch.value.status_code == 403
