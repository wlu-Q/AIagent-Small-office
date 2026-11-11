"""Real Office pipeline/launcher/projection/SQLite; controlled Run boundary, no remote LLM."""

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from test_office_message_pipeline import EmployeeServiceStub, RunViewStub

from app.gateway import deps
from deerflow.config.paths import Paths
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.message_pipeline import OfficeMessagePipeline
from deerflow.office.messages import OfficeMessageCommand
from deerflow.office.project_knowledge import ProjectKnowledgeStore
from deerflow.office.project_knowledge_service import ProjectKnowledgeService
from deerflow.office.projects import OfficeProjectService
from deerflow.office.run_gate import EmployeeRunGate
from deerflow.office.run_launch import OfficeRunLauncher
from deerflow.office.run_projection import OfficeRunProjection
from deerflow.office.workflow_knowledge import WorkflowKnowledgeSource
from deerflow.office.workflows import WorkflowStore
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.execution import CHECKPOINT_RUN_ID_METADATA_KEY, RunExecutionSpecV1, RunLaunchContractV1
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus


@pytest.mark.anyio
async def test_gateway_actually_installs_shared_workflow_services(tmp_path, monkeypatch):
    from office_history_fixture import seed_history

    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: Paths(tmp_path))
    monkeypatch.setenv("SMALL_OFFICE_USER_ID", "default")
    history = WorkflowStore(paths=Paths(tmp_path), user_id='default')
    legacy = seed_history(history, project_id='legacy-project')
    app = FastAPI()
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    app.state.checkpointer = InMemorySaver()
    app.state.run_manager = RunManager()
    app.state.run_event_store = MemoryRunEventStore()
    await deps._initialize_office_runtime_services(app, SimpleNamespace())
    assert app.state.office_workflow_store.current(project_id="project-a") is None
    for component in (app.state.office_message_pipeline, app.state.office_run_projection, app.state.office_run_launcher):
        assert not hasattr(component, 'workflow_controller')
    assert not hasattr(app.state.office_workflow_service, 'ensure_continuation')
    assert not hasattr(app.state.office_workflow_service, 'reconcile')
    assert app.state.office_run_launcher.project_knowledge is app.state.office_project_knowledge
    assert history.get(legacy.workflow_id) == legacy
    assert await app.state.run_manager.list_by_thread('office__default__pm__group', user_id='default') == []


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    paths = Paths(tmp_path / "state")
    monkeypatch.setattr("deerflow.config.paths._paths", paths)
    employees = EmployeeServiceStub()
    employees.paths = paths
    employees.get_capability_index = lambda _, *, model_name=None: {
        "runtimeAvailableTools": ["read_file", "task"],
        "authorizedToolGroups": ["file:read"],
        "effectiveTools": ["file:read"],
        "unavailableAuthorizedToolGroups": [],
        "toolAvailabilityReasons": {},
    }
    workflows = WorkflowStore(paths=paths, user_id="user-a")
    knowledge = ProjectKnowledgeService(ProjectKnowledgeStore(paths=paths, user_id="user-a"), WorkflowKnowledgeSource(workflows))
    project = OfficeProjectService(paths=paths, user_id="user-a")
    root = tmp_path / "project"
    root.mkdir()
    project.bind(root)
    group = GroupMessageStore(paths=paths, user_id="user-a")
    from deerflow.office.tool_manifest import EmployeeToolManifest, save_tool_manifest
    pm_dir = paths.user_agent_dir("user-a", "pm")
    pm_dir.mkdir(parents=True, exist_ok=True)
    save_tool_manifest(
        pm_dir,
        EmployeeToolManifest(1, (
            "ask_clarification",
            "capture_user_message",
            "read_file",
            "search_employee_registry",
            "task",
        )),
        expected_revision="",
    )
    gate = EmployeeRunGate()
    launcher = OfficeRunLauncher(employee_service=employees, project_knowledge=knowledge)

    async def registry():
        return [
            {
                "employee_id": "frontend-a",
                "display_name": "全栈工程师",
                "template_id": "fullstack",
                "enabled": True,
                "can_delegate": False,
                "is_pm": False,
                "working": False,
                "category": "engineering",
                "tags": ["frontend", "backend", "testing"],
                "specialties": ["React", "FastAPI", "CRUD"],
                "skills": [],
                "tool_groups": ["file:read", "file:write"],
            }
        ]

    launcher._employee_registry_snapshot = registry
    employees.update_runtime_projection = lambda *args: None
    projection = OfficeRunProjection(employee_service=employees, user_id="user-a", employee_run_gate=gate, group_store=group)
    pipeline = OfficeMessagePipeline(user_id="user-a", employee_service=employees, group_store=group, employee_run_gate=gate, run_launcher=launcher, run_view=RunViewStub(), project_service=project)
    records = []

    class ResultCheckpoints:
        """The controlled Run boundary also supplies that Run's full checkpoint."""

        async def alist(self, config):
            for record in reversed(records):
                if record.thread_id == config["configurable"]["thread_id"]:
                    yield await self.aget_tuple({"configurable": {"checkpoint_id": record.run_id}})

        async def aget_tuple(self, config):
            record = next(record for record in records if record.run_id == config["configurable"]["checkpoint_id"])
            return SimpleNamespace(
                config={"configurable": {"thread_id": record.thread_id, "checkpoint_id": record.run_id}},
                metadata={CHECKPOINT_RUN_ID_METADATA_KEY: record.run_id},
                checkpoint={"channel_values": {"structured_response": json.loads(record.last_ai_message) if record.last_ai_message else None}},
            )

    projection.checkpointer = ResultCheckpoints()

    async def start(body, thread_id, request):
        contract = RunLaunchContractV1.build(
            model_profile_id=body.model_profile_id,
            execution_spec=RunExecutionSpecV1.parse(body.execution_spec),
            config=body.config,
            context={},
            trusted_context=body.context,
            stream_modes=body.stream_mode,
            stream_subgraphs=False,
            interrupt_before=None,
            interrupt_after=None,
        )
        record = SimpleNamespace(
            run_id=f"r{len(records) + 1}",
            user_id="user-a",
            assistant_id=body.assistant_id,
            thread_id=thread_id,
            metadata=body.metadata,
            kwargs={"launch_contract": contract.to_dict()},
            status=RunStatus.running,
            last_ai_message=None,
            body=body,
        )
        records.append(record)
        return record

    return SimpleNamespace(workflows=workflows, project=project, pipeline=pipeline, group=group, gate=gate, projection=projection, start=start, records=records)


async def send(rt, text, mentions=("pm",), mid="m1"):
    return await rt.pipeline.send(mid, OfficeMessageCommand("group", text, None, mentions), start_run=rt.start, request=object())












class _OwnedCheckpoint:
    """One Run-owned checkpoint exposing fixed state channels."""

    def __init__(self, run_id, channel_values):
        self.run_id = run_id
        self.channel_values = channel_values

    async def aget_tuple(self, config):
        return {
            "checkpoint": {"channel_values": self.channel_values},
            "metadata": {CHECKPOINT_RUN_ID_METADATA_KEY: self.run_id},
        }






@pytest.mark.anyio
async def test_native_task_rejects_missing_native_identity_even_if_metadata_claims_approved(runtime):
    rt = runtime
    await send(rt, "新增 CRUD")
    run = rt.records[0]
    task = importlib.import_module("deerflow.tools.builtins.task_tool")
    result = await task.task_tool.coroutine(
        runtime=SimpleNamespace(context={**run.body.context, "user_id": "user-a"}, config={"metadata": {**run.metadata, "office_workflow_stage": "executing_employees"}}, state={}),
        description="绕过确认",
        prompt="修改代码",
        subagent_type="frontend-a",
        tool_call_id="illegal-task",
    )
    assert result.update["messages"][0].additional_kwargs["subagent_status"] == "failed"
    assert await rt.gate.get_admission("illegal-task") is None






@pytest.mark.anyio
async def test_queued_pm_message_does_not_move_to_a_new_project(runtime, tmp_path):
    rt = runtime
    await send(rt, "项目 A 需求")
    await send(rt, "确认实施", mid="queued-for-a")
    other = tmp_path / "other-project"
    other.mkdir()
    rt.project.bind(other)
    first = rt.records[0]
    first.last_ai_message = "项目 A 的回答"
    first.status = RunStatus.success
    await rt.projection.handle_run_completed(first)
    await asyncio.gather(*rt.pipeline._queued_message_tasks)
    assert len(rt.records) == 1
    assert rt.workflows.current(project_id=rt.project.get_binding().project_id) is None
    assert await rt.gate.list_pending() == []
    assert "项目已切换" in rt.group.list()[-1].content


@pytest.mark.anyio
async def test_real_gateway_agent_worker_finishes_native_pm_answer(runtime, monkeypatch):
    """Only the model and employee package lookup are controlled; no fake Worker.

    Exercise Office's actual trusted start adapter, launch contract, Agent factory,
    compiled LangGraph, Checkpoint, Run/Event persistence and completion hooks.
    This is deterministic integration evidence, not remote-model quality evidence.
    """
    from unittest.mock import AsyncMock

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    from app.gateway import services
    from app.gateway.routers.office import _start_office_run
    from deerflow.agents.lead_agent import agent
    from deerflow.config.agents_config import AgentConfig
    from deerflow.config.app_config import AppConfig
    from deerflow.runtime.runs.worker import RunContext
    from deerflow.tools.builtins.task_tool import task_tool

    rt = runtime
    app_config = AppConfig.model_validate(
        {
            "models": [{"name": "profile-primary", "model": "controlled-model", "use": "unused"}],
            "tools": [{"name": "read_file", "use": "deerflow.sandbox.tools:read_file_tool", "group": "file:read"}],
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "memory": {"enabled": False},
            "title": {"enabled": False},
            "summarization": {"enabled": False},
            "tool_search": {"enabled": False},
        }
    )
    manager = RunManager()
    checkpoint = InMemorySaver()
    rt.projection.checkpointer = checkpoint
    events = MemoryRunEventStore()
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("office__user-a__pm__group", assistant_id="pm", user_id="user-a")
    completion_states = []

    async def complete_office_run(record):
        completion_states.append((record.status, record.completion_pending, record.events_finalized))
        await rt.projection.handle_run_completed(record)

    run_ctx = RunContext(
        checkpointer=checkpoint,
        event_store=events,
        thread_store=thread_store,
        app_config=app_config,
        on_run_started=rt.projection.handle_run_started,
        on_run_input_checkpointed=rt.projection.handle_run_input_checkpointed,
        on_run_completed=complete_office_run,
    )
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    monkeypatch.setattr(services, "get_run_manager", lambda _: manager)
    monkeypatch.setattr(services, "get_run_context", lambda _: run_ctx)
    monkeypatch.setattr(services, "get_stream_bridge", lambda _: bridge)
    monkeypatch.setattr(agent, "is_office_managed_agent", lambda *args, **kwargs: True)
    monkeypatch.setattr(agent, "load_agent_config", lambda _: AgentConfig(name="pm", skills=[], runtime_contract={"authority": {"can_delegate": True}}))
    monkeypatch.setattr(agent, "load_agent_soul", lambda _: "负责研发规划的 PM")
    monkeypatch.setattr(agent, "_load_enabled_employee_skills", lambda *args, **kwargs: [])
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [task_tool])
    model_calls = []
    bound_tools = []
    prompts = []
    create_agent = agent.create_agent

    def capture_agent(**kwargs):
        bound_tools.append([tool.name for tool in kwargs["tools"]])
        prompts.append(kwargs["system_prompt"])
        return create_agent(**kwargs)

    monkeypatch.setattr(agent, "create_agent", capture_agent)

    class ControlledModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            model_calls.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    records = []
    async def start(body, thread_id, request):
        assert 'structured_response' not in body.execution_spec['input']
        assert 'office_workflow' not in body.context
        assert body.metadata['office_preselected_employee_ids'] == ['frontend-a']
        model = ControlledModel(responses=[AIMessage(content="当前前端使用 React；本次仅回答架构问题。")])
        monkeypatch.setattr(agent, "create_chat_model", lambda **kwargs: model)
        body.config["recursion_limit"] = 120
        record = await _start_office_run(body, thread_id, request)
        records.append(record)
        return record

    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="user-a"), auth_source="internal"), headers={}, cookies={})
    try:
        await rt.pipeline.send("native-answer", OfficeMessageCommand("group", "直接说明当前项目的前端架构，不修改文件，不指派员工。已知前端使用 React。", None, ("pm", "frontend-a")), start_run=start, request=request)
        await asyncio.wait_for(records[-1].task, timeout=20)
        record = records[-1]
        assert record.status == RunStatus.success
        assert completion_states[-1] == (RunStatus.success, True, True)
        assert record.finalizing is False and record.completion_pending is False
        assert record.last_ai_message == "当前前端使用 React；本次仅回答架构问题。"
        assert len(model_calls) == 1
        assert len(records) == 1 and record.assistant_id == "pm"
        assert bound_tools and all("task" in names for names in bound_tools)
        assert all("<office_workflow>" not in prompt for prompt in prompts)
        assert all("<office_work_context>" in prompt for prompt in prompts)
        saved = await checkpoint.aget_tuple({"configurable": {"thread_id": record.thread_id}})
        assert saved is not None
        assert "structured_response" not in saved.checkpoint["channel_values"]
        assert record.last_event_seq > 0
        assert await rt.gate.list_pending() == []
        # Native PM output is now independent of the old business protocol.
        assert rt.group.list()[-1].content == record.last_ai_message
    finally:
        await rt.pipeline.shutdown()
        await manager.shutdown()
