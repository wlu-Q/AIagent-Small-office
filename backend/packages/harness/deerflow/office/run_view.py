# 本文件属于 Office Run 查询投影模块：只校验员工归属并读取 DeerFlow Run、Event 和 Thread 历史事实。
# 不负责 Run 启动、取消执行、员工排队、状态维护或保存任何权威副本。
# 关联：Gateway Office Router 调用本模块，RunManager 与 RunEventStore 是唯一执行和事件事实来源。
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from enum import Enum
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.human_input import read_human_input_response
from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.native_work import work_identity
from deerflow.runtime.context_keys import RUNTIME_MODEL_ID_METADATA_KEY
from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.runs.execution import (
    RunLaunchContractV1,
    RunLaunchContractValidationError,
)
from deerflow.runtime.runs.manager import RunManager, RunRecord
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.utils.messages import get_original_user_content_text, message_to_text, public_ai_text


class OfficeRunView:
    """Project employee-scoped views from DeerFlow run facts."""

    def __init__(
        self,
        *,
        employee_service: OfficeEmployeeService,
        run_manager: RunManager,
        event_store: RunEventStore,
        checkpointer: Any,
        user_id: str,
    ) -> None:
        self.employee_service = employee_service
        self.run_manager = run_manager
        self.event_store = event_store
        self.checkpointer = checkpointer
        self.user_id = user_id

    def _employee(self, employee_id: str) -> dict[str, Any]:
        normalized = self.employee_service.validate_employee_id(employee_id)
        return self.employee_service.get_employee_summary(normalized)

    def is_pm(self, employee_id: str) -> bool:
        return self._employee(employee_id).get("canDelegate") is True

    @staticmethod
    def _status_value(status: RunStatus | str) -> str:
        return status.value if isinstance(status, Enum) else str(status)

    @staticmethod
    def model_binding(record: RunRecord) -> tuple[str | None, str | None]:
        raw_contract = (
            record.kwargs.get("launch_contract")
            if isinstance(record.kwargs, Mapping)
            else None
        )
        try:
            contract = RunLaunchContractV1.parse(raw_contract)
        except RunLaunchContractValidationError:
            return None, None

        runtime_model_id = contract.context.get(RUNTIME_MODEL_ID_METADATA_KEY)
        if not isinstance(runtime_model_id, str) or not runtime_model_id.strip():
            runtime_model_id = None
        else:
            runtime_model_id = runtime_model_id.strip()
        return contract.model_profile_id, runtime_model_id

    @staticmethod
    def task_summary(record: RunRecord) -> str | None:
        metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        stored_summary = metadata.get("office_task_summary")
        if isinstance(stored_summary, str) and stored_summary.strip():
            return stored_summary.strip()

        raw_contract = (
            record.kwargs.get("launch_contract")
            if isinstance(record.kwargs, Mapping)
            else None
        )
        try:
            contract = RunLaunchContractV1.parse(raw_contract)
        except RunLaunchContractValidationError:
            return None
        execution_spec = contract.execution_spec
        execution_input = execution_spec.input if execution_spec is not None else None
        if not isinstance(execution_input, Mapping):
            return None
        messages = execution_input.get("messages")
        if not isinstance(messages, list):
            return None
        for message in reversed(messages):
            if not isinstance(message, Mapping) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return None

    @staticmethod
    def source_task_id(record: RunRecord) -> str:
        metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        task_id = metadata.get("office_task_id")
        return str(task_id).strip() if task_id else record.run_id

    def serialize_run(self, employee_id: str, record: RunRecord) -> dict[str, Any]:
        model_profile_id, runtime_model_id = self.model_binding(record)
        work = work_identity(record)
        metadata = record.metadata or {}
        return {
            "runId": record.run_id,
            "projectId": work.project_id if work else metadata.get('office_project_id'),
            "workId": work.work_id if work else None,
            "resumeSourceRunId": metadata.get('office_resume_source_run_id'),
            "employeeId": employee_id,
            "taskId": self.source_task_id(record),
            "taskSummary": self.task_summary(record),
            "threadId": record.thread_id,
            "assistantId": record.assistant_id,
            "status": self._status_value(record.status),
            "createdAt": record.created_at,
            "workStartedAt": metadata.get('office_work_started_at'),
            "runtimeFinishedAt": metadata.get('office_runtime_finished_at'),
            "updatedAt": record.updated_at,
            "error": record.error,
            "modelName": record.model_name,
            "modelProfileId": model_profile_id,
            "runtimeModelId": runtime_model_id,
            "llmCallCount": record.llm_call_count,
            "messageCount": record.message_count,
            "lastAiMessage": record.last_ai_message,
            "eventsFinalized": record.events_finalized,
            "finalizing": record.finalizing or record.completion_pending,
            "lastEventSeq": record.last_event_seq,
            "tokenUsage": {
                "input": record.total_input_tokens,
                "output": record.total_output_tokens,
                "total": record.total_tokens,
            },
        }

    async def resolve_run(
        self,
        employee_id: str,
        run_id: str,
    ) -> tuple[str, RunRecord]:
        employee = self._employee(employee_id)
        normalized = employee["id"]
        record = await self.run_manager.get(str(run_id), user_id=self.user_id)
        metadata = (
            record.metadata
            if record is not None and isinstance(record.metadata, Mapping)
            else {}
        )
        conversation_kind = metadata.get("office_conversation_kind")
        if conversation_kind not in {"private", "group"}:
            raise FileNotFoundError(f"Run not found: {run_id}")
        thread_id = self.employee_service.thread_id(normalized, conversation_kind)
        if (
            record is None
            or record.thread_id != thread_id
            or record.assistant_id != normalized
            or record.user_id != self.user_id
        ):
            raise FileNotFoundError(f"Run not found: {run_id}")
        return normalized, record

    async def get_run(self, employee_id: str, run_id: str) -> dict[str, Any]:
        normalized, record = await self.resolve_run(employee_id, run_id)
        return self.serialize_run(normalized, record)

    async def list_runs(
        self,
        employee_id: str,
        kind: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        if kind not in {"private", "group"}:
            raise ValueError(f"Unsupported conversation kind: {kind}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        employee = self._employee(employee_id)
        if employee.get("canDelegate") is True and kind == "private":
            raise ValueError("PM does not have a private conversation")
        thread_id = self.employee_service.thread_id(employee["id"], kind)
        records = await self.run_manager.list_by_thread(
            thread_id,
            user_id=self.user_id,
            limit=limit,
        )
        runs = []
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
            if (
                record.user_id != self.user_id
                or record.thread_id != thread_id
                or record.assistant_id != employee["id"]
                or metadata.get("office_conversation_kind") != kind
            ):
                continue
            runs.append(self.serialize_run(employee["id"], record))
        return {"runs": runs}

    async def find_message_run(
        self,
        employee_id: str,
        conversation_kind: str,
        message_id: str,
    ) -> RunRecord | None:
        employee = self._employee(employee_id)
        if conversation_kind not in {"private", "group"}:
            raise ValueError(f"Unsupported conversation kind: {conversation_kind}")
        thread_id = self.employee_service.thread_id(
            employee["id"],
            conversation_kind,
        )
        record = await self.run_manager.find_by_thread_metadata(
            thread_id,
            {
                "office_message_id": message_id,
                "office_conversation_kind": conversation_kind,
            },
            user_id=self.user_id,
        )
        if (
            record is None
            or record.user_id != self.user_id
            or record.thread_id != thread_id
            or record.assistant_id != employee["id"]
        ):
            return None
        return record

    async def find_resume_run(
        self,
        employee_id: str,
        conversation_kind: str,
        source_run_id: str,
        request_id: str,
    ) -> RunRecord | None:
        employee = self._employee(employee_id)
        thread_id = self.employee_service.thread_id(
            employee["id"],
            conversation_kind,
        )
        record = await self.run_manager.find_by_thread_metadata(
            thread_id,
            {
                "office_resume_source_run_id": source_run_id,
                "office_human_input_request_id": request_id,
            },
            user_id=self.user_id,
        )
        if (
            record is None
            or record.user_id != self.user_id
            or record.thread_id != thread_id
            or record.assistant_id != employee["id"]
        ):
            return None
        return record

    async def list_events(
        self,
        employee_id: str,
        run_id: str,
        *,
        after_seq: int | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise ValueError("limit must be an integer between 1 and 2000")
        if after_seq is not None and (
            isinstance(after_seq, bool)
            or not isinstance(after_seq, int)
            or after_seq < 0
        ):
            raise ValueError("after_seq must be a non-negative integer")
        _, record = await self.resolve_run(employee_id, run_id)
        events = await self.event_store.list_events(
            record.thread_id,
            record.run_id,
            limit=limit,
            after_seq=after_seq,
        )
        next_after_seq = events[-1]["seq"] if events else after_seq
        return {"events": events, "nextAfterSeq": next_after_seq}

    async def list_conversation_messages(
        self,
        employee_id: str,
        kind: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        if kind not in {"private", "group"}:
            raise ValueError(f"Unsupported conversation kind: {kind}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        employee = self._employee(employee_id)
        if employee.get("canDelegate") is True and kind == "private":
            raise ValueError("PM does not have a private conversation")
        thread_id = self.employee_service.thread_id(employee["id"], kind)
        checkpoint_tuple = await self.checkpointer.aget_tuple(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": "",
                }
            }
        )
        if checkpoint_tuple is None:
            return {"messages": []}
        checkpoint = (
            checkpoint_tuple.get("checkpoint")
            if isinstance(checkpoint_tuple, Mapping)
            else getattr(checkpoint_tuple, "checkpoint", None)
        )
        if not isinstance(checkpoint, Mapping):
            return {"messages": []}
        channel_values = checkpoint.get("channel_values")
        if not isinstance(channel_values, Mapping):
            return {"messages": []}
        thread_messages = channel_values.get("messages")
        if not isinstance(thread_messages, list):
            return {"messages": []}

        messages: list[dict[str, Any]] = []
        occurrences: dict[str, int] = {}
        source_runs: dict[str, str] = {}
        for message in thread_messages:
            additional_kwargs = getattr(message, "additional_kwargs", None)
            if additional_kwargs is None and isinstance(message, Mapping):
                additional_kwargs = message.get("additional_kwargs")
            message_metadata = (
                dict(additional_kwargs)
                if isinstance(additional_kwargs, Mapping)
                else {}
            )
            role: str | None = None
            content = ""
            if isinstance(message, HumanMessage):
                if message_metadata.get("hide_from_ui") is True or message_metadata.get("hidden") is True:
                    continue
                role = "user"
                content = get_original_user_content_text(
                    message.content,
                    message_metadata,
                ).strip()
            elif isinstance(message, AIMessage):
                if message_metadata.get("hide_from_ui") is True or message_metadata.get("hidden") is True:
                    continue
                role = "assistant"
                content = public_ai_text(message)
            elif isinstance(message, ToolMessage):
                response = read_human_input_response(message_metadata)
                if response is None:
                    continue
                role = "user"
                content = response["value"].strip()
            elif isinstance(message, Mapping):
                message_type = message.get("type") or message.get("role")
                if message_type in {"human", "user"}:
                    if message_metadata.get("hide_from_ui") is True or message_metadata.get("hidden") is True:
                        continue
                    role = "user"
                    original = message_metadata.get("original_user_content")
                    content = (
                        original.strip()
                        if isinstance(original, str)
                        else message_to_text(message).strip()
                    )
                elif message_type in {"ai", "assistant"}:
                    if message_metadata.get("hide_from_ui") is True or message_metadata.get("hidden") is True:
                        continue
                    role = "assistant"
                    content = public_ai_text(message)
                elif message_type == "tool":
                    response = read_human_input_response(message_metadata)
                    if response is None:
                        continue
                    role = "user"
                    content = response["value"].strip()
            if role is None or not content:
                continue
            raw_message_id = (
                message.get("id")
                if isinstance(message, Mapping)
                else getattr(message, "id", None)
            )
            run_id = message_metadata.get("run_id")
            created_at = message_metadata.get("timestamp")
            if isinstance(raw_message_id, str) and raw_message_id.strip():
                message_id = raw_message_id.strip()
            else:
                # Checkpoint positions move after compression; they are not IDs.
                identity = json.dumps([thread_id, role, content, run_id, created_at], ensure_ascii=False)
                ordinal = occurrences.get(identity, 0)
                occurrences[identity] = ordinal + 1
                message_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:{ordinal}"))
            is_human = isinstance(message, HumanMessage) or (
                isinstance(message, Mapping) and (message.get("type") or message.get("role")) in {"human", "user"}
            )
            if is_human and isinstance(run_id, str) and run_id:
                source_runs[message_id] = run_id
            messages.append(
                {
                    "messageId": message_id,
                    "runId": run_id if isinstance(run_id, str) else None,
                    "role": role,
                    "content": content,
                    "createdAt": (
                        created_at if isinstance(created_at, str) else None
                    ),
                }
            )
        visible = messages[-limit:]
        sources: dict[str, str | None] = {}
        for message in visible:
            source_run = source_runs.get(message["messageId"])
            if source_run is None:
                continue
            if source_run not in sources:
                sources[source_run] = None
                try:
                    _, record = await self.resolve_run(employee["id"], source_run)
                except FileNotFoundError:
                    continue
                # Read the existing Office receipt identity; never rewrite native
                # checkpoint IDs (which may acquire a __user suffix at runtime).
                if record.thread_id == thread_id:
                    source_id = (record.metadata or {}).get("office_message_id")
                    if isinstance(source_id, str) and source_id.strip():
                        sources[source_run] = source_id.strip()
            if sources[source_run]:
                message["sourceMessageId"] = sources[source_run]
        return {"messages": visible}
