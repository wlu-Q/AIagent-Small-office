# 本文件属于 Office 执行许可模块：只维护每名员工单执行位、独立 FIFO 和进程内门禁实例。
# 不负责启动 DeerFlow Run、查询历史、保存 Run 状态、解释事件或建立持久化调度系统。
# 关联：message_pipeline.py 使用员工门禁；任务文件由独立 Execution Workspace 隔离。
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from typing import Literal

from deerflow.office.execution_facts import PendingAsk

@dataclass(frozen=True)
class QueuedOfficeRun:
    queue_id: str
    target_employee_id: str
    source_kind: Literal["direct", "pm_delegate"]
    text: str
    enqueued_at: int
    parent_run_id: str | None = None
    tool_call_id: str | None = None
    conversation_kind: Literal["private", "group"] = field(kw_only=True)

    def __post_init__(self) -> None:
        if not self.queue_id.strip():
            raise ValueError("queue_id is required")
        if not self.target_employee_id.strip():
            raise ValueError("target_employee_id is required")
        if self.source_kind not in {"direct", "pm_delegate"}:
            raise ValueError(f"Unsupported source_kind: {self.source_kind}")
        if self.conversation_kind not in {"private", "group"}:
            raise ValueError(
                f"Unsupported conversation_kind: {self.conversation_kind}"
            )
        if not self.text.strip():
            raise ValueError("text is required")


@dataclass(frozen=True)
class RunAdmission:
    queue_id: str
    lease_id: str | None
    started: bool


@dataclass(frozen=True)
class ReleasedAdmission:
    released_queue_id: str
    released_employee_id: str
    next_item: QueuedOfficeRun | None
    next_admission: RunAdmission | None


class EmployeeRunGate:
    """Own one in-process execution slot and FIFO per employee."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._held: set[str] = set()
        self._asks: dict[str, PendingAsk] = {}
        self._resume_asks: dict[str, PendingAsk] = {}
        self._group_stopping = False
        self._active_by_employee: dict[str, tuple[str, QueuedOfficeRun]] = {}
        self._active_by_lease: dict[str, QueuedOfficeRun] = {}
        self._pending_by_employee: dict[str, list[QueuedOfficeRun]] = {}
        self._admissions_by_queue: dict[str, RunAdmission] = {}
        self._items_by_queue: dict[str, QueuedOfficeRun] = {}
        self._waiters_by_queue: dict[str, asyncio.Future[RunAdmission]] = {}

    @staticmethod
    def _new_lease_id() -> str:
        return f"office-lease-{uuid.uuid4().hex}"

    async def admit(self, item: QueuedOfficeRun, *, hold: bool = False, resume_ask: PendingAsk | None = None, priority: bool = False) -> RunAdmission:
        async with self._lock:
            if resume_ask is not None and self._asks.get(item.target_employee_id) != resume_ask:
                raise ValueError('Resume does not match the employee pending ask')
            existing = self._admissions_by_queue.get(item.queue_id)
            if existing is not None:
                if self._items_by_queue[item.queue_id] != item:
                    raise ValueError("queue_id already belongs to another run")
                return RunAdmission(item.queue_id, None, False) if item.source_kind == "pm_delegate" else existing
            employee_id = item.target_employee_id
            if item.source_kind == "pm_delegate" and (
                employee_id in self._active_by_employee or self._blocked(item) or hold
                or self._pending_by_employee.get(employee_id)
                or (self._group_stopping and item.conversation_kind == "group")
            ):
                return RunAdmission(item.queue_id, None, False)
            if resume_ask is not None:
                self._resume_asks[item.queue_id] = resume_ask
            if hold or (self._group_stopping and item.conversation_kind == "group"):
                self._held.add(item.queue_id)
            if employee_id not in self._active_by_employee and not self._blocked(item):
                lease_id = self._new_lease_id()
                admission = RunAdmission(item.queue_id, lease_id, True)
                self._active_by_employee[employee_id] = (lease_id, item)
                self._active_by_lease[lease_id] = item
            else:
                admission = RunAdmission(item.queue_id, None, False)
                queue = self._pending_by_employee.setdefault(employee_id, [])
                queue.insert(0, item) if priority else queue.append(item)
                self._waiters_by_queue[item.queue_id] = asyncio.get_running_loop().create_future()
            self._admissions_by_queue[item.queue_id] = admission
            self._items_by_queue[item.queue_id] = item
            return admission

    async def restore_active(
        self,
        item: QueuedOfficeRun,
        lease_id: str,
    ) -> RunAdmission:
        """Restore one persisted active run without rebuilding the in-memory FIFO."""
        normalized_lease = str(lease_id or "").strip()
        if not normalized_lease:
            raise ValueError("lease_id is required")

        async with self._lock:
            existing = self._admissions_by_queue.get(item.queue_id)
            if existing is not None:
                if (
                    self._items_by_queue.get(item.queue_id) != item
                    or not existing.started
                    or existing.lease_id != normalized_lease
                ):
                    raise ValueError("queue_id already belongs to another run")
                return existing

            leased_item = self._active_by_lease.get(normalized_lease)
            if leased_item is not None and leased_item != item:
                raise ValueError("lease_id already belongs to another active run")

            active = self._active_by_employee.get(item.target_employee_id)
            if active is not None:
                active_lease, active_item = active
                if active_lease != normalized_lease or active_item != item:
                    raise ValueError("employee already has an active lease")

            admission = RunAdmission(item.queue_id, normalized_lease, True)
            self._active_by_employee[item.target_employee_id] = (
                normalized_lease,
                item,
            )
            self._active_by_lease[normalized_lease] = item
            self._admissions_by_queue[item.queue_id] = admission
            self._items_by_queue[item.queue_id] = item
            return admission

    async def get_item(self, queue_id: str) -> QueuedOfficeRun:
        async with self._lock:
            item = self._items_by_queue.get(str(queue_id or ""))
            if item is None:
                raise FileNotFoundError(f"Queued run not found: {queue_id}")
            return item

    async def get_admission(self, queue_id: str) -> RunAdmission | None:
        async with self._lock:
            return self._admissions_by_queue.get(str(queue_id or ""))

    async def wait_until_started(self, queue_id: str) -> RunAdmission:
        async with self._lock:
            admission = self._admissions_by_queue.get(str(queue_id or ""))
            if admission is None:
                raise FileNotFoundError(f"Queued run not found: {queue_id}")
            if admission.started:
                return admission
            waiter = self._waiters_by_queue[admission.queue_id]
        return await asyncio.shield(waiter)

    async def release(self, lease_id: str, *, pending_ask: PendingAsk | None = None) -> ReleasedAdmission | None:
        async with self._lock:
            item = self._active_by_lease.get(str(lease_id or ""))
            if item is None:
                return None
            employee_id = item.target_employee_id
            if pending_ask is not None:
                if pending_ask.employee_id != employee_id:
                    raise ValueError('Pending ask employee does not own this lease')
                self._asks[employee_id] = pending_ask
            self._active_by_lease.pop(lease_id)
            self._resume_asks.pop(item.queue_id, None)
            self._active_by_employee.pop(employee_id, None)
            self._admissions_by_queue.pop(item.queue_id, None)
            self._items_by_queue.pop(item.queue_id, None)
            self._held.discard(item.queue_id)
            next_item, next_admission = self._promote_unlocked(employee_id)
            return ReleasedAdmission(
                released_queue_id=item.queue_id,
                released_employee_id=employee_id,
                next_item=next_item,
                next_admission=next_admission,
            )

    def _promote_unlocked(self, employee_id):
        if employee_id in self._active_by_employee:
            return None, None
        queue = self._pending_by_employee.get(employee_id, [])
        next_item = next((item for item in queue if not self._blocked(item)), None)
        if next_item is None:
            return None, None
        queue.remove(next_item)
        if not queue:
            self._pending_by_employee.pop(employee_id, None)
        lease_id = self._new_lease_id()
        admission = RunAdmission(next_item.queue_id, lease_id, True)
        self._active_by_employee[employee_id] = (lease_id, next_item)
        self._active_by_lease[lease_id] = next_item
        self._admissions_by_queue[next_item.queue_id] = admission
        waiter = self._waiters_by_queue.pop(next_item.queue_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(admission)
        return next_item, admission

    def _blocked(self, item: QueuedOfficeRun) -> bool:
        waiting = self._asks.get(item.target_employee_id)
        return item.queue_id in self._held or (waiting is not None and self._resume_asks.get(item.queue_id) != waiting)

    async def pending_ask(self, employee_id: str) -> PendingAsk | None:
        async with self._lock:
            return self._asks.get(employee_id)

    async def restore_ask(self, pending: PendingAsk) -> None:
        async with self._lock:
            existing = self._asks.get(pending.employee_id)
            if existing is not None and existing != pending:
                raise ValueError('Employee already has another pending ask')
            self._asks[pending.employee_id] = pending

    async def consume_ask(self, pending: PendingAsk) -> bool:
        """Clear only this request after Resume checkpoints its input or explicit cancellation."""
        async with self._lock:
            if self._asks.get(pending.employee_id) != pending:
                return False
            self._asks.pop(pending.employee_id)
            self._promote_unlocked(pending.employee_id)
            return True

    async def pause_group(self):
        async with self._lock:
            self._group_stopping = True
            self._held.update(item.queue_id for item in self._items_by_queue.values() if item.conversation_kind == 'group')

    async def finish_group_pause(self):
        async with self._lock:
            self._group_stopping = False

    async def is_held(self, queue_id):
        async with self._lock:
            return queue_id in self._held

    async def resume_group(self):
        async with self._lock:
            if self._group_stopping:
                raise ValueError('群聊任务仍在停止，请稍后继续')
            self._held.clear()
            for employee_id in list(self._pending_by_employee):
                self._promote_unlocked(employee_id)

    async def list_pending(
        self,
        employee_id: str | None = None,
    ) -> list[QueuedOfficeRun]:
        async with self._lock:
            if employee_id is not None:
                return list(self._pending_by_employee.get(employee_id, []))
            items = [item for queue in self._pending_by_employee.values() for item in queue]
            return sorted(items, key=lambda item: (item.enqueued_at, item.queue_id))

    async def edit(self, queue_id: str, text: str) -> QueuedOfficeRun:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            raise ValueError("text is required")
        async with self._lock:
            admission = self._admissions_by_queue.get(queue_id)
            if admission is None:
                raise FileNotFoundError(f"Queued run not found: {queue_id}")
            if admission.started:
                raise ValueError("Queued run already started")
            for employee_id, queue in self._pending_by_employee.items():
                for index, item in enumerate(queue):
                    if item.queue_id == queue_id:
                        edited = replace(item, text=normalized_text)
                        queue[index] = edited
                        self._items_by_queue[queue_id] = edited
                        return edited
            raise FileNotFoundError(f"Queued run not found: {queue_id}")

    async def delete(self, queue_id: str) -> QueuedOfficeRun:
        async with self._lock:
            admission = self._admissions_by_queue.get(queue_id)
            if admission is None:
                raise FileNotFoundError(f"Queued run not found: {queue_id}")
            if admission.started:
                raise ValueError("Queued run already started")
            item = self._remove_pending_unlocked(queue_id)
            self._admissions_by_queue.pop(queue_id, None)
            self._items_by_queue.pop(queue_id, None)
            self._resume_asks.pop(queue_id, None)
            self._held.discard(queue_id)
            waiter = self._waiters_by_queue.pop(queue_id, None)
            if waiter is not None and not waiter.done():
                waiter.cancel()
            return item

    def _remove_pending_unlocked(self, queue_id: str) -> QueuedOfficeRun:
        for employee_id, queue in list(self._pending_by_employee.items()):
            for index, item in enumerate(queue):
                if item.queue_id != queue_id:
                    continue
                queue.pop(index)
                if not queue:
                    self._pending_by_employee.pop(employee_id, None)
                return item
        raise FileNotFoundError(f"Queued run not found: {queue_id}")

    async def cancel_parent(self, parent_run_id: str) -> list[QueuedOfficeRun]:
        normalized = str(parent_run_id or "").strip()
        if not normalized:
            raise ValueError("parent_run_id is required")
        async with self._lock:
            removed: list[QueuedOfficeRun] = []
            for employee_id, queue in list(self._pending_by_employee.items()):
                kept: list[QueuedOfficeRun] = []
                for item in queue:
                    if item.source_kind == "pm_delegate" and item.parent_run_id == normalized:
                        removed.append(item)
                        self._admissions_by_queue.pop(item.queue_id, None)
                        self._items_by_queue.pop(item.queue_id, None)
                        self._resume_asks.pop(item.queue_id, None)
                        self._held.discard(item.queue_id)
                        waiter = self._waiters_by_queue.pop(item.queue_id, None)
                        if waiter is not None and not waiter.done():
                            waiter.cancel()
                    else:
                        kept.append(item)
                if kept:
                    self._pending_by_employee[employee_id] = kept
                else:
                    self._pending_by_employee.pop(employee_id, None)
            return sorted(removed, key=lambda item: (item.enqueued_at, item.queue_id))


_PROCESS_EMPLOYEE_RUN_GATE: EmployeeRunGate | None = None


def install_process_employee_run_gate(employee_run_gate: EmployeeRunGate) -> None:
    global _PROCESS_EMPLOYEE_RUN_GATE
    _PROCESS_EMPLOYEE_RUN_GATE = employee_run_gate


def get_process_employee_run_gate() -> EmployeeRunGate:
    if _PROCESS_EMPLOYEE_RUN_GATE is None:
        raise RuntimeError("Office employee run gate is not initialized")
    return _PROCESS_EMPLOYEE_RUN_GATE
