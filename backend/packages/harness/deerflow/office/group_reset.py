# 本文件属于 Office 群聊重置编排模块：只按固定顺序清空群聊消息、群聊线程上下文、匹配的 Run 与工作流。
# 不负责定义各存储的清空原语，也不触发或调度 Agent。
# 关联：group_messages.py / employees.py / workflows.py 提供清空原语，routers/office.py 暴露 HTTP 入口。
"""Clear the public group chat and everything that exists only to serve it.

Group state is spread across four stores with no foreign keys between them: group
messages, thread checkpoints, Run / RunEvent rows, and workflows. Clearing only some
of them is what strands the rest — a workflow whose phase Runs are gone keeps the UI
polling a dead run forever, and a checkpoint whose thread ownership row was removed
404s every future run on that thread. This module is the one place that clears them
together, in an order that never leaves a dangling reference behind.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class OfficeGroupReset:
    """Reset the group conversation for the bound project."""

    def __init__(
        self,
        *,
        pipeline: Any,
        workflow_service: Any,
        group_store: Any,
        project_service: Any,
        run_manager: Any,
        run_event_store: Any,
        user_id: str,
    ) -> None:
        self.pipeline = pipeline
        self.workflow_service = workflow_service
        self.group_store = group_store
        self.project_service = project_service
        self.run_manager = run_manager
        self.run_event_store = run_event_store
        self.user_id = user_id
        self._lock = asyncio.Lock()

    async def reset(self) -> dict[str, Any]:
        async with self._lock:
            binding = self.project_service.get_binding()
            project_id = binding.project_id if binding is not None else None
            # Order matters. Runs are signalled before their workflows and checkpoints
            # go away, and the group messages are deleted last so the chat stays visible
            # for the whole operation instead of emptying out from under the reader.
            cancelled_workflow_id = await self._cancel_current_workflow()
            cleared_threads = await self._clear_group_contexts()
            dropped_queue_items = await self._drop_pending_group_work()
            cleared_workflows = (
                self.workflow_service.store.clear(project_id=project_id)
                if project_id
                else 0
            )
            cleared_messages = self.group_store.clear()
            logger.info(
                "Office group reset: %d message(s), %d thread(s), %d workflow(s), %d queued item(s)",
                cleared_messages,
                len(cleared_threads),
                cleared_workflows,
                len(dropped_queue_items),
            )
            return {
                "clearedMessages": cleared_messages,
                "clearedThreads": cleared_threads,
                "clearedWorkflows": cleared_workflows,
                "droppedQueueItems": dropped_queue_items,
                "cancelledWorkflowId": cancelled_workflow_id,
            }

    async def _cancel_current_workflow(self) -> str | None:
        """Retire the legacy record before clearing its project history.

        Native Run cancellation is handled separately. A retirement failure is
        logged rather than raised: this explicit reset still deletes the old row.
        """
        try:
            workflow = self.workflow_service.get()
            if workflow is None:
                return None
            await self.workflow_service.cancel(workflow.workflow_id)
            return workflow.workflow_id
        except Exception:
            logger.exception("Failed to cancel the current workflow during group reset")
            return None

    async def _clear_group_contexts(self) -> list[str]:
        """Drop every employee's group-thread history, keeping the thread itself.

        All employees, not just the PM: group runs execute on each employee's own
        ``__group`` thread and the group view renders their run chips, so clearing only
        the PM would leave everyone else's orphaned context behind. Private threads are
        deliberately untouched.
        """
        employee_service = self.pipeline.employee_service
        cleared: list[str] = []
        for summary in await employee_service.list_employees():
            employee_id = str(summary.get("id") or "").strip()
            if not employee_id:
                continue
            thread_id = employee_service.thread_id(employee_id, "group")
            # Signal and forget the runs first: a worker still holding this thread would
            # otherwise write fresh checkpoints back into the history we just deleted.
            await self.run_manager.forget_thread(thread_id, user_id=self.user_id)
            await self.run_event_store.delete_by_thread(thread_id, user_id=self.user_id)
            await employee_service.reset_conversation_context(employee_id, kind="group")
            cleared.append(thread_id)
        return cleared

    async def _drop_pending_group_work(self) -> list[str]:
        """Discard queued group work and release the group pause boundary."""
        gate = self.pipeline.employee_run_gate
        dropped: list[str] = []
        for item in await gate.list_pending():
            if item.conversation_kind != "group":
                continue
            try:
                await gate.delete(item.queue_id)
                dropped.append(item.queue_id)
            except (ValueError, FileNotFoundError):
                # Already started, or already gone: nothing left to drop.
                continue
        # Release the pause in both places it lives: the FIFO holds on the gate, and the
        # in-memory flag on GroupExecution. resume() is not used because it re-raises a
        # stale pause error, which must not block a reset.
        for employee in await self.pipeline.employee_service.list_employees():
            waiting = await gate.pending_ask(employee['id'])
            if waiting is not None and waiting.work.thread_id == self.pipeline.employee_service.thread_id(employee['id'], 'group'):
                await gate.consume_ask(waiting)
        await gate.finish_group_pause()
        await gate.resume_group()
        group_execution = getattr(self.pipeline, "group_execution", None)
        if group_execution is not None:
            group_execution.paused = False
            group_execution.error = ""
            group_execution.stopping_ids.clear()
        return dropped
