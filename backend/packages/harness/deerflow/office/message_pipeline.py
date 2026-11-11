# 本文件属于 Office 消息执行管道模块：只负责消息投递、内部 single-flight、员工门禁、排队项启动和 PM 启动时群聊增量取样。
# 不负责 Mention 规则、Run/Thread/Event 持久化、对外历史查询、游标确认、状态投影或 Agent Loop。
# 关联：messages.py 提供确定性路由，run_gate.py 控制准入，run_launch.py 调用 DeerFlow 原生 start_run。
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from deerflow.office.group_execution import GroupExecution
from deerflow.office.group_messages import GroupMessage, GroupMessageStore
from deerflow.office.identity import employee_sender
from deerflow.office.messages import (
    ConversationKind,
    OfficeMessageCommand,
    ResolvedOfficeMessage,
    build_pm_group_input,
    resolve_message_command,
)
from deerflow.office.native_work import pending_ask
from deerflow.office.projects import OfficeProjectService
from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun
from deerflow.office.run_launch import OfficeRunLauncher, RunStarter
from deerflow.office.run_view import OfficeRunView
from deerflow.runtime.runs.execution import RunExecutionSpecV1
from deerflow.runtime.runs.schemas import RunStatus

logger = logging.getLogger(__name__)


class _StaleProjectDispatch(RuntimeError):
    """A queued PM message no longer belongs to the current project."""


_WORKFLOW_CANCEL_COMMAND = re.compile(
    r'^(?:请)?(?:立即)?(?:停止|取消|终止|结束)(?:本次|当前|这个)?(?:工作流|工具流|流程)(?:吧)?[。！!]?$', re.IGNORECASE,
)
_WORKFLOW_DO_NOT_CONTINUE = re.compile(
    r'^我不想(?:再)?(?:按|按照|根据)?(?:这个|当前|本次)?(?:工作流|工具流|流程)(?:再)?继续(?:执行)?了?[。！!]?$', re.IGNORECASE,
)


def is_workflow_cancel_command(text: str) -> bool:
    normalized = re.sub(r'\s+', '', text.strip())
    return bool(_WORKFLOW_CANCEL_COMMAND.fullmatch(normalized) or _WORKFLOW_DO_NOT_CONTINUE.fullmatch(normalized))


@dataclass(frozen=True)
class MessageDispatchRequest:
    message_id: str
    employee_id: str
    conversation_kind: ConversationKind
    text: str
    preselected_employee_ids: tuple[str, ...]
    project_id: str | None = None
    group_epoch: int | None = field(default=None, compare=False)

    @property
    def task_summary(self) -> str:
        return self.text.strip()

    @property
    def idempotency_key(self) -> tuple[str, str]:
        return self.message_id, self.employee_id


MessageStartedCallback = Callable[[str], Awaitable[None]]
InFlightDispatch = tuple[
    MessageDispatchRequest,
    asyncio.Task[Mapping[str, Any]],
]
ResumeIdentity = tuple[str, str]
InFlightResume = tuple[
    ResumeIdentity,
    asyncio.Task[Mapping[str, Any]],
]


class OfficeMessagePipeline:
    """Deliver resolved Office messages through the shared execution gates."""

    def __init__(
        self,
        *,
        user_id: str,
        employee_service: Any,
        group_store: GroupMessageStore,
        employee_run_gate: EmployeeRunGate,
        run_launcher: OfficeRunLauncher,
        run_view: OfficeRunView,
        project_service: OfficeProjectService | None,
    ) -> None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("user_id is required")
        self.user_id = normalized_user_id
        self.employee_service = employee_service
        self.group_store = group_store
        self.employee_run_gate = employee_run_gate
        self.run_launcher = run_launcher
        self.run_view = run_view
        self.project_service = project_service
        self.group_execution = GroupExecution(self)
        self._dispatch_tasks: dict[tuple[str, str], InFlightDispatch] = {}
        self._resume_tasks: dict[tuple[str, str, str], InFlightResume] = {}
        self._dispatch_lock = asyncio.Lock()
        self._queued_message_tasks: set[asyncio.Task[None]] = set()

    async def restore_waits(self):
        """Rebuild only projections; native checkpoints and Run records own facts."""
        records = await self.group_execution.records()
        paused = await self._current_paused_records(records)
        self.group_execution.paused = bool(paused)
        superseded = {(r.metadata or {}).get('office_resume_source_run_id') for r in records if r.status in {'pending', 'running', 'success'}}
        for record in records:
            if record.run_id in superseded:
                continue
            if self._employee(record.assistant_id).get('canDelegate') is not True:
                continue
            waiting = await pending_ask(self.run_launcher.checkpointer, record)
            if waiting:
                await self.employee_run_gate.restore_ask(waiting)
                self.employee_service.update_runtime_projection(record.assistant_id,
                    {'state': 'working', 'statusDetail': '等待用户回答',
                     'currentTaskId': self.run_view.source_task_id(record)})

    async def cancel_current_work(self, employee_id):
        from deerflow.runtime.runs.manager import CancelOutcome
        for record in await self.group_execution.records():
            if record.assistant_id != employee_id:
                continue
            if self.group_execution.active(record):
                await self.cancel_wait(record)
                outcome = await self.run_view.run_manager.cancel(record.run_id, action='interrupt', user_id=self.user_id)
                if outcome in {CancelOutcome.unknown, CancelOutcome.not_active_locally}:
                    raise RuntimeError('Run cannot be cancelled locally')
                await self.employee_run_gate.cancel_parent(record.run_id)
            elif await pending_ask(self.run_launcher.checkpointer, record):
                await self.cancel_wait(record)

    async def cancel_wait(self, record):
        async with self.group_execution.launch_lock:
            await self._cancel_wait_locked(record)

    async def _cancel_wait_locked(self, record):
        waiting = await self.employee_run_gate.pending_ask(record.assistant_id)
        await self.run_view.run_manager.update_metadata(record.run_id, {'office_control': 'cancelled'}, user_id=self.user_id)
        if waiting is not None and waiting.source_run_id == record.run_id:
            queue_id = self._resume_queue_id(record.run_id, waiting.request_id)
            for item in await self.employee_run_gate.list_pending(record.assistant_id):
                if item.queue_id == queue_id:
                    await self.employee_run_gate.delete(queue_id)
            await self.employee_run_gate.consume_ask(waiting)
            employee = self._employee(record.assistant_id)
            if employee.get('currentTaskId') == self.run_view.source_task_id(record):
                self.employee_service.update_runtime_projection(record.assistant_id,
                    {'state': 'idle', 'statusDetail': '等待新任务', 'currentTaskId': None})

    async def queue_paused_continuations(self, *, start_run, request):
        records = await self.group_execution.records()
        for record in await self._current_paused_records(records):
            if (not self._employee(record.assistant_id).get('canDelegate')
                    or await pending_ask(self.run_launcher.checkpointer, record)):
                continue
            queue_id = f'continue:{record.run_id}'
            existing = await self.employee_run_gate.get_admission(queue_id)
            if existing is not None:
                continue
            await self.employee_run_gate.admit(QueuedOfficeRun(queue_id, record.assistant_id, 'direct',
                '继续执行', int(time.time() * 1000), conversation_kind='group'), hold=True, priority=True)
            task = asyncio.create_task(self._continue_paused(record, queue_id, start_run, request))
            self._queued_message_tasks.add(task)
            task.add_done_callback(self._queued_message_tasks.discard)

    async def _current_paused_records(self, records):
        superseded = {(r.metadata or {}).get('office_resume_source_run_id') for r in records if r.status in {'pending', 'running', 'success'}}
        paused = []
        for record in records:
            if (record.run_id in superseded or record.status != 'interrupted'
                    or (record.metadata or {}).get('office_control') != 'paused'):
                continue
            if await self._owns_current_project_checkpoint(record):
                paused.append(record)
        return paused

    async def _owns_current_project_checkpoint(self, record):
        from deerflow.office.native_work import work_identity
        from deerflow.runtime.runs.execution import checkpoint_run_id
        binding = self.project_service.get_binding() if self.project_service else None
        work = work_identity(record)
        project = work.project_id if work else (record.metadata or {}).get('office_project_id')
        if project != (binding.project_id if binding else None):
            return False
        checkpoint = await self.run_launcher.checkpointer.aget_tuple({'configurable': {'thread_id': record.thread_id, 'checkpoint_ns': ''}})
        return checkpoint_run_id(checkpoint) == record.run_id

    async def _validate_continuation(self, record):
        _, record = await self.run_view.resolve_run(record.assistant_id, record.run_id)
        if (record.metadata or {}).get('office_control') != 'paused':
            raise ValueError('Run is no longer paused')
        if not await self._owns_current_project_checkpoint(record):
            raise ValueError('Paused Run no longer owns the current project/checkpoint; send a new request')

    async def _continue_paused(self, record, queue_id, start_run, request):
        lease_id = None
        started = False
        try:
            admission = await self.employee_run_gate.wait_until_started(queue_id)
            lease_id = admission.lease_id
            await self._validate_continuation(record)
            await self.run_launcher.continue_from_checkpoint(record,
                start_run=self.group_execution.guard(start_run, queue_id, before_start=lambda: self._validate_continuation(record)), request=request,
                office_lease_id=lease_id, office_queue_id=queue_id)
            started = True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.group_execution.error = '暂停任务未能恢复，请检查 Run 后重试。'
            logger.exception('Failed to resume paused Run %s', record.run_id)
        finally:
            # A successfully started Run owns the lease until its completion hook.
            if lease_id and not started:
                await self.employee_run_gate.release(lease_id)

    async def shutdown(self) -> None:
        """Cancel and await only the short-lived tasks owned by this pipeline."""
        await self.group_execution.shutdown()
        async with self._dispatch_lock:
            tasks = {
                task
                for _dispatch, task in self._dispatch_tasks.values()
            }
            tasks.update(task for _identity, task in self._resume_tasks.values())
            tasks.update(self._queued_message_tasks)
        current = asyncio.current_task()
        pending = [
            task
            for task in tasks
            if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        async with self._dispatch_lock:
            self._dispatch_tasks.clear()
            self._resume_tasks.clear()
            self._queued_message_tasks.clear()

    @staticmethod
    def _model_profile_id(payload: Mapping[str, Any]) -> str:
        model_profile_id = payload.get("modelProfileId")
        if not isinstance(model_profile_id, str) or not model_profile_id.strip():
            raise ValueError("modelProfileId must be a non-empty string")
        return model_profile_id.strip()

    def _employee(
        self,
        employee_id: str,
        *,
        require_enabled: bool = False,
    ) -> dict[str, Any]:
        normalized = self.employee_service.validate_employee_id(employee_id)
        employee = self.employee_service.get_employee_summary(normalized)
        if require_enabled and not employee["enabled"]:
            raise RuntimeError("Employee is disabled")
        return employee

    @staticmethod
    def _message_queue_id(user_id: str, message_id: str, employee_id: str) -> str:
        value = f"{user_id}\n{message_id}\n{employee_id}"
        return f"office-message-{uuid.uuid5(uuid.NAMESPACE_URL, value).hex}"

    @staticmethod
    def _resume_queue_id(source_run_id: str, request_id: str) -> str:
        return f"resume:{source_run_id}:{request_id}"

    def _save_group_resume_once(
        self,
        source_record: Any,
        request_id: str,
        text: str,
    ) -> GroupMessage | None:
        metadata = (
            source_record.metadata
            if isinstance(source_record.metadata, Mapping)
            else {}
        )
        if metadata.get("office_conversation_kind") != "group":
            return
        return self.group_store.append(
            message_id=self._resume_queue_id(source_record.run_id, request_id),
            sender_kind="user",
            sender_id=self.user_id,
            content=text,
            mention_employee_ids=(source_record.assistant_id,),
            project_id=(metadata.get("office_project_id") if isinstance(metadata.get("office_project_id"), str) else None),
        )

    async def _start_admitted_message(
        self,
        dispatch: MessageDispatchRequest,
        *,
        queue_id: str,
        lease_id: str,
        start_run: RunStarter,
        request: Any,
    ) -> dict[str, Any]:
        try:
            item = await self.employee_run_gate.get_item(queue_id)
            employee = self._employee(dispatch.employee_id, require_enabled=True)
            project_binding = (
                self.project_service.get_binding()
                if self.project_service is not None
                else None
            )
            input_messages = [{"role": "user", "content": item.text}]
            if employee.get("canDelegate") is True and dispatch.project_id != (project_binding.project_id if project_binding else None):
                if dispatch.conversation_kind == 'group':
                    self.group_store.append(message_id=f'{dispatch.message_id}:project-changed', **employee_sender(employee),
                        content='项目已切换，这条排队中的 PM 消息未执行。请切回目标项目后重新发送。')
                raise _StaleProjectDispatch('Project changed while this message was queued; resend in the intended project')
            message_metadata: dict[str, Any] = {
                "office_message_id": dispatch.message_id,
                "office_work_started_at": datetime.fromtimestamp(item.enqueued_at / 1000, UTC).isoformat(),
                "office_conversation_kind": dispatch.conversation_kind,
                "office_preselected_employee_ids": list(
                    dispatch.preselected_employee_ids
                ),
                "office_queue_id": queue_id,
            }
            if (
                dispatch.conversation_kind == "group"
                and employee.get("canDelegate") is True
            ):
                delta = self.group_store.read_pm_delta()
                current_message = self.group_store.find(dispatch.message_id)
                if current_message is None:
                    raise RuntimeError("Current PM group message is missing from the group store")
                input_messages = build_pm_group_input(delta.messages, current_message_id=current_message.message_id)
                message_metadata["office_group_sequence_end"] = delta.sequence_end
            execution_spec = RunExecutionSpecV1(version=1, mode="new", input={"messages": input_messages})
            record = await self.run_launcher.launch(
                employee["id"],
                self._model_profile_id(employee),
                execution_spec,
                start_run=self.group_execution.guard(start_run, queue_id) if item.conversation_kind == "group" else start_run,
                request=request,
                thread_id=self.employee_service.thread_id(
                    employee["id"],
                    dispatch.conversation_kind,
                ),
                office_lease_id=lease_id,
                office_task_summary=dispatch.task_summary,
                project_binding=project_binding,
                message_metadata=message_metadata,
            )
            return self.run_view.serialize_run(employee["id"], record)
        except BaseException:
            await self.employee_run_gate.release(lease_id)
            raise

    async def _start_queued_message(
        self,
        dispatch: MessageDispatchRequest,
        queue_id: str,
        on_started: MessageStartedCallback,
        start_run: RunStarter,
        request: Any,
    ) -> None:
        lease_id: str | None = None
        try:
            admission = await self.employee_run_gate.wait_until_started(queue_id)
            if admission.lease_id is None:
                raise RuntimeError(f"Employee run did not receive a lease: {queue_id}")
            lease_id = admission.lease_id
            item = await self.employee_run_gate.get_item(queue_id)
            await on_started(item.text)
            await self._start_admitted_message(
                dispatch,
                queue_id=queue_id,
                lease_id=lease_id,
                start_run=start_run,
                request=request,
            )
        except asyncio.CancelledError:
            if lease_id is not None:
                await self.employee_run_gate.release(lease_id)
            return
        except _StaleProjectDispatch:
            if lease_id is not None:
                await self.employee_run_gate.release(lease_id)
            logger.info("Skipped stale queued PM workflow message: %s", queue_id)
        except Exception:
            if lease_id is not None:
                await self.employee_run_gate.release(lease_id)
            logger.exception("Queued Office message failed to start: %s", queue_id)

    async def _dispatch_message(
        self,
        dispatch: MessageDispatchRequest,
        on_started: MessageStartedCallback,
        *,
        start_run: RunStarter,
        request: Any,
    ) -> dict[str, Any]:
        employee = self._employee(dispatch.employee_id, require_enabled=True)
        existing_run = await self.run_view.find_message_run(
            employee["id"],
            dispatch.conversation_kind,
            dispatch.message_id,
        )
        if existing_run is not None:
            metadata = (
                existing_run.metadata
                if isinstance(existing_run.metadata, Mapping)
                else {}
            )
            if self.run_view.task_summary(existing_run) != dispatch.task_summary:
                raise ValueError(
                    "Message dispatch identity already exists with different content"
                )
            return {
                **self.run_view.serialize_run(employee["id"], existing_run),
                "employeeId": employee["id"],
                "queueId": metadata.get("office_queue_id"),
                "started": True,
            }

        queue_id = self._message_queue_id(
            self.user_id,
            dispatch.message_id,
            employee["id"],
        )
        existing_admission = await self.employee_run_gate.get_admission(queue_id)
        if existing_admission is not None:
            existing_item = await self.employee_run_gate.get_item(queue_id)
            if existing_item.text != dispatch.text.strip():
                raise ValueError(
                    "Message dispatch identity already exists with different content"
                )
            return {
                "employeeId": employee["id"],
                "queueId": queue_id,
                "started": existing_admission.started,
                "runId": None,
            }

        admission = await self.employee_run_gate.admit(
            QueuedOfficeRun(
                queue_id=queue_id,
                target_employee_id=employee["id"],
                source_kind="direct",
                text=dispatch.text,
                enqueued_at=int(time.time() * 1000),
                conversation_kind=dispatch.conversation_kind,
            ),
            hold=dispatch.conversation_kind == "group" and dispatch.group_epoch is not None and dispatch.group_epoch != self.group_execution.epoch,
        )
        if not admission.started or admission.lease_id is None:
            task = asyncio.create_task(
                self._start_queued_message(
                    dispatch,
                    queue_id,
                    on_started,
                    start_run,
                    request,
                )
            )
            self._queued_message_tasks.add(task)
            task.add_done_callback(self._queued_message_tasks.discard)
            return {
                "employeeId": employee["id"],
                "queueId": queue_id,
                "started": False,
                "runId": None,
            }

        try:
            await on_started(dispatch.text)
        except BaseException:
            await self.employee_run_gate.release(admission.lease_id)
            raise
        result = await self._start_admitted_message(
            dispatch,
            queue_id=queue_id,
            lease_id=admission.lease_id,
            start_run=start_run,
            request=request,
        )
        return {
            **result,
            "employeeId": employee["id"],
            "queueId": queue_id,
            "started": True,
        }

    async def _start_admitted_resume(
        self,
        source_record: Any,
        *,
        source: str,
        request_id: str,
        queue_id: str,
        lease_id: str,
        start_run: RunStarter,
        request: Any,
    ) -> dict[str, Any]:
        try:
            item = await self.employee_run_gate.get_item(queue_id)
            await self._validate_answer(source_record, source, request_id)
            response = {
                "version": 1,
                "kind": "human_input_response",
                "source": source,
                "request_id": request_id,
                "response_kind": "text",
                "value": item.text,
            }
            answer = self._save_group_resume_once(
                source_record,
                request_id,
                item.text,
            )
            record = await self.run_launcher.resume(
                source_record,
                response,
                start_run=self.group_execution.guard(start_run, queue_id, before_start=lambda: self._validate_answer(source_record, source, request_id)) if item.conversation_kind == "group" else start_run,
                request=request,
                office_lease_id=lease_id,
                office_queue_id=queue_id,
                office_group_sequence_end=(answer.sequence if answer is not None else None),
            )
            return self.run_view.serialize_run(
                source_record.assistant_id,
                record,
            )
        except BaseException:
            await self.employee_run_gate.release(lease_id)
            raise

    async def _start_queued_resume(
        self,
        source_record: Any,
        *,
        source: str,
        request_id: str,
        queue_id: str,
        start_run: RunStarter,
        request: Any,
    ) -> None:
        lease_id: str | None = None
        try:
            admission = await self.employee_run_gate.wait_until_started(queue_id)
            if admission.lease_id is None:
                raise RuntimeError(
                    f"Employee run did not receive a lease: {queue_id}"
                )
            lease_id = admission.lease_id
            await self._start_admitted_resume(
                source_record,
                source=source,
                request_id=request_id,
                queue_id=queue_id,
                lease_id=admission.lease_id,
                start_run=start_run,
                request=request,
            )
        except asyncio.CancelledError:
            if lease_id is not None:
                await self.employee_run_gate.release(lease_id)
            return
        except Exception:
            logger.exception("Queued Office resume failed to start: %s", queue_id)

    def _forget_completed_resume(
        self,
        key: tuple[str, str, str],
        task: asyncio.Task[Mapping[str, Any]],
    ) -> None:
        current = self._resume_tasks.get(key)
        if current is not None and current[1] is task:
            self._resume_tasks.pop(key, None)

    async def resume(
        self,
        employee_id: str,
        source_run_id: str,
        *,
        source: str,
        request_id: str,
        text: str,
        start_run: RunStarter,
        request: Any,
    ) -> dict[str, Any]:
        normalized_employee_id = self.employee_service.validate_employee_id(employee_id)
        normalized_source_run_id = str(source_run_id or "").strip()
        normalized_source = str(source or "").strip()
        normalized_request_id = str(request_id or "").strip()
        normalized_text = str(text or "").strip()
        if not normalized_source_run_id:
            raise ValueError("source run ID is required")
        if not normalized_source:
            raise ValueError("source is required")
        if not normalized_request_id:
            raise ValueError("requestId is required")
        if not normalized_text:
            raise ValueError("text is required")
        key = (
            normalized_employee_id,
            normalized_source_run_id,
            normalized_request_id,
        )
        identity = (normalized_source, normalized_text)
        async with self._dispatch_lock:
            current = self._resume_tasks.get(key)
            if current is None:
                task = asyncio.create_task(
                    self._resume_once(
                        normalized_employee_id,
                        normalized_source_run_id,
                        source=normalized_source,
                        request_id=normalized_request_id,
                        text=normalized_text,
                        start_run=start_run,
                        request=request,
                    )
                )
                self._resume_tasks[key] = (identity, task)
                task.add_done_callback(
                    lambda completed, resume_key=key: self._forget_completed_resume(
                        resume_key,
                        completed,
                    )
                )
            else:
                existing_identity, task = current
                if existing_identity != identity:
                    raise ValueError(
                        "Resume request identity already exists with different content"
                    )
        return dict(await asyncio.shield(task))

    async def _validate_answer(self, record, source, request_id):
        _, record = await self.run_view.resolve_run(record.assistant_id, record.run_id)
        waiting = await pending_ask(self.run_launcher.checkpointer, record)
        if waiting is None or source != 'ask_clarification' or waiting.request_id != request_id:
            raise ValueError('This question is no longer pending on the current checkpoint')
        binding = self.project_service.get_binding() if self.project_service else None
        if waiting.work.project_id != (binding.project_id if binding else None):
            raise ValueError('Resume project binding changed')
        return waiting

    async def _resume_once(
        self,
        employee_id: str,
        source_run_id: str,
        *,
        source: str,
        request_id: str,
        text: str,
        start_run: RunStarter,
        request: Any,
    ) -> dict[str, Any]:
        group_epoch = self.group_execution.epoch
        employee = self._employee(employee_id, require_enabled=True)
        _, source_record = await self.run_view.resolve_run(
            employee["id"],
            source_run_id,
        )
        source_status = (
            source_record.status.value
            if isinstance(source_record.status, RunStatus)
            else str(source_record.status)
        )
        if source_status != RunStatus.interrupted.value:
            raise ValueError("Only an interrupted run can be resumed")
        metadata = (
            source_record.metadata
            if isinstance(source_record.metadata, Mapping)
            else {}
        )
        conversation_kind = metadata.get("office_conversation_kind")
        if conversation_kind not in {"private", "group"}:
            raise ValueError("Source run has no Office conversation identity")
        existing_run = await self.run_view.find_resume_run(
            employee["id"],
            conversation_kind,
            source_record.run_id,
            request_id,
        )
        if (existing_run is not None and existing_run.status in {'error', 'timeout', 'interrupted'}
                and not getattr(existing_run, 'finalizing', False) and not getattr(existing_run, 'completion_pending', False)
                and await pending_ask(self.run_launcher.checkpointer, source_record)):
            if self.run_view.task_summary(existing_run) != text:
                raise ValueError('Resume request identity already exists with different content')
            existing_run = None  # Failed before checkpointing: retry the same pending answer.
        if existing_run is not None:
            if self.run_view.task_summary(existing_run) != text:
                raise ValueError(
                    "Resume request identity already exists with different content"
                )
            existing_metadata = (
                existing_run.metadata
                if isinstance(existing_run.metadata, Mapping)
                else {}
            )
            return {
                **self.run_view.serialize_run(employee["id"], existing_run),
                "employeeId": employee["id"],
                "sourceRunId": source_record.run_id,
                "queueId": existing_metadata.get("office_queue_id"),
                "started": True,
            }

        queue_id = self._resume_queue_id(
            source_record.run_id,
            request_id,
        )
        existing_admission = await self.employee_run_gate.get_admission(queue_id)
        if existing_admission is not None:
            existing_item = await self.employee_run_gate.get_item(queue_id)
            if existing_item.text != text:
                raise ValueError(
                    "Resume request identity already exists with different content"
                )
            return {
                "employeeId": employee["id"],
                "sourceRunId": source_record.run_id,
                "queueId": queue_id,
                "started": existing_admission.started,
                "runId": None,
            }

        waiting = await self._validate_answer(source_record, source, request_id)
        if employee.get('canDelegate') is True:
            await self.employee_run_gate.restore_ask(waiting)
        admission = await self.employee_run_gate.admit(
            QueuedOfficeRun(
                queue_id=queue_id,
                target_employee_id=employee["id"],
                source_kind="direct",
                text=text,
                enqueued_at=int(time.time() * 1000),
                conversation_kind=conversation_kind,
            ),
            hold=conversation_kind == "group" and ((source_record.metadata or {}).get('office_control') == 'paused' or group_epoch != self.group_execution.epoch),
            resume_ask=waiting if employee.get('canDelegate') is True else None,
        )
        if not admission.started or admission.lease_id is None:
            task = asyncio.create_task(
                self._start_queued_resume(
                    source_record,
                    source=source,
                    request_id=request_id,
                    queue_id=queue_id,
                    start_run=start_run,
                    request=request,
                )
            )
            self._queued_message_tasks.add(task)
            task.add_done_callback(self._queued_message_tasks.discard)
            return {
                "employeeId": employee["id"],
                "sourceRunId": source_record.run_id,
                "queueId": queue_id,
                "started": False,
                "runId": None,
            }

        result = await self._start_admitted_resume(
            source_record,
            source=source,
            request_id=request_id,
            queue_id=queue_id,
            lease_id=admission.lease_id,
            start_run=start_run,
            request=request,
        )
        return {
            **result,
            "employeeId": employee["id"],
            "sourceRunId": source_record.run_id,
            "queueId": queue_id,
            "started": True,
        }

    def _forget_completed_dispatch(
        self,
        key: tuple[str, str],
        task: asyncio.Task[Mapping[str, Any]],
    ) -> None:
        current = self._dispatch_tasks.get(key)
        if current is not None and current[1] is task:
            self._dispatch_tasks.pop(key, None)

    async def _dispatch_once(
        self,
        dispatch: MessageDispatchRequest,
        on_started: MessageStartedCallback,
        *,
        start_run: RunStarter,
        request: Any,
    ) -> dict[str, Any]:
        async with self._dispatch_lock:
            current = self._dispatch_tasks.get(dispatch.idempotency_key)
            if current is None:
                task = asyncio.create_task(
                    self._dispatch_message(
                        dispatch,
                        on_started,
                        start_run=start_run,
                        request=request,
                    )
                )
                self._dispatch_tasks[dispatch.idempotency_key] = (dispatch, task)
                task.add_done_callback(
                    lambda completed, key=dispatch.idempotency_key: self._forget_completed_dispatch(
                        key,
                        completed,
                    )
                )
            else:
                existing_dispatch, task = current
                if existing_dispatch != dispatch:
                    raise ValueError(
                        "Message dispatch identity already exists with different content"
                    )
        return dict(await asyncio.shield(task))

    async def _save_group_once(
        self,
        message_id: str,
        resolved: ResolvedOfficeMessage,
        content: str | None = None,
    ) -> None:
        if resolved.conversation_kind != "group":
            return
        binding = self.project_service.get_binding() if self.project_service else None
        self.group_store.append(
            message_id=message_id,
            sender_kind="user",
            sender_id=self.user_id,
            content=resolved.text if content is None else content,
            mention_employee_ids=resolved.mention_employee_ids,
            project_id=binding.project_id if binding else None,
        )

    async def send(
        self,
        message_id: str,
        command: OfficeMessageCommand,
        *,
        start_run: RunStarter,
        request: Any,
    ) -> dict[str, Any]:
        group_epoch = self.group_execution.epoch
        if not isinstance(message_id, str):
            raise ValueError("message_id must be a string")
        normalized_message_id = message_id.strip()
        if not normalized_message_id:
            raise ValueError("message_id is required")
        resolved = resolve_message_command(
            command,
            await self.employee_service.list_employees(),
        )
        binding = self.project_service.get_binding() if self.project_service else None
        resume_message_id = f'{normalized_message_id}:group-resumed'
        control_text = resolved.text.strip()
        if resolved.mention_employee_ids == ('pm',):
            mention = re.escape('@' + self._employee('pm')['displayName'])
            control_text = re.sub(rf'^{mention}\s+', '', control_text).strip()
        if (resolved.conversation_kind == 'group' and set(resolved.mention_employee_ids) <= {'pm'}
                and re.fullmatch(r'(?:请)?继续(?:执行|群聊任务|工作流)?(?:吧)?[。！!]?', control_text)
                and (self.group_store.find(resume_message_id) or (await self.group_execution.state())['paused'])):
            if self.group_store.find(resume_message_id) is None:
                await self._save_group_once(normalized_message_id, resolved)
                await self.group_execution.resume(start_run=start_run, request=request)
                self.group_store.append(message_id=resume_message_id, **employee_sender(self._employee('pm')),
                    content='已继续群聊排队任务。直接 @ 员工的已中断执行不会自动重放；如需继续，请 @ 对应员工说明继续任务，员工应先核对已有产物。')
            return {'messageId': normalized_message_id, 'conversationKind': 'group',
                    'triggerEmployeeIds': [], 'preselectedEmployeeIds': [], 'dispatches': []}
        cancelled_message = self.group_store.find(f'{normalized_message_id}:work-cancelled') if resolved.conversation_kind == 'group' else None
        if cancelled_message and is_workflow_cancel_command(resolved.text):
            await self._save_group_once(normalized_message_id, resolved)
            return {'messageId': normalized_message_id, 'conversationKind': 'group',
                    'triggerEmployeeIds': [cancelled_message.sender_id], 'preselectedEmployeeIds': [], 'dispatches': []}
        # Unmentioned replies during a current PM Run retain the existing route.
        targets = ((await self.group_execution.state())['runTargets'] if resolved.conversation_kind == 'group'
                   and (not resolved.trigger_employee_ids or is_workflow_cancel_command(resolved.text)) else [])
        pm_id = next((t['employeeId'] for t in targets if self._employee(t['employeeId']).get('canDelegate') is True), None)
        if pm_id and not resolved.trigger_employee_ids:
            resolved = replace(resolved, trigger_employee_ids=(pm_id,))
        if (pm_id and set(resolved.trigger_employee_ids) <= {pm_id} and is_workflow_cancel_command(resolved.text)):
            await self._save_group_once(normalized_message_id, resolved)
            await self.cancel_current_work(pm_id)
            self.group_store.append(message_id=f'{normalized_message_id}:work-cancelled',
                **employee_sender(self._employee(pm_id)), content='已停止本次工作。')
            return {'messageId': normalized_message_id, 'conversationKind': 'group',
                    'triggerEmployeeIds': [pm_id], 'preselectedEmployeeIds': [], 'dispatches': []}
        if resolved.conversation_kind == "group" and not resolved.trigger_employee_ids:
            await self._save_group_once(normalized_message_id, resolved)
            return {
                "messageId": normalized_message_id,
                "conversationKind": resolved.conversation_kind,
                "triggerEmployeeIds": [],
                "preselectedEmployeeIds": [],
                "dispatches": [],
            }

        dispatches_to_start = [
            MessageDispatchRequest(
                message_id=normalized_message_id,
                employee_id=employee_id,
                conversation_kind=resolved.conversation_kind,
                text=resolved.text,
                preselected_employee_ids=resolved.preselected_employee_ids,
                project_id=binding.project_id if binding else None,
                group_epoch=group_epoch,
            )
            for employee_id in resolved.trigger_employee_ids
        ]

        async def on_started(started_text: str) -> None:
            await self._save_group_once(
                normalized_message_id,
                resolved,
                started_text,
            )

        # The targets are already validated and independently gated. Submit
        # them together so one slow DeerFlow start does not delay other idle
        # employees; gather preserves the input mention order in its result.
        dispatches = list(
            await asyncio.gather(
                *(
                    self._dispatch_once(
                        dispatch,
                        on_started,
                        start_run=start_run,
                        request=request,
                    )
                    for dispatch in dispatches_to_start
                )
            )
        )
        return {
            "messageId": normalized_message_id,
            "conversationKind": resolved.conversation_kind,
            "triggerEmployeeIds": list(resolved.trigger_employee_ids),
            "preselectedEmployeeIds": list(resolved.preselected_employee_ids),
            "dispatches": dispatches,
        }

