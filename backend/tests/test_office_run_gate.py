# 本文件属于 Office 执行许可测试模块：只验证每员工单执行位、独立 FIFO、编辑、删除和释放。
# 不负责 DeerFlow Run 启动、项目写入、消息路由、历史查询或持久化恢复测试。
# 关联：office/run_gate.py 是被测门禁，message_pipeline.py 消费许可并启动排队项。
from __future__ import annotations

import asyncio

import pytest

from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun


def queued(
    queue_id: str,
    employee_id: str,
    text: str,
    *,
    source_kind: str = "direct",
    conversation_kind: str = "group",
    enqueued_at: int = 1,
    parent_run_id: str | None = None,
) -> QueuedOfficeRun:
    return QueuedOfficeRun(
        queue_id=queue_id,
        target_employee_id=employee_id,
        source_kind=source_kind,
        text=text,
        enqueued_at=enqueued_at,
        conversation_kind=conversation_kind,
        parent_run_id=parent_run_id,
    )


@pytest.mark.asyncio
async def test_idle_employee_gets_stable_lease_and_second_item_waits() -> None:
    gate = EmployeeRunGate()

    first = await gate.admit(queued("q1", "employee-a", "first"))
    duplicate = await gate.admit(queued("q1", "employee-a", "first"))
    second = await gate.admit(queued("q2", "employee-a", "second"))

    assert first.started is True
    assert first.lease_id
    assert duplicate == first
    assert second.started is False
    assert second.lease_id is None
    assert [item.queue_id for item in await gate.list_pending("employee-a")] == ["q2"]


@pytest.mark.asyncio
async def test_release_is_idempotent_and_starts_next_fifo_item() -> None:
    gate = EmployeeRunGate()
    first = await gate.admit(queued("q1", "employee-a", "first"))
    await gate.admit(queued("q2", "employee-a", "second"))

    released = await gate.release(first.lease_id or "")

    assert released is not None
    assert released.released_queue_id == "q1"
    assert released.next_admission is not None
    assert released.next_admission.queue_id == "q2"
    assert released.next_admission.started is True
    assert await gate.release(first.lease_id or "") is None


@pytest.mark.asyncio
async def test_waiting_item_receives_the_promoted_lease_and_latest_text() -> None:
    gate = EmployeeRunGate()
    first = await gate.admit(queued("q1", "employee-a", "active"))
    await gate.admit(queued("q2", "employee-a", "old text"))
    waiter = asyncio.create_task(gate.wait_until_started("q2"))
    await asyncio.sleep(0)
    assert not waiter.done()

    await gate.edit("q2", "new text")
    await gate.release(first.lease_id or "")
    promoted = await asyncio.wait_for(waiter, timeout=1)

    assert promoted.started is True
    assert promoted.lease_id
    assert (await gate.get_item("q2")).text == "new text"


@pytest.mark.asyncio
async def test_duplicate_queue_id_cannot_be_reused_for_different_work() -> None:
    gate = EmployeeRunGate()
    await gate.admit(queued("stable", "employee-a", "first"))

    with pytest.raises(ValueError, match="already belongs to another run"):
        await gate.admit(queued("stable", "employee-a", "changed"))


@pytest.mark.asyncio
async def test_each_employee_has_an_independent_fifo() -> None:
    gate = EmployeeRunGate()

    a = await gate.admit(queued("a1", "employee-a", "a"))
    b = await gate.admit(queued("b1", "employee-b", "b"))
    await gate.admit(queued("a2", "employee-a", "a2"))

    assert a.started is True
    assert b.started is True
    assert await gate.list_pending("employee-b") == []
    assert [item.queue_id for item in await gate.list_pending()] == ["a2"]


@pytest.mark.asyncio
async def test_pending_item_edit_keeps_identity_time_target_and_position() -> None:
    gate = EmployeeRunGate()
    await gate.admit(queued("q1", "employee-a", "active", enqueued_at=10))
    await gate.admit(
        queued(
            "q2",
            "employee-a",
            "old",
            enqueued_at=20,
            conversation_kind="private",
        )
    )
    await gate.admit(queued("q3", "employee-a", "third", enqueued_at=30))

    edited = await gate.edit("q2", "new")

    assert edited == queued(
        "q2",
        "employee-a",
        "new",
        enqueued_at=20,
        conversation_kind="private",
    )
    assert [item.queue_id for item in await gate.list_pending("employee-a")] == ["q2", "q3"]


@pytest.mark.asyncio
async def test_busy_delegation_is_not_queued_and_direct_fifo_is_preserved():
    gate = EmployeeRunGate()
    active = await gate.admit(queued("q1", "employee-a", "active"))
    direct = await gate.admit(queued("q2", "employee-a", "user followup"))
    delegated = await gate.admit(queued("q3", "employee-a", "PM task", source_kind="pm_delegate", parent_run_id="pm-1"))
    assert not delegated.started
    assert await gate.get_admission("q3") is None
    assert not direct.started
    assert [item.queue_id for item in await gate.list_pending()] == ["q2"]
    assert await gate.cancel_parent("pm-1") == []
    with pytest.raises(ValueError, match="already started"):
        await gate.delete("q1")
    released = await gate.release(active.lease_id)
    assert released.next_admission.queue_id == "q2"
    assert released.next_admission.started


@pytest.mark.asyncio
async def test_two_simultaneous_pm_delegations_only_admit_one():
    gate = EmployeeRunGate()
    admissions = await asyncio.gather(*[
        gate.admit(queued(identity, "employee-a", "PM task", source_kind="pm_delegate", parent_run_id="pm-1"))
        for identity in ["first", "second"]
    ])
    assert sum(admission.started for admission in admissions) == 1
    assert await gate.list_pending() == []
    active = next(admission for admission in admissions if admission.started)
    duplicate = await gate.admit(queued(active.queue_id, "employee-a", "PM task", source_kind="pm_delegate", parent_run_id="pm-1"))
    assert not duplicate.started
    assert await gate.get_admission(active.queue_id) == active
    await gate.release(active.lease_id)


def test_queue_item_rejects_unknown_conversation_kind() -> None:
    with pytest.raises(ValueError, match="conversation_kind"):
        queued("q1", "employee-a", "work", conversation_kind="unknown")


def test_queue_item_requires_explicit_conversation_kind() -> None:
    with pytest.raises(TypeError, match="conversation_kind"):
        QueuedOfficeRun("q1", "employee-a", "direct", "work", 1)
