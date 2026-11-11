# 本文件属于 Office Run 启动适配测试模块：只验证员工、Thread、模型、项目与 lease 事实转换为 DeerFlow Run 输入。
# 不负责消息路由、员工排队、历史查询、状态投影或 DeerFlow Agent Loop 内部执行测试。
# 关联：run_launch.py 是被测模块，Gateway start_run 是唯一 Run 创建入口。
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from pathlib import Path

import pytest
from deerflow.config.paths import Paths


@pytest.fixture(autouse=True)
def workspace_storage(tmp_path, monkeypatch):
    EmployeeServiceStub.paths = Paths(str(tmp_path / 'data'))
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setattr(ProjectBindingStub, 'path', project)

from deerflow.office.run_launch import OfficeRunLauncher
from deerflow.runtime.runs.execution import RunExecutionSpecV1, RunLaunchContractV1


def test_launcher_has_no_project_write_gate_dependency() -> None:
    assert "project_write_gate" not in inspect.signature(OfficeRunLauncher).parameters


@pytest.mark.anyio
async def test_read_only_launch_does_not_allocate_or_initialize_a_worktree():
    class Reader(EmployeeServiceStub):
        @staticmethod
        def get_capability_index(employee_id, *, model_name=None):
            result = EmployeeServiceStub.get_capability_index(employee_id, model_name=model_name)
            return {**result, 'runtimeAvailableTools': ['read_file'], 'effectiveTools': ['file:read']}

    starter = CapturingStarter()
    launcher = OfficeRunLauncher(employee_service=Reader(), checkpointer=CheckpointerStub())
    await launcher.launch('employee-one', 'profile-primary',
                          RunExecutionSpecV1(version=1, mode='new', input={'messages': [{'role': 'user', 'content': '看看项目'}]}),
                          start_run=starter, request=object(), thread_id='read-only', office_lease_id='read-only',
                          project_binding=ProjectBindingStub())
    body = starter.calls[0][0]
    assert 'office_execution_workspace' not in body.context
    assert body.context['office_project_path'] == str(ProjectBindingStub.path)
    assert list(ProjectBindingStub.path.iterdir()) == []


class EmployeeServiceStub:
    user_id = "default"

    @staticmethod
    def get_capability_index(employee_id: str, *, model_name=None) -> dict[str, object]:
        assert employee_id == "employee-one"
        return {
            "authorizedToolGroups": ["file:read", "file:write"],
            "effectiveTools": ["file:read", "file:write"],
            "unavailableAuthorizedToolGroups": [],
            "runtimeAvailableTools": ["read_file", "apply_patch"],
            "toolAvailabilityReasons": {},
        }

    @staticmethod
    def get_employee_summary(employee_id: str) -> dict[str, object]:
        return {"id": employee_id, "canDelegate": False}


class ProjectBindingStub:
    project_id = "project-one"
    path = Path('D:/workspace/project-one')

    @classmethod
    def to_context(cls) -> dict[str, object]:
        return {
            "office_project_id": "project-one",
            "office_project_path": str(cls.path),
        }


class CapturingStarter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, object]] = []

    async def __call__(self, body, thread_id, request):
        self.calls.append((body, thread_id, request))
        return SimpleNamespace(
            run_id="run-one",
            thread_id=thread_id,
            assistant_id=body.assistant_id,
        )


class CheckpointerStub:
    async def alist(self, config):
        assert config == {
            "configurable": {"thread_id": "office__default__employee-one__private"}
        }
        yield SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": "office__default__employee-one__private",
                    "checkpoint_ns": "",
                    "checkpoint_id": "checkpoint-one",
                }
            },
            metadata={"deerflow_run_id": "run-interrupted"},
        )


@pytest.mark.anyio
async def test_launcher_starts_write_capable_run_without_project_gate_dependency() -> None:
    starter_called = asyncio.Event()

    class Starter:
        async def __call__(self, body, thread_id, request):
            del body, request
            starter_called.set()
            return SimpleNamespace(
                run_id="run-one",
                thread_id=thread_id,
                assistant_id="employee-one",
            )

    launcher = OfficeRunLauncher(
        employee_service=EmployeeServiceStub(),
        checkpointer=CheckpointerStub(),
    )
    await launcher.launch(
        "employee-one",
        "profile-primary",
        RunExecutionSpecV1(
            version=1,
            mode="new",
            input={"messages": [{"role": "user", "content": "build it"}]},
        ),
        start_run=Starter(),
        request=object(),
        thread_id="office__default__employee-one__private",
        office_lease_id="lease-one",
        project_binding=ProjectBindingStub(),
    )

    assert starter_called.is_set()


@pytest.mark.anyio
async def test_launcher_converts_office_facts_into_one_deerflow_start_run_call() -> None:
    starter = CapturingStarter()
    request = object()
    launcher = OfficeRunLauncher(
        employee_service=EmployeeServiceStub(),
        checkpointer=CheckpointerStub(),
    )
    execution_spec = RunExecutionSpecV1(
        version=1,
        mode="new",
        input={"messages": [{"role": "user", "content": "build it"}]},
    )

    record = await launcher.launch(
        "employee-one",
        "profile-primary",
        execution_spec,
        start_run=starter,
        request=request,
        thread_id="office__default__employee-one__group",
        office_lease_id="lease-one",
        office_task_id="task-one",
        office_task_summary="build it",
        project_binding=ProjectBindingStub(),
        message_metadata={
            "office_message_id": "message-one",
            "office_conversation_kind": "group",
        },
    )

    assert record.run_id == "run-one"
    assert len(starter.calls) == 1
    body, thread_id, captured_request = starter.calls[0]
    assert thread_id == "office__default__employee-one__group"
    assert captured_request is request
    assert body.assistant_id == "employee-one"
    assert body.model_profile_id == "profile-primary"
    assert body.execution_spec == execution_spec.to_dict()
    assert body.metadata == {
        "office_employee_id": "employee-one",
        "office_authorized_tool_groups": ["file:read", "file:write"],
        "office_runtime_tool_groups": ["file:read", "file:write"],
        "office_unavailable_tool_groups": [],
        "office_runtime_tool_names": ["read_file", "apply_patch"],
        "office_tool_availability_reasons": {},
        "office_lease_id": "lease-one",
        "office_project_id": "project-one",
        "office_task_id": "task-one",
        "office_task_summary": "build it",
        "office_message_id": "message-one",
        "office_conversation_kind": "group",
        "office_work_id": "message:message-one",
    }
    assert "office_execution_workspace" not in body.context
    assert body.context == {
        "office_employee_id": "employee-one",
        "office_project_id": "project-one",
        "office_project_path": str(ProjectBindingStub.path),
        "office_work": {"user_id": "default", "project_id": "project-one", "thread_id": thread_id, "work_id": "message:message-one"},
        "office_work_context": {"user_id": "default", "project_id": "project-one", "thread_id": thread_id, "work_id": "message:message-one",
                                "goal": "build it", "latest_user_message": "build it", "constraints": [], "decisions": [], "result_refs": [], "proposals": [], "user_answers": []},
    }


@pytest.mark.anyio
async def test_launcher_injects_only_server_project_knowledge_for_employee() -> None:
    class KnowledgeStub:
        def context_for(self, project_id, user_id, *, query, pm):
            assert (project_id, user_id, query, pm) == (
                'project-one', 'default', 'build todo CRUD', False,
            )
            return {'projectId': project_id, 'facts': [{'statement': 'Use SQLite'}], 'unresolved': []}

    starter = CapturingStarter()
    launcher = OfficeRunLauncher(
        employee_service=EmployeeServiceStub(),
        project_knowledge=KnowledgeStub(),
    )
    await launcher.launch(
        'employee-one',
        'profile-primary',
        RunExecutionSpecV1(version=1, mode='new', input={'messages': []}),
        start_run=starter,
        request=object(),
        thread_id='office__default__employee-one__private',
        office_lease_id='lease-memory',
        office_task_summary='build todo CRUD',
        project_binding=ProjectBindingStub(),
    )
    assert starter.calls[0][0].context['office_project_knowledge']['facts'][0]['statement'] == 'Use SQLite'


@pytest.mark.anyio
async def test_write_capable_employee_requires_project_binding() -> None:
    starter = CapturingStarter()
    launcher = OfficeRunLauncher(
        employee_service=EmployeeServiceStub(),
        checkpointer=CheckpointerStub(),
    )

    with pytest.raises(
        RuntimeError,
        match='Office project binding is required for write-capable runs',
    ):
        await launcher.launch(
            'employee-one',
            'profile-primary',
            RunExecutionSpecV1(
                version=1,
                mode='new',
                input={'messages': [{'role': 'user', 'content': 'write code'}]},
            ),
            start_run=starter,
            request=object(),
            thread_id='office__default__employee-one__group',
            office_lease_id='lease-unbound',
            project_binding=None,
        )

    assert starter.calls == []


@pytest.mark.anyio
async def test_launcher_rejects_a_run_returned_outside_the_employee_thread_boundary() -> None:
    class WrongStarter:
        async def __call__(self, _body, _thread_id, _request):
            return SimpleNamespace(
                run_id="run-wrong",
                thread_id="wrong-thread",
                assistant_id="employee-one",
            )

    launcher = OfficeRunLauncher(
        employee_service=EmployeeServiceStub(),
        checkpointer=CheckpointerStub(),
    )

    with pytest.raises(RuntimeError, match="outside the employee boundary"):
        await launcher.launch(
            "employee-one",
            "profile-primary",
            RunExecutionSpecV1(
                version=1,
                mode="new",
                input={"messages": [{"role": "user", "content": "build it"}]},
            ),
            start_run=WrongStarter(),
            request=object(),
            thread_id="office__default__employee-one__group",
            office_lease_id="lease-one",
            project_binding=ProjectBindingStub(),
        )


@pytest.mark.anyio
async def test_pm_launcher_captures_current_employee_registry_snapshot() -> None:
    class PMEmployeeService(EmployeeServiceStub):
        @staticmethod
        def get_capability_index(_employee_id: str, *, model_name=None) -> dict[str, object]:
            return {
                "authorizedToolGroups": ["file:read"],
                "effectiveTools": ["file:read"],
                "unavailableAuthorizedToolGroups": [],
                "runtimeAvailableTools": ["read_file", "task"],
                "toolAvailabilityReasons": {},
            }

        @staticmethod
        def get_employee_summary(employee_id: str) -> dict[str, object]:
            return {"id": employee_id, "canDelegate": employee_id == "pm"}

        async def list_employees(self) -> list[dict[str, object]]:
            return [
                {"id": "pm", "displayName": "项目经理", "modelProfileId": "profile-primary", "isPm": True},
                {"id": "frontend-a", "displayName": "前端员工", "modelProfileId": "profile-worker", "isPm": False},
            ]

    starter = CapturingStarter()
    launcher = OfficeRunLauncher(
        employee_service=PMEmployeeService(),
        checkpointer=CheckpointerStub(),
    )
    await launcher.launch(
        "pm",
        "profile-primary",
        RunExecutionSpecV1(
            version=1,
            mode="new",
            input={"messages": [{"role": "user", "content": "plan it"}]},
        ),
        start_run=starter,
        request=object(),
        thread_id="office__default__pm__group",
        office_lease_id="lease-pm",
        message_metadata={"office_preselected_employee_ids": ["frontend-a"]},
    )

    body = starter.calls[0][0]
    assert body.metadata["office_employee_registry_snapshot"] == [
        {"employee_id": "frontend-a", "display_name": "前端员工", "model_profile_id": "profile-worker"}
    ]
    assert body.metadata["office_preselected_employee_ids"] == ["frontend-a"]
    assert "office_project_write_required" not in body.metadata


@pytest.mark.anyio
async def test_launcher_propagates_deerflow_start_failure() -> None:
    class FailingStarter:
        async def __call__(self, _body, _thread_id, _request):
            raise RuntimeError("start failed")

    launcher = OfficeRunLauncher(
        employee_service=EmployeeServiceStub(),
        checkpointer=CheckpointerStub(),
    )

    with pytest.raises(RuntimeError, match="start failed"):
        await launcher.launch(
            "employee-one",
            "profile-primary",
            RunExecutionSpecV1(
                version=1,
                mode="new",
                input={"messages": [{"role": "user", "content": "build it"}]},
            ),
            start_run=FailingStarter(),
            request=object(),
            thread_id="office__default__employee-one__group",
            office_lease_id="lease-one",
            project_binding=ProjectBindingStub(),
        )

@pytest.mark.anyio
async def test_resume_reuses_the_source_run_contract_and_latest_owned_checkpoint() -> None:
    starter = CapturingStarter()
    from unittest.mock import Mock
    employee_service = EmployeeServiceStub()
    employee_service.get_capability_index = Mock(wraps=employee_service.get_capability_index)
    launcher = OfficeRunLauncher(
        employee_service=employee_service,
        checkpointer=CheckpointerStub(),
    )
    source_contract = RunLaunchContractV1.build(
        model_profile_id="profile-primary",
        execution_spec=RunExecutionSpecV1(
            version=1,
            mode="new",
            input={"messages": [{"role": "user", "content": "original"}]},
        ),
        config={"recursion_limit": 1000},
        context={},
        trusted_context={
            "office_project_id": "project-one",
            "office_project_path": str(ProjectBindingStub.path),
        },
        stream_modes=["values", "custom"],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    )
    source = SimpleNamespace(
        run_id="run-interrupted",
        thread_id="office__default__employee-one__private",
        assistant_id="employee-one",
        kwargs={"launch_contract": source_contract.to_dict()},
        metadata={
            "office_employee_id": "employee-one",
            "office_conversation_kind": "private",
            "office_task_id": "task-one",
            },
    )

    record = await launcher.resume(
        source,
        {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-one",
            "response_kind": "text",
            "value": "D:/workspace",
        },
        start_run=starter,
        request=object(),
        office_lease_id="lease-resume",
        office_queue_id="resume:run-interrupted:clarification:call-one",
        office_group_sequence_end=12,
    )

    assert record.run_id == "run-one"
    body, thread_id, _ = starter.calls[0]
    assert thread_id == source.thread_id
    assert body.assistant_id == "employee-one"
    assert body.model_profile_id == "profile-primary"
    employee_service.get_capability_index.assert_called_once_with('employee-one', model_name='profile-primary')
    assert body.execution_spec == {
        "version": 1,
        "mode": "resume",
        "source_run_id": "run-interrupted",
        "checkpoint_id": "checkpoint-one",
        "resume_payload": {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-one",
            "response_kind": "text",
            "value": "D:/workspace",
        },
    }
    assert 'office_execution_workspace' not in body.context
    assert body.context == {
        'office_employee_id': 'employee-one',
        "office_project_id": "project-one",
        "office_project_path": str(ProjectBindingStub.path),
        "office_work": {"user_id": "default", "project_id": "project-one", "thread_id": source.thread_id, "work_id": "run:run-interrupted"},
        "office_work_context": {"user_id": "default", "project_id": "project-one", "thread_id": source.thread_id, "work_id": "run:run-interrupted",
                                "goal": "original", "latest_user_message": "D:/workspace", "constraints": [], "decisions": [], "result_refs": [], "proposals": [],
                                "user_answers": [{"source_run_id": "run-interrupted", "request_id": "clarification:call-one", "text": "D:/workspace"}]},
    }
    assert body.metadata["office_resume_source_run_id"] == "run-interrupted"
    assert body.metadata["office_queue_id"] == "resume:run-interrupted:clarification:call-one"
    assert body.metadata["office_lease_id"] == "lease-resume"
    assert body.metadata["office_group_sequence_end"] == 12
    assert body.metadata["office_project_id"] == "project-one"


@pytest.mark.anyio
async def test_resume_uses_declared_parent_checkpoint_for_forked_run() -> None:
    starter = CapturingStarter()

    class ForkedCheckpoint:
        async def alist(self, _config):
            if False:
                yield None

        async def aget_tuple(self, config):
            assert config["configurable"]["checkpoint_id"] == "checkpoint-parent"
            return SimpleNamespace(
                config={
                    "configurable": {
                        "thread_id": "office__default__employee-one__private",
                        "checkpoint_ns": "",
                        "checkpoint_id": "checkpoint-parent",
                    }
                },
                metadata={"deerflow_run_id": "run-parent"},
            )

    source_contract = RunLaunchContractV1.build(
        model_profile_id="profile-primary",
        execution_spec=RunExecutionSpecV1(
            version=1,
            mode="resume",
            source_run_id="run-parent",
            checkpoint_id="checkpoint-parent",
            resume_payload=None,
        ),
        config={"recursion_limit": 1000},
        context={},
        trusted_context={},
        stream_modes=["values", "custom"],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    )
    source = SimpleNamespace(
        run_id="run-forked",
        thread_id="office__default__employee-one__private",
        assistant_id="employee-one",
        kwargs={"launch_contract": source_contract.to_dict()},
        metadata={
            "office_employee_id": "employee-one",
            "office_conversation_kind": "private",
        },
    )

    launcher = OfficeRunLauncher(
        employee_service=EmployeeServiceStub(),
        checkpointer=ForkedCheckpoint(),
    )
    record = await launcher.resume(
        source,
        {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:forked",
            "response_kind": "text",
            "value": "继续",
        },
        start_run=starter,
        request=object(),
        office_lease_id="lease-resume",
        office_queue_id="resume:run-forked:clarification:forked",
    )

    assert record.run_id == "run-one"
    body, _, _ = starter.calls[0]
    assert body.execution_spec["source_run_id"] == "run-parent"
    assert body.execution_spec["checkpoint_id"] == "checkpoint-parent"
