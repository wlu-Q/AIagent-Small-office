# 本文件属于 Office Run 投影模块：只把 DeerFlow 生命周期投影为员工状态、PM 已确认群聊游标和群聊最终回复，并按恢复后的真实许可清理历史 working 投影。
# 不负责消息投递、员工排队、Run 启动、历史查询、取消或保存 Run/Event 副本。
# 关联：deps.py 将本模块接入 DeerFlow 生命周期钩子，run_gate.py 接收员工终态释放，employees.py 与 group_messages.py 接收展示投影。
from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.identity import employee_sender
from deerflow.office.native_work import pending_ask, work_identity
from deerflow.office.run_gate import EmployeeRunGate
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import RunStatus

logger = logging.getLogger(__name__)


class OfficeRunProjection:
    """Project DeerFlow Run lifecycle into the office employee view."""

    def __init__(
        self,
        *,
        employee_service: OfficeEmployeeService,
        user_id: str,
        employee_run_gate: EmployeeRunGate,
        group_store: GroupMessageStore | None = None,
        checkpointer: Any | None = None,
        knowledge_compactor: Any | None = None,
    ) -> None:
        self.employee_service = employee_service
        self.user_id = user_id
        self.employee_run_gate = employee_run_gate
        self.group_store = group_store
        self.checkpointer = checkpointer
        self.knowledge_compactor = knowledge_compactor

    async def reconcile_startup(
        self,
        active_employee_ids: Collection[str],
    ) -> None:
        """Reset stale working projections after persisted leases are restored."""
        active = {str(employee_id).strip() for employee_id in active_employee_ids}
        for employee in await self.employee_service.list_employees():
            employee_id = str(employee.get("id") or "").strip()
            if employee.get("state") != "working" or employee_id in active:
                continue
            self.employee_service.update_runtime_projection(
                employee_id,
                {
                    "state": "idle",
                    "statusDetail": "等待新任务",
                    "currentTaskId": None,
                },
            )

    def _identity(self, record: RunRecord) -> tuple[str, str, str] | None:
        metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        employee_id = metadata.get("office_employee_id")
        if not isinstance(employee_id, str) or not employee_id.strip():
            return None
        try:
            employee_id = self.employee_service.validate_employee_id(employee_id)
        except ValueError:
            return None
        conversation_kind = metadata.get("office_conversation_kind")
        if conversation_kind not in {"private", "group"}:
            return None
        if (
            record.user_id != self.user_id
            or record.assistant_id != employee_id
            or record.thread_id
            != self.employee_service.thread_id(employee_id, conversation_kind)
        ):
            return None
        task_id = metadata.get("office_task_id")
        normalized_task_id = str(task_id).strip() if task_id else record.run_id
        return employee_id, normalized_task_id, conversation_kind

    async def handle_run_started(self, record: RunRecord) -> None:
        identity = self._identity(record)
        if identity is None or record.status not in {RunStatus.pending, RunStatus.running}:
            return
        employee_id, task_id, _ = identity
        try:
            self.employee_service.update_runtime_projection(
                employee_id,
                {
                    "state": "working",
                    "statusDetail": "正在执行任务",
                    "currentTaskId": task_id,
                },
            )
        except FileNotFoundError:
            logger.warning(
                "Office employee %s is missing for run %s",
                employee_id,
                record.run_id,
            )

    async def handle_run_input_checkpointed(self, record: RunRecord) -> None:
        identity = self._identity(record)
        if identity is None or self.group_store is None:
            return
        employee_id, _, conversation_kind = identity
        if conversation_kind != "group":
            return
        waiting = await self.employee_run_gate.pending_ask(employee_id)
        metadata = record.metadata or {}
        if (waiting is not None and metadata.get('office_resume_source_run_id') == waiting.source_run_id
                and metadata.get('office_human_input_request_id') == waiting.request_id):
            await self.employee_run_gate.consume_ask(waiting)
        employee = self.employee_service.get_employee_summary(employee_id)
        if employee.get("canDelegate") is not True:
            return
        metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        sequence_end = metadata.get("office_group_sequence_end")
        if not isinstance(sequence_end, int) or isinstance(sequence_end, bool):
            return
        self.group_store.advance_pm_cursor(sequence_end)

    async def handle_run_completed(self, record: RunRecord) -> None:
        identity = self._identity(record)
        if identity is None or record.status not in {
            RunStatus.success,
            RunStatus.interrupted,
            RunStatus.error,
            RunStatus.timeout,
        }:
            return
        employee_id, task_id, conversation_kind = identity
        metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        lease_id = metadata.get("office_lease_id")
        if not isinstance(lease_id, str) or not lease_id.strip():
            return
        context = dict((record.kwargs.get('launch_contract') or {}).get('trusted_context') or {})
        if context.get('office_work') and record.status != RunStatus.interrupted:
            import asyncio
            from deerflow.office.execution_facts import ExecutionReceiptStore
            from deerflow.office.terminal import manager_for
            try:
                store = ExecutionReceiptStore(paths=self.employee_service.paths, user_id=self.employee_service.user_id)
                await asyncio.to_thread(manager_for(store).finish_run, record.run_id)
            except Exception:
                logger.exception('Could not finish terminal commands for Run %s', record.run_id)
        employee = self.employee_service.get_employee_summary(employee_id)
        waiting = await pending_ask(self.checkpointer, record) if employee.get('canDelegate') is True else None
        existing_wait = await self.employee_run_gate.pending_ask(employee_id)
        if (waiting is None and existing_wait is not None
                and metadata.get('office_resume_source_run_id') == existing_wait.source_run_id):
            # Resume failed before its first checkpoint: the original ask survives.
            waiting = existing_wait
        released = await self.employee_run_gate.release(lease_id, pending_ask=waiting)
        if released is None:
            return
        patch: dict[str, Any] = ({'state': 'working', 'statusDetail': '等待用户回答', 'currentTaskId': task_id}
                                if waiting else {'state': 'idle', 'statusDetail': '等待新任务', 'currentTaskId': None})
        if record.status == RunStatus.success and record.last_ai_message:
            patch['lastReport'] = record.last_ai_message
        try:
            employee = self.employee_service.get_employee_summary(employee_id)
            if employee.get("currentTaskId") == task_id:
                self.employee_service.update_runtime_projection(employee_id, patch)
            if (
                self.group_store is not None
                and conversation_kind == "group"
                and record.status == RunStatus.success
                and record.last_ai_message
            ):
                self.group_store.append(
                    message_id=f"run:{record.run_id}:final",
                    **employee_sender(employee),
                    content=record.last_ai_message,
                    source_run_id=record.run_id,
                )
        except FileNotFoundError:
            logger.warning(
                "Office employee %s is missing for run %s",
                employee_id,
                record.run_id,
            )
        work = work_identity(record)
        if record.status == RunStatus.success and work and work.project_id and self.knowledge_compactor:
            try:
                await self.knowledge_compactor.compact(work.project_id)
            except Exception:
                logger.exception('Failed to compact project knowledge for Run %s', record.run_id)
