# 本文件属于 Office Run 投影测试模块：只验证 DeerFlow 生命周期到员工状态和群聊最终回复的投影。
# 不负责消息路由、员工排队、Run 启动、历史查询或 DeerFlow Agent Loop 测试。
# 关联：run_projection.py 是被测模块，employees.py 与 group_messages.py 提供投影目标边界。
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun
from deerflow.office.run_projection import OfficeRunProjection
from deerflow.runtime.runs.schemas import RunStatus


def test_projection_has_no_run_query_or_startup_reconciliation_dependency() -> None:
    parameters = inspect.signature(OfficeRunProjection).parameters

    assert "run_manager" not in parameters
    assert hasattr(OfficeRunProjection, "reconcile_startup")


class EmployeeServiceStub:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, object]]] = []
        self.summaries = {
            "employee-one": {
                "id": "employee-one",
                "displayName": "Old name",
                "avatar": "adam",
                "canDelegate": False,
                "state": "idle",
                "currentTaskId": None,
            },
            "employee-two": {
                "id": "employee-two",
                "canDelegate": False,
                "state": "working",
                "currentTaskId": "stale-task",
            },
            "pm": {
                "id": "pm",
                "canDelegate": True,
                "state": "idle",
                "currentTaskId": None,
            },
        }

    def validate_employee_id(self, employee_id: str) -> str:
        if employee_id not in self.summaries:
            raise ValueError("unknown employee")
        return employee_id

    @staticmethod
    def thread_id(employee_id: str, kind: str) -> str:
        return f"office__default__{employee_id}__{kind}"

    def update_runtime_projection(
        self,
        employee_id: str,
        patch: dict[str, object],
    ) -> None:
        self.updates.append((employee_id, patch))
        self.summaries[employee_id].update(patch)

    def get_employee_summary(self, employee_id: str) -> dict[str, object]:
        return dict(self.summaries[employee_id])

    async def list_employees(self) -> list[dict[str, object]]:
        return [dict(summary) for summary in self.summaries.values()]


class GroupStoreStub:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.pm_cursor = 0

    def append(self, **message: object) -> None:
        self.messages.append(message)

    def advance_pm_cursor(self, sequence: int) -> int:
        self.pm_cursor = sequence
        return sequence






def _record(
    status: RunStatus,
    *,
    conversation_kind: str = "group",
    last_ai_message: str | None = None,
    lease_id: str = "lease-one",
    run_id: str = "run-one",
    task_id: str = "task-one",
):
    return SimpleNamespace(
        kwargs={},
        run_id=run_id,
        user_id="default",
        assistant_id="employee-one",
        thread_id=f"office__default__employee-one__{conversation_kind}",
        status=status,
        last_ai_message=last_ai_message,
        metadata={
            "office_employee_id": "employee-one",
            "office_conversation_kind": conversation_kind,
            "office_task_id": task_id,
            "office_lease_id": lease_id,
            "office_project_write_required": False,
        },
        tool_events=[{"name": "apply_patch"}],
        checkpoint={"id": "checkpoint-one"},
    )


def _pm_record(status: RunStatus, *, sequence_end: int = 7):
    return SimpleNamespace(
        kwargs={},
        run_id="run-pm",
        user_id="default",
        assistant_id="pm",
        thread_id="office__default__pm__group",
        status=status,
        last_ai_message=None,
        metadata={
            "office_employee_id": "pm",
            "office_conversation_kind": "group",
            "office_group_sequence_end": sequence_end,
        },
    )


@pytest.mark.anyio
async def test_pm_cursor_advances_only_from_input_checkpoint_hook() -> None:
    employees = EmployeeServiceStub()
    group_store = GroupStoreStub()
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=EmployeeRunGate(),
        group_store=group_store,
    )
    record = _pm_record(RunStatus.running)

    await projection.handle_run_started(record)
    assert group_store.pm_cursor == 0

    await projection.handle_run_input_checkpointed(record)
    assert group_store.pm_cursor == 7

    record.status = RunStatus.error
    await projection.handle_run_completed(record)
    assert group_store.pm_cursor == 7


@pytest.mark.anyio
@pytest.mark.parametrize("status", [RunStatus.pending, RunStatus.running])
async def test_projection_marks_only_active_office_runs_as_working(status: RunStatus) -> None:
    employees = EmployeeServiceStub()
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=EmployeeRunGate(),
    )

    await projection.handle_run_started(_record(status))

    assert employees.updates == [
        (
            "employee-one",
            {
                "state": "working",
                "statusDetail": "正在执行任务",
                "currentTaskId": "task-one",
            },
        )
    ]




@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [RunStatus.success, RunStatus.interrupted, RunStatus.error, RunStatus.timeout],
)
async def test_projection_returns_every_terminal_office_run_to_idle(status: RunStatus) -> None:
    employees = EmployeeServiceStub()
    employee_gate = EmployeeRunGate()
    admission = await employee_gate.admit(
        QueuedOfficeRun(
            "queue-one",
            "employee-one",
            "direct",
            "work",
            1,
            conversation_kind="group",
        )
    )
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=employee_gate,
    )
    employees.summaries["employee-one"].update(
        {"state": "working", "currentTaskId": "task-one"}
    )

    await projection.handle_run_completed(
        _record(
            status,
            last_ai_message="completed" if status == RunStatus.success else None,
            lease_id=admission.lease_id or "",
        )
    )

    expected_patch: dict[str, object] = {
        "state": "idle",
        "statusDetail": "等待新任务",
        "currentTaskId": None,
    }
    if status == RunStatus.success:
        expected_patch["lastReport"] = "completed"
    assert employees.updates == [("employee-one", expected_patch)]


@pytest.mark.anyio
async def test_projection_releases_employee_without_touching_operation_leases() -> None:
    employees = EmployeeServiceStub()
    employee_gate = EmployeeRunGate()
    admission = await employee_gate.admit(
        QueuedOfficeRun(
            "queue-project-write",
            "employee-one",
            "direct",
            "write project",
            1,
            conversation_kind="private",
        )
    )
    employees.summaries["employee-one"].update(
        {"state": "working", "currentTaskId": "task-project-write"}
    )
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=employee_gate,
    )
    record = _record(
        RunStatus.error,
        conversation_kind="private",
        lease_id=admission.lease_id or "",
        task_id="task-project-write",
    )
    record.metadata["office_project_write_required"] = True

    await projection.handle_run_completed(record)

    assert await employee_gate.get_admission("queue-project-write") is None
    assert employees.summaries["employee-one"]["state"] == "idle"
    assert employees.summaries["employee-one"]["currentTaskId"] is None


@pytest.mark.anyio
async def test_projection_appends_only_the_group_run_final_reply() -> None:
    employees = EmployeeServiceStub()
    group_store = GroupStoreStub()
    employee_gate = EmployeeRunGate()
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=employee_gate,
        group_store=group_store,
    )

    group_admission = await employee_gate.admit(
        QueuedOfficeRun(
            "queue-group",
            "employee-one",
            "direct",
            "group",
            1,
            conversation_kind="group",
        )
    )
    employees.summaries["employee-one"].update(
        {"state": "working", "currentTaskId": "task-group"}
    )
    await projection.handle_run_completed(
        _record(
            RunStatus.success,
            last_ai_message="用户可见最终回复",
            lease_id=group_admission.lease_id or "",
            run_id="run-group",
            task_id="task-group",
        )
    )
    private_admission = await employee_gate.admit(
        QueuedOfficeRun(
            "queue-private",
            "employee-one",
            "direct",
            "private",
            2,
            conversation_kind="private",
        )
    )
    employees.summaries["employee-one"].update(
        {"state": "working", "currentTaskId": "task-private"}
    )
    await projection.handle_run_completed(
        _record(
            RunStatus.success,
            conversation_kind="private",
            last_ai_message="私聊最终回复",
            lease_id=private_admission.lease_id or "",
            run_id="run-private",
            task_id="task-private",
        )
    )

    assert group_store.messages == [
        {
            "message_id": "run:run-group:final",
            "sender_kind": "employee",
            "sender_id": "employee-one",
            "sender_display_name": "Old name",
            "sender_avatar": "adam",
            "content": "用户可见最终回复",
            "source_run_id": "run-group",
        }
    ]


@pytest.mark.anyio
async def test_projection_ignores_runs_without_a_complete_office_identity() -> None:
    employees = EmployeeServiceStub()
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=EmployeeRunGate(),
    )
    invalid_records = [
        SimpleNamespace(
            **{
                **_record(RunStatus.running).__dict__,
                "metadata": {},
            }
        ),
        SimpleNamespace(
            **{
                **_record(RunStatus.running).__dict__,
                "thread_id": "wrong-thread",
            }
        ),
        SimpleNamespace(
            **{
                **_record(RunStatus.running).__dict__,
                "user_id": "another-user",
            }
        ),
    ]

    for record in invalid_records:
        await projection.handle_run_started(record)

    assert employees.updates == []


@pytest.mark.anyio
async def test_startup_reconciliation_resets_only_working_employees_without_active_lease() -> None:
    employees = EmployeeServiceStub()
    employees.summaries["employee-one"].update(
        {"state": "working", "currentTaskId": "active-task"}
    )
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=EmployeeRunGate(),
    )

    await projection.reconcile_startup({"employee-one"})

    assert employees.updates == [
        (
            "employee-two",
            {
                "state": "idle",
                "statusDetail": "等待新任务",
                "currentTaskId": None,
            },
        )
    ]
