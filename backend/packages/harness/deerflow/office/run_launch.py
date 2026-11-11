# 本文件属于 Office Run 启动适配模块：只把员工、Thread、模型、项目、lease、Resume 和 PM 员工快照转换为 DeerFlow Run 输入，并分配独立任务目录身份。
# 不负责消息路由、员工排队、历史查询、取消、状态投影或保存 Run/Thread/Checkpoint。
# 关联：message_pipeline.py 先取得员工许可，run_projection.py 投影运行状态并记录任务成果版本，Gateway start_run 执行唯一 Run 创建链。
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from deerflow.agents.human_input import parse_human_input_response
from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.execution_facts import WorkIdentity
from deerflow.office.projects import ProjectBinding
from deerflow.office.work_context import build_work_context
from deerflow.runtime.context_keys import (
    OFFICE_PROJECT_ID_CONTEXT_KEY,
    OFFICE_PROJECT_KNOWLEDGE_CONTEXT_KEY,
    OFFICE_WORK_CONTEXT_KEY,
    OFFICE_WORK_IDENTITY_CONTEXT_KEY,
    OFFICE_WORKFLOW_CONTEXT_KEY,
)
from deerflow.runtime.runs.execution import (
    RunExecutionSpecV1,
    RunLaunchContractV1,
    RunLaunchContractValidationError,
    checkpoint_configurable,
    checkpoint_run_id,
    find_owned_checkpoint_config,
)
from deerflow.runtime.runs.manager import RunRecord

RunStarter = Callable[..., Awaitable[RunRecord]]
_OFFICE_RUN_RECURSION_LIMIT = 1000


class OfficeRunLauncher:
    """Translate Office launch facts into one native DeerFlow start_run call."""

    def __init__(
        self,
        *,
        employee_service: OfficeEmployeeService,
        checkpointer: Any | None = None,
        project_knowledge=None,
    ) -> None:
        self.employee_service = employee_service
        self.checkpointer = checkpointer
        self.project_knowledge = project_knowledge

    async def _employee_registry_snapshot(self) -> list[dict[str, Any]]:
        # The Run needs names for delegation targets and model bindings for its config.
        # Live availability is checked when looking up or claiming an employee.
        return [
            {
                "employee_id": employee["id"],
                "display_name": employee["displayName"],
                "model_profile_id": employee["modelProfileId"],
            }
            for employee in await self.employee_service.list_employees()
            if not employee["isPm"]
        ]

    def _bind_project_knowledge(self, context: dict, *, project_id: str | None, employee_id: str, query: str) -> None:
        if self.project_knowledge is not None and project_id is not None:
            context[OFFICE_PROJECT_KNOWLEDGE_CONTEXT_KEY] = self.project_knowledge.context_for(
                project_id,
                self.employee_service.user_id,
                query=query,
                pm=self.employee_service.get_employee_summary(employee_id).get("canDelegate") is True,
            )

    def _run_body(
        self,
        employee_id: str,
        model_profile_id: str,
        execution_spec: RunExecutionSpecV1,
        *,
        office_lease_id: str,
        office_task_id: str | None = None,
        office_task_summary: str | None = None,
        project_binding: ProjectBinding | None = None,
        multitask_strategy: str = "reject",
        message_metadata: Mapping[str, Any] | None = None,
    ) -> SimpleNamespace:
        capability_index = self.employee_service.get_capability_index(employee_id, model_name=model_profile_id)
        runtime_tool_names = capability_index["runtimeAvailableTools"]
        if set(runtime_tool_names).intersection({'apply_patch', 'bash'}) and project_binding is None:
            raise RuntimeError(
                "Office project binding is required for write-capable runs"
            )
        metadata = {
            "office_employee_id": employee_id,
            "office_authorized_tool_groups": capability_index["authorizedToolGroups"],
            "office_runtime_tool_groups": capability_index["effectiveTools"],
            "office_unavailable_tool_groups": capability_index[
                "unavailableAuthorizedToolGroups"
            ],
            "office_runtime_tool_names": runtime_tool_names,
            "office_tool_availability_reasons": capability_index[
                "toolAvailabilityReasons"
            ],
            "office_lease_id": office_lease_id,
        }
        if office_task_id is not None:
            metadata["office_task_id"] = office_task_id
        if office_task_summary is not None:
            metadata["office_task_summary"] = office_task_summary
        if message_metadata is not None:
            metadata.update(message_metadata)
        if project_binding is not None:
            metadata["office_project_id"] = project_binding.project_id
        context = project_binding.to_context() if project_binding is not None else {}
        context['office_employee_id'] = employee_id
        if capability_index.get('toolContract') is not None:
            context['office_tool_contract'] = capability_index['toolContract']
        return SimpleNamespace(
            assistant_id=employee_id,
            model_profile_id=model_profile_id,
            execution_spec=execution_spec.to_dict(),
            metadata=metadata,
            config={"recursion_limit": _OFFICE_RUN_RECURSION_LIMIT},
            context=context,
            interrupt_before=None,
            interrupt_after=None,
            stream_mode=["values", "custom"],
            stream_subgraphs=False,
            on_disconnect="continue",
            multitask_strategy=multitask_strategy,
        )

    async def launch(
        self,
        employee_id: str,
        model_profile_id: str,
        execution_spec: RunExecutionSpecV1,
        *,
        start_run: RunStarter,
        request: Any,
        thread_id: str,
        office_lease_id: str,
        office_task_id: str | None = None,
        office_task_summary: str | None = None,
        project_binding: ProjectBinding | None = None,
        multitask_strategy: str = "reject",
        message_metadata: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        resolved_message_metadata = dict(message_metadata or {})
        employee = self.employee_service.get_employee_summary(employee_id)
        if employee.get("canDelegate") is True:
            resolved_message_metadata["office_employee_registry_snapshot"] = (
                await self._employee_registry_snapshot()
            )
        body = self._run_body(
            employee_id,
            model_profile_id,
            execution_spec,
            office_lease_id=office_lease_id,
            office_task_id=office_task_id,
            office_task_summary=office_task_summary,
            project_binding=project_binding,
            multitask_strategy=multitask_strategy,
            message_metadata=resolved_message_metadata,
        )
        self._bind_project_knowledge(
            body.context, project_id=project_binding.project_id if project_binding else None,
            employee_id=employee_id, query=office_task_summary or "",
        )
        work_id = (f"message:{resolved_message_metadata['office_message_id']}"
                   if resolved_message_metadata.get('office_message_id') else f'work:{uuid4().hex}')
        work = WorkIdentity(self.employee_service.user_id,
                            project_binding.project_id if project_binding else None, thread_id, work_id)
        body.context[OFFICE_WORK_IDENTITY_CONTEXT_KEY] = work.to_record()
        body.metadata['office_work_id'] = work.work_id
        body.context[OFFICE_WORK_CONTEXT_KEY] = build_work_context(
            **work.to_record(), latest_user_message=self._input_text(execution_spec) or office_task_summary or '')
        record = await start_run(body, thread_id, request)
        if record.thread_id != thread_id or record.assistant_id != employee_id:
            raise RuntimeError("DeerFlow returned a run outside the employee boundary")
        return record

    @staticmethod
    def _input_text(spec: RunExecutionSpecV1) -> str:
        messages = spec.input.get('messages', []) if isinstance(spec.input, Mapping) else []
        for message in reversed(messages):
            if isinstance(message, Mapping) and message.get('role') == 'user' and isinstance(message.get('content'), str):
                return message['content']
        return ''

    async def resume(self, source_record, resume_payload, **kwargs):
        return await self._resume(source_record, resume_payload, **kwargs)

    async def continue_from_checkpoint(self, source_record, **kwargs):
        if (source_record.metadata or {}).get('office_control') != 'paused':
            raise ValueError('Run was not manually paused')
        return await self._resume(source_record, None, manual=True, **kwargs)

    async def _resume(
        self,
        source_record: RunRecord,
        resume_payload: Any,
        *,
        start_run: RunStarter,
        request: Any,
        office_lease_id: str,
        office_queue_id: str,
        office_group_sequence_end: int | None = None,
        manual: bool = False,
    ) -> RunRecord:
        response = {"request_id": "continue", "value": "继续执行"} if manual else parse_human_input_response(resume_payload)
        if response is None:
            raise ValueError("Human input response is invalid")
        try:
            contract = RunLaunchContractV1.parse(
                source_record.kwargs.get("launch_contract")
            )
        except RunLaunchContractValidationError as exc:
            raise RuntimeError("Source run launch contract is invalid") from exc
        checkpoint_owner_run_id = source_record.run_id
        try:
            checkpoint_config = await find_owned_checkpoint_config(
                self.checkpointer,
                thread_id=source_record.thread_id,
                run_id=source_record.run_id,
            )
        except RuntimeError as error:
            declared = contract.execution_spec
            if declared.mode != "resume" or not declared.checkpoint_id:
                raise
            checkpoint = await self.checkpointer.aget_tuple(
                {
                    "configurable": {
                        "thread_id": source_record.thread_id,
                        "checkpoint_ns": "",
                        "checkpoint_id": declared.checkpoint_id,
                    }
                }
            )
            checkpoint_owner_run_id = checkpoint_run_id(checkpoint)
            checkpoint_config = checkpoint_configurable(checkpoint)
            if (
                checkpoint_owner_run_id != declared.source_run_id
                or checkpoint_config.get("thread_id") != source_record.thread_id
                or checkpoint_config.get("checkpoint_id") != declared.checkpoint_id
            ):
                raise error
        execution_spec = RunExecutionSpecV1(
            version=1,
            mode="resume",
            source_run_id=checkpoint_owner_run_id,
            checkpoint_id=str(checkpoint_config["checkpoint_id"]),
            resume_payload=None if manual else response,
        )
        metadata = dict(source_record.metadata or {})
        metadata.pop("office_runtime_finished_at", None)
        metadata.pop("office_group_sequence_end", None)
        metadata.pop("office_message_id", None)
        metadata.pop("office_control", None)
        metadata.update(
            {
                "office_lease_id": office_lease_id,
                "office_queue_id": office_queue_id,
                "office_resume_source_run_id": source_record.run_id,
                "office_human_input_request_id": None if manual else response["request_id"],
                "office_task_summary": response["value"],
            }
        )
        if office_group_sequence_end is not None:
            metadata["office_group_sequence_end"] = office_group_sequence_end
        context = {**contract.context, **contract.trusted_context}
        context['office_employee_id'] = source_record.assistant_id
        current_tools = self.employee_service.get_capability_index(source_record.assistant_id, model_name=contract.model_profile_id).get('toolContract')
        if current_tools is not None and (context.get('office_tool_contract') or {}).get('revision') != current_tools['revision']:
            raise RuntimeError('Employee tool contract missing or changed; cannot resume this Run')
        if current_tools is not None:
            from deerflow.office.tool_contract import ResolvedEmployeeTools, restrict_to_tool_snapshot
            current = ResolvedEmployeeTools(
                revision=current_tools['revision'], authorized_ids=tuple(current_tools['tools']),
                available_ids=tuple(current_tools['bindings']), unavailable_reasons={},
                runtime_names=tuple(current_tools['runtime_names']), derived_groups=(),
                bindings=current_tools['bindings'])
            restrict_to_tool_snapshot(current, context['office_tool_contract'])
        # Old persisted Runs have no work identity. Anchor their first Resume to
        # that source Run; every subsequent Resume persists and reuses it.
        raw_work = contract.trusted_context.get(OFFICE_WORK_IDENTITY_CONTEXT_KEY)
        work = WorkIdentity.from_record(raw_work) if raw_work is not None else WorkIdentity(
            self.employee_service.user_id, context.get(OFFICE_PROJECT_ID_CONTEXT_KEY),
            source_record.thread_id, f'run:{source_record.run_id}')
        if (work.user_id != self.employee_service.user_id or work.thread_id != source_record.thread_id
                or work.project_id != context.get(OFFICE_PROJECT_ID_CONTEXT_KEY)):
            raise ValueError('Resume work scope mismatch')
        context[OFFICE_WORK_IDENTITY_CONTEXT_KEY] = work.to_record()
        metadata['office_work_id'] = work.work_id
        previous = contract.trusted_context.get(OFFICE_WORK_CONTEXT_KEY)
        if previous is None:
            previous = build_work_context(**work.to_record(), latest_user_message=self._input_text(contract.execution_spec))
        context[OFFICE_WORK_CONTEXT_KEY] = build_work_context(**work.to_record(),
            latest_user_message=previous.get('latest_user_message', '') if manual else response['value'], previous=previous,
            answer_source=None if manual else {'source_run_id': source_record.run_id, 'request_id': response['request_id']})
        context.pop(OFFICE_WORKFLOW_CONTEXT_KEY, None)
        self._bind_project_knowledge(
            context, project_id=work.project_id, employee_id=source_record.assistant_id, query=response["value"],
        )
        if context.get(OFFICE_PROJECT_ID_CONTEXT_KEY):
            metadata['office_project_id'] = context[OFFICE_PROJECT_ID_CONTEXT_KEY]
        body = SimpleNamespace(
            assistant_id=source_record.assistant_id,
            model_profile_id=contract.model_profile_id,
            execution_spec=execution_spec.to_dict(),
            metadata=metadata,
            config=dict(contract.config),
            context=context,
            interrupt_before=contract.interrupt_before,
            interrupt_after=contract.interrupt_after,
            stream_mode=list(contract.stream_modes),
            stream_subgraphs=contract.stream_subgraphs,
            on_disconnect="continue",
            multitask_strategy="reject",
        )
        record = await start_run(body, source_record.thread_id, request)
        if (
            record.thread_id != source_record.thread_id
            or record.assistant_id != source_record.assistant_id
        ):
            raise RuntimeError(
                "DeerFlow returned a resumed run outside the employee boundary"
            )
        return record
