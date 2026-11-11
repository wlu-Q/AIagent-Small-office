# 本文件属于 Office 执行许可恢复测试模块：只验证 DeerFlow Run 终态、重启恢复和历史投影对同一许可的处理。
# 不负责 Agent Loop、消息内容质量、持久化队列、第二调度器或前端交互。
# 关联：run_projection.py 释放许可，run_gate.py 保存员工进程内准入事实，deps.py 恢复未完成 Run。
from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun
from deerflow.office.run_projection import OfficeRunProjection
from deerflow.runtime.runs.schemas import RunStatus


class EmployeeServiceStub:
    def __init__(self) -> None:
        self.summary = {
            "id": "employee-one",
            "canDelegate": False,
            "state": "idle",
            "currentTaskId": None,
        }
        self.updates: list[dict[str, object]] = []

    @staticmethod
    def validate_employee_id(employee_id: str) -> str:
        if employee_id != "employee-one":
            raise ValueError("unknown employee")
        return employee_id

    @staticmethod
    def thread_id(employee_id: str, kind: str) -> str:
        return f"office__default__{employee_id}__{kind}"

    def get_employee_summary(self, _employee_id: str) -> dict[str, object]:
        return dict(self.summary)

    def update_runtime_projection(
        self,
        _employee_id: str,
        patch: dict[str, object],
    ) -> None:
        self.summary.update(patch)
        self.updates.append(dict(patch))


def _record(
    run_id: str,
    task_id: str,
    lease_id: str,
    status: RunStatus,
) -> SimpleNamespace:
    return SimpleNamespace(
        kwargs={},
        run_id=run_id,
        user_id="default",
        assistant_id="employee-one",
        thread_id="office__default__employee-one__group",
        status=status,
        last_ai_message="completed" if status == RunStatus.success else None,
        metadata={
            "office_employee_id": "employee-one",
            "office_conversation_kind": "group",
            "office_task_id": task_id,
            "office_lease_id": lease_id,
            "office_project_write_required": True,
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "terminal_status",
    [RunStatus.success, RunStatus.error, RunStatus.timeout, RunStatus.interrupted],
)
async def test_direct_run_terminal_releases_employee_once(
    terminal_status: RunStatus,
) -> None:
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
    lease_id = admission.lease_id or ""
    employees = EmployeeServiceStub()
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=employee_gate,
    )
    record = _record("run-one", "task-one", lease_id, RunStatus.running)
    await projection.handle_run_started(record)

    record.status = terminal_status
    record.last_ai_message = (
        "completed" if terminal_status == RunStatus.success else None
    )
    await projection.handle_run_completed(record)
    await projection.handle_run_completed(record)

    assert await employee_gate.get_admission("queue-one") is None
    assert employees.summary["state"] == "idle"
    assert employees.summary["currentTaskId"] is None
    assert len(employees.updates) == 2


@pytest.mark.anyio
async def test_late_duplicate_terminal_does_not_release_or_reset_the_next_run() -> None:
    employee_gate = EmployeeRunGate()
    first = await employee_gate.admit(
        QueuedOfficeRun(
            "queue-one",
            "employee-one",
            "direct",
            "first",
            1,
            conversation_kind="group",
        )
    )
    second = await employee_gate.admit(
        QueuedOfficeRun(
            "queue-two",
            "employee-one",
            "direct",
            "second",
            2,
            conversation_kind="group",
        )
    )
    first_lease = first.lease_id or ""
    employees = EmployeeServiceStub()
    projection = OfficeRunProjection(
        employee_service=employees,
        user_id="default",
        employee_run_gate=employee_gate,
    )
    first_record = _record("run-one", "task-one", first_lease, RunStatus.running)
    await projection.handle_run_started(first_record)
    first_record.status = RunStatus.success
    first_record.last_ai_message = "first completed"
    await projection.handle_run_completed(first_record)

    second_admission = await employee_gate.wait_until_started("queue-two")
    second_record = _record(
        "run-two",
        "task-two",
        second_admission.lease_id or "",
        RunStatus.running,
    )
    await projection.handle_run_started(second_record)
    await projection.handle_run_completed(first_record)

    assert second.started is False
    assert employees.summary["state"] == "working"
    assert employees.summary["currentTaskId"] == "task-two"
    assert await employee_gate.get_admission("queue-two") == second_admission


@pytest.mark.anyio
async def test_restore_active_reuses_persisted_lease_without_restoring_fifo() -> None:
    gate = EmployeeRunGate()
    item = QueuedOfficeRun(
        "run:run-one",
        "employee-one",
        "direct",
        "recovered work",
        0,
        conversation_kind="group",
    )

    first = await gate.restore_active(item, "lease-persisted")
    duplicate = await gate.restore_active(item, "lease-persisted")

    assert first == duplicate
    assert first.lease_id == "lease-persisted"
    assert first.started is True
    assert await gate.get_admission(item.queue_id) == first
    assert await gate.list_pending() == []


@pytest.mark.anyio
async def test_restore_active_rejects_another_lease_for_the_same_employee() -> None:
    gate = EmployeeRunGate()
    await gate.restore_active(
        QueuedOfficeRun(
            "run:first",
            "employee-one",
            "direct",
            "first",
            0,
            conversation_kind="group",
        ),
        "lease-first",
    )

    with pytest.raises(ValueError, match="employee already has an active lease"):
        await gate.restore_active(
            QueuedOfficeRun(
                "run:second",
                "employee-one",
                "direct",
                "second",
                0,
                conversation_kind="group",
            ),
            "lease-second",
        )
