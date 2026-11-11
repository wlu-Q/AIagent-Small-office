# 本文件属于 Stage 7 Office 产品黑盒验收模块：只通过公开 HTTP API 验证最终员工、消息、Mention 和 FIFO 行为。
# 不负责模拟第二套 Agent Loop、复制 Run 状态、验证模型文案质量或保存长期测试事实。
# 关联：Gateway office router 是唯一被测入口，真实 DeerFlow 模型执行由独立端到端证据测试负责。
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.gateway.routers import office
from deerflow.config.paths import Paths
from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.message_pipeline import OfficeMessagePipeline
from deerflow.office.run_gate import EmployeeRunGate
from deerflow.office.run_view import OfficeRunView
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import CancelOutcome, RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.user_context import get_effective_user_id

MODEL_PROFILE_ID = "profile-primary"


@pytest.fixture(autouse=True)
def _use_repository_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let template inventory reuse the repository's DeerFlow config."""
    config_path = Path(__file__).parents[2] / "config.yaml"
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))

EMPLOYEE_PAYLOADS = (
    {
        "id": "liming",
        "templateId": "product-manager",
        "displayName": "黎明",
        "avatar": "adam",
    },
    {
        "id": "zhouli",
        "templateId": "frontend-engineer",
        "displayName": "周礼",
        "avatar": "ash",
    },
    {
        "id": "sunhe",
        "templateId": "backend-engineer",
        "displayName": "孙河",
        "avatar": "lucy",
    },
    {
        "id": "ousi",
        "templateId": "test-engineer",
        "displayName": "藕丝",
        "avatar": "nancy",
    },
    {
        "id": "sisi",
        "templateId": "research-documentation",
        "displayName": "思思",
        "avatar": "sara",
    },
)


class _ControlledRunLauncher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def launch(
        self,
        employee_id: str,
        model_profile_id: str,
        execution_spec: Any,
        **kwargs: Any,
    ) -> SimpleNamespace:
        run_id = f"run-{employee_id}-{len(self.calls) + 1}"
        self.calls.append(
            {
                "run_id": run_id,
                "employee_id": employee_id,
                "model_profile_id": model_profile_id,
                "execution_spec": execution_spec,
                **kwargs,
            }
        )
        return SimpleNamespace(
            run_id=run_id,
            assistant_id=employee_id,
            thread_id=kwargs["thread_id"],
            metadata={"office_task_summary": kwargs.get("office_task_summary")},
        )


class _RunView:
    run_manager = SimpleNamespace(list_by_thread=AsyncMock(return_value=[]))
    async def find_message_run(self, *_args: Any) -> None:
        return None

    @staticmethod
    def serialize_run(employee_id: str, record: Any) -> dict[str, Any]:
        return {"employeeId": employee_id, "runId": record.run_id}

    @staticmethod
    def task_summary(record: Any) -> str | None:
        return record.metadata.get("office_task_summary")


class _StatefulRunManager(RunManager):
    async def cancel(self, run_id: str, *, action: str, user_id: str) -> CancelOutcome:
        record = await self.get(run_id, user_id=user_id)
        if record is None:
            return CancelOutcome.unknown
        if record.status not in {RunStatus.pending, RunStatus.running}:
            return CancelOutcome.not_cancellable
        record.abort_action = action
        record.status = RunStatus.interrupted
        return CancelOutcome.cancelled


class _StatefulRunLauncher:
    def __init__(
        self,
        *,
        run_manager: _StatefulRunManager,
        event_store: MemoryRunEventStore,
        user_id: str,
    ) -> None:
        self.run_manager = run_manager
        async def current_checkpoint(config):
            records = await run_manager.list_by_thread(config['configurable']['thread_id'], user_id=user_id)
            record = records[0] if records else None
            if record is None or record.status != RunStatus.interrupted:
                return None
            from langgraph.types import Interrupt
            return SimpleNamespace(metadata={'deerflow_run_id': record.run_id}, pending_writes=[
                ('ask', '__interrupt__', (Interrupt(value={'kind': 'human_input_request', 'source': 'ask_clarification',
                    'request_id': 'clarification-blackbox-1'}),))])
        self.checkpointer = SimpleNamespace(aget_tuple=current_checkpoint)
        self.event_store = event_store
        self.user_id = user_id
        self.calls: list[dict[str, Any]] = []

    async def _create_running_run(
        self,
        *,
        employee_id: str,
        thread_id: str,
        metadata: dict[str, Any],
        summary: str,
    ) -> Any:
        metadata = {**metadata, "office_task_summary": summary}
        record = await self.run_manager.create(
            thread_id=thread_id,
            assistant_id=employee_id,
            metadata=metadata,
            user_id=self.user_id,
        )
        await self.run_manager.set_status(record.run_id, RunStatus.running)
        await self.event_store.put(
            thread_id=thread_id,
            run_id=record.run_id,
            event_type="llm.ai.response",
            category="message",
            content=f"测试执行结果：{summary}",
            metadata={"caller": "lead_agent"},
            user_id=self.user_id,
        )
        return await self.run_manager.get(record.run_id, user_id=self.user_id)

    async def launch(
        self,
        employee_id: str,
        model_profile_id: str,
        execution_spec: Any,
        **kwargs: Any,
    ) -> Any:
        summary = str(kwargs["office_task_summary"])
        record = await self._create_running_run(
            employee_id=employee_id,
            thread_id=kwargs["thread_id"],
            metadata=dict(kwargs.get("message_metadata") or {}),
            summary=summary,
        )
        self.calls.append(
            {
                "employee_id": employee_id,
                "model_profile_id": model_profile_id,
                "execution_spec": execution_spec,
                "thread_id": kwargs["thread_id"],
                "record": record,
            }
        )
        return record

    async def resume(
        self,
        source_record: Any,
        resume_payload: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        summary = str(resume_payload["value"])
        metadata = {
            "office_conversation_kind": source_record.metadata[
                "office_conversation_kind"
            ],
            "office_resume_source_run_id": source_record.run_id,
            "office_human_input_request_id": resume_payload["request_id"],
            "office_queue_id": kwargs["office_queue_id"],
        }
        record = await self._create_running_run(
            employee_id=source_record.assistant_id,
            thread_id=source_record.thread_id,
            metadata=metadata,
            summary=summary,
        )
        self.calls.append(
            {
                "employee_id": source_record.assistant_id,
                "resume_payload": resume_payload,
                "thread_id": source_record.thread_id,
                "record": record,
            }
        )
        return record


def _build_stateful_product_api(
    tmp_path: Path,
) -> tuple[TestClient, OfficeEmployeeService, EmployeeRunGate, _StatefulRunManager, _StatefulRunLauncher]:
    user_id = get_effective_user_id()
    paths = Paths(tmp_path / ".deer-flow")
    run_manager = _StatefulRunManager()
    event_store = MemoryRunEventStore()
    employee_service = OfficeEmployeeService(
        paths=paths,
        thread_store=MemoryThreadMetaStore(InMemoryStore()),
        checkpointer=InMemorySaver(),
        run_manager=run_manager,
        user_id=user_id,
        model_profile_exists=lambda profile_id: profile_id == MODEL_PROFILE_ID,
        default_model_profile_id=MODEL_PROFILE_ID,
        workstation_ids=tuple(f"desk-{index:02d}" for index in range(1, 9)),
    )
    asyncio.run(
        employee_service.ensure_builtin_pm(
            default_model_profile_id=MODEL_PROFILE_ID,
        )
    )
    group_store = GroupMessageStore(paths=paths, user_id=user_id)
    run_gate = EmployeeRunGate()
    launcher = _StatefulRunLauncher(
        run_manager=run_manager,
        event_store=event_store,
        user_id=user_id,
    )
    run_view = OfficeRunView(
        employee_service=employee_service,
        run_manager=run_manager,
        event_store=event_store,
        checkpointer=InMemorySaver(),
        user_id=user_id,
    )
    pipeline = OfficeMessagePipeline(
        user_id=user_id,
        employee_service=employee_service,
        group_store=group_store,
        employee_run_gate=run_gate,
        run_launcher=launcher,  # type: ignore[arg-type]
        run_view=run_view,
        project_service=None,
    )
    app = FastAPI()
    app.include_router(office.router)
    app.state.office_employee_service = employee_service
    app.state.office_group_message_store = group_store
    app.state.office_employee_run_gate = run_gate
    app.state.office_message_pipeline = pipeline
    app.state.office_run_view = run_view
    app.state.run_manager = run_manager
    return TestClient(app), employee_service, run_gate, run_manager, launcher


def _create_interrupted_source(
    run_manager: _StatefulRunManager,
    employee_service: OfficeEmployeeService,
    employee_id: str,
    *,
    kind: str = "private",
) -> Any:
    user_id = get_effective_user_id()
    thread_id = employee_service.thread_id(employee_id, kind)

    async def create() -> Any:
        record = await run_manager.create(
            thread_id=thread_id,
            assistant_id=employee_id,
            metadata={"office_conversation_kind": kind},
            user_id=user_id,
        )
        record.status = RunStatus.interrupted
        return record

    return asyncio.run(create())


def _build_product_api(tmp_path: Path) -> tuple[TestClient, _ControlledRunLauncher]:
    user_id = get_effective_user_id()
    paths = Paths(tmp_path / ".deer-flow")
    employee_service = OfficeEmployeeService(
        paths=paths,
        thread_store=MemoryThreadMetaStore(InMemoryStore()),
        checkpointer=InMemorySaver(),
        run_manager=RunManager(),
        user_id=user_id,
        model_profile_exists=lambda profile_id: profile_id == MODEL_PROFILE_ID,
        default_model_profile_id=MODEL_PROFILE_ID,
        workstation_ids=tuple(f"desk-{index:02d}" for index in range(1, 9)),
    )
    asyncio.run(
        employee_service.ensure_builtin_pm(
            default_model_profile_id=MODEL_PROFILE_ID,
        )
    )
    group_store = GroupMessageStore(paths=paths, user_id=user_id)
    run_gate = EmployeeRunGate()
    launcher = _ControlledRunLauncher()
    run_view = _RunView()
    pipeline = OfficeMessagePipeline(
        user_id=user_id,
        employee_service=employee_service,
        group_store=group_store,
        employee_run_gate=run_gate,
        run_launcher=launcher,  # type: ignore[arg-type]
        run_view=run_view,  # type: ignore[arg-type]
        project_service=None,
    )
    app = FastAPI()
    app.include_router(office.router)
    app.state.office_employee_service = employee_service
    app.state.office_group_message_store = group_store
    app.state.office_employee_run_gate = run_gate
    app.state.office_message_pipeline = pipeline
    app.state.office_run_view = run_view
    return TestClient(app), launcher


def _create_regular_employees(client: TestClient) -> None:
    for payload in EMPLOYEE_PAYLOADS:
        response = client.post("/api/office/employees", json=payload)
        assert response.status_code == 201, response.text


def test_employee_personal_info_and_historical_display_survive_rename_delete_restart(tmp_path: Path) -> None:
    client, _ = _build_product_api(tmp_path)
    payload = EMPLOYEE_PAYLOADS[1]
    assert client.post("/api/office/employees", json=payload).status_code == 201
    store = client.app.state.office_group_message_store
    store.append(
        message_id="employee-history", sender_kind="employee", sender_id="zhouli",
        sender_display_name="周礼", sender_avatar="ash", content="原来的消息",
    )
    update = {"displayName": "新周礼", "prompt": "新的个人 Prompt", "skills": []}
    assert client.put("/api/office/employees/zhouli/personal-info", json=update).status_code == 200
    info = client.get("/api/office/employees/zhouli").json()["personalInfo"]
    assert info == {**update, "prompt": update["prompt"] + "\n", "modelProfileId": MODEL_PROFILE_ID}
    for field in ("avatar", "tools", "permissions", "canDelegate"):
        assert client.put("/api/office/employees/zhouli/personal-info", json={field: "forged"}).status_code == 400
    assert client.delete("/api/office/employees/zhouli").status_code == 200
    restarted, _ = _build_product_api(tmp_path)
    message = restarted.get("/api/office/group/messages").json()["messages"][0]
    assert message["senderDisplayName"] == "周礼"
    assert message["senderAvatar"] == "ash"
    assert message["senderId"] == "zhouli"


def test_public_creation_cannot_override_office_model(tmp_path: Path) -> None:
    client, _ = _build_product_api(tmp_path)
    response = client.post("/api/office/employees", json={**EMPLOYEE_PAYLOADS[0], "modelProfileId": MODEL_PROFILE_ID})
    assert response.status_code == 400


def _message(
    message_id: str,
    text: str,
    *,
    mentions: list[str] | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    kind = "private" if target is not None else "group"
    return {
        "messageId": message_id,
        "conversationKind": kind,
        "text": text,
        "targetEmployeeId": target,
        "mentionEmployeeIds": mentions or [],
    }


def test_stage7_public_surface_uses_fixed_templates_and_has_no_legacy_run_creation(
    tmp_path: Path,
) -> None:
    client, _ = _build_product_api(tmp_path)

    templates = client.get("/api/office/employee-templates")
    assert templates.status_code == 200
    assert [item["templateId"] for item in templates.json()["templates"]] == [
        "product-manager",
        "frontend-engineer",
        "backend-engineer",
        "test-engineer",
        "research-documentation",
    ]
    _create_regular_employees(client)
    assert [item["id"] for item in client.get("/api/office/employees").json()["employees"]] == [
        "liming",
        "ousi",
        "pm",
        "sisi",
        "sunhe",
        "zhouli",
    ]

    forged = client.post(
        "/api/office/employees",
        json={**EMPLOYEE_PAYLOADS[1], "id": "forged", "tools": ["task"]},
    )
    assert forged.status_code == 400

    removed_routes = (
        ("post", "/api/office/delegation/runs"),
        ("post", "/api/office/employees/zhouli/runs"),
        ("get", "/api/office/artifacts"),
        ("get", "/api/office/runtime-events"),
    )
    for method, path in removed_routes:
        assert client.request(method, path, json={}).status_code in {404, 405}


def test_stage7_message_api_routes_private_group_multiple_and_pm_mentions(
    tmp_path: Path,
) -> None:
    client, launcher = _build_product_api(tmp_path)
    _create_regular_employees(client)

    visible = client.post(
        "/api/office/messages",
        json=_message("group-visible", "这个想法先放在群聊里"),
    )
    private = client.post(
        "/api/office/messages",
        json=_message("private-front", "检查当前前端结构", target="zhouli"),
    )
    single = client.post(
        "/api/office/messages",
        json=_message("group-back", "@孙河 检查后端接口", mentions=["sunhe"]),
    )
    multiple = client.post(
        "/api/office/messages",
        json=_message(
            "group-multiple",
            "@黎明 @思思 分别梳理需求和资料",
            mentions=["liming", "sisi"],
        ),
    )
    pm = client.post(
        "/api/office/messages",
        json=_message(
            "group-pm",
            "@PM 我想给日程管理系统增加团队共享日历功能，前端由@周礼负责",
            mentions=["pm", "zhouli"],
        ),
    )

    assert visible.json()["dispatches"] == []
    assert private.json()["triggerEmployeeIds"] == ["zhouli"]
    assert single.json()["triggerEmployeeIds"] == ["sunhe"]
    assert multiple.json()["triggerEmployeeIds"] == ["liming", "sisi"]
    assert pm.json()["triggerEmployeeIds"] == ["pm"]
    assert pm.json()["preselectedEmployeeIds"] == ["zhouli"]
    assert [call["employee_id"] for call in launcher.calls] == [
        "zhouli",
        "sunhe",
        "liming",
        "sisi",
        "pm",
    ]
    pm_call = launcher.calls[-1]
    assert pm_call["message_metadata"]["office_preselected_employee_ids"] == [
        "zhouli"
    ]
    assert pm_call["execution_spec"].input["messages"][-1]["content"].endswith(
        "前端由@周礼负责"
    )


def test_stage7_same_employee_fifo_can_be_read_edited_and_deleted_through_api(
    tmp_path: Path,
) -> None:
    client, launcher = _build_product_api(tmp_path)
    _create_regular_employees(client)

    first = client.post(
        "/api/office/messages",
        json=_message("front-1", "先检查页面", target="zhouli"),
    )
    second = client.post(
        "/api/office/messages",
        json=_message("front-2", "根据检查结果整理修改方案", target="zhouli"),
    )
    third = client.post(
        "/api/office/messages",
        json=_message("front-3", "补充移动端适配", target="zhouli"),
    )

    assert first.json()["dispatches"][0]["started"] is True
    assert second.json()["dispatches"][0]["started"] is False
    assert third.json()["dispatches"][0]["started"] is False
    assert len(launcher.calls) == 1

    pending = client.get("/api/office/queue").json()["items"]
    assert [item["text"] for item in pending] == [
        "根据检查结果整理修改方案",
        "补充移动端适配",
    ]
    second_queue_id = pending[0]["queueId"]
    third_queue_id = pending[1]["queueId"]

    edited = client.patch(
        f"/api/office/queue/{second_queue_id}",
        json={"text": "根据检查结果修复页面并完成构建"},
    )
    deleted = client.delete(f"/api/office/queue/{third_queue_id}")

    assert edited.json()["item"]["targetEmployeeId"] == "zhouli"
    assert edited.json()["item"]["text"] == "根据检查结果修复页面并完成构建"
    assert deleted.json()["item"]["queueId"] == third_queue_id
    assert [item["queueId"] for item in client.get("/api/office/queue").json()["items"]] == [
        second_queue_id
    ]


def test_stage7_public_run_history_events_cancel_and_resume(
    tmp_path: Path,
) -> None:
    client, employee_service, _gate, run_manager, launcher = _build_stateful_product_api(
        tmp_path
    )
    _create_regular_employees(client)

    sent = client.post(
        "/api/office/messages",
        json=_message("run-control", "请检查后端健康检查接口", target="sunhe"),
    )
    assert sent.status_code == 200
    run_id = sent.json()["dispatches"][0]["runId"]
    assert run_id

    detail = client.get(f"/api/office/employees/sunhe/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "running"
    assert detail.json()["taskSummary"] == "请检查后端健康检查接口"

    events = client.get(
        f"/api/office/employees/sunhe/runs/{run_id}/events",
        params={"after_seq": 0, "limit": 10},
    )
    assert events.status_code == 200
    assert [event["seq"] for event in events.json()["events"]] == [1]
    assert events.json()["nextAfterSeq"] == 1
    assert client.get(
        f"/api/office/employees/sunhe/runs/{run_id}/events",
        params={"after_seq": 1},
    ).json() == {"events": [], "nextAfterSeq": 1}

    cancelled = client.post(
        f"/api/office/employees/sunhe/runs/{run_id}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "interrupted"

    source = _create_interrupted_source(run_manager, employee_service, "zhouli")
    resumed = client.post(
        f"/api/office/employees/zhouli/runs/{source.run_id}/resume",
        json={
            "source": "ask_clarification",
            "requestId": "clarification-blackbox-1",
            "text": "使用 React 和 TypeScript",
        },
    )
    assert resumed.status_code == 200
    resumed_payload = resumed.json()
    assert resumed_payload["started"] is True
    assert resumed_payload["sourceRunId"] == source.run_id
    assert resumed_payload["runId"] != source.run_id
    assert launcher.calls[-1]["thread_id"] == source.thread_id


def test_stage7_public_boundaries_reject_unknown_mentions_and_pm_private_chat(
    tmp_path: Path,
) -> None:
    client, *_ = _build_stateful_product_api(tmp_path)
    _create_regular_employees(client)

    unknown = client.post(
        "/api/office/messages",
        json=_message(
            "unknown-mention",
            "@不存在的员工请处理这个任务",
            mentions=["missing-employee"],
        ),
    )
    assert unknown.status_code == 404
    assert "missing-employee" in unknown.json()["detail"]

    pm_private = client.get(
        "/api/office/employees/pm/conversations/private/messages"
    )
    assert pm_private.status_code == 400
    assert "private conversation" in pm_private.json()["detail"]


def test_stage7_busy_employee_keeps_fifo_while_other_employee_starts(
    tmp_path: Path,
) -> None:
    client, _employee_service, _gate, _run_manager, launcher = _build_stateful_product_api(
        tmp_path
    )
    _create_regular_employees(client)

    first = client.post(
        "/api/office/messages",
        json=_message("fifo-front-1", "先检查前端", target="zhouli"),
    )
    queued = client.post(
        "/api/office/messages",
        json=_message("fifo-front-2", "再整理修复方案", target="zhouli"),
    )
    parallel = client.post(
        "/api/office/messages",
        json=_message("parallel-back", "检查后端健康接口", target="sunhe"),
    )

    assert first.json()["dispatches"][0]["started"] is True
    assert queued.json()["dispatches"][0]["started"] is False
    assert parallel.json()["dispatches"][0]["started"] is True
    assert [call["employee_id"] for call in launcher.calls] == ["zhouli", "sunhe"]

    pending = client.get("/api/office/queue")
    assert pending.status_code == 200
    assert [item["targetEmployeeId"] for item in pending.json()["items"]] == [
        "zhouli"
    ]
    assert [item["text"] for item in pending.json()["items"]] == ["再整理修复方案"]


def test_stage7_refresh_can_read_employee_capabilities_and_run_history(
    tmp_path: Path,
) -> None:
    client, *_ = _build_stateful_product_api(tmp_path)
    _create_regular_employees(client)

    employee = client.get("/api/office/employees/sunhe")
    capabilities = client.get("/api/office/employees/sunhe/capabilities")
    assert employee.status_code == 200
    assert capabilities.status_code == 200
    assert employee.json()["employee"]["id"] == "sunhe"
    assert capabilities.json()["capabilityIndex"]["employeeId"] == "sunhe"

    sent = client.post(
        "/api/office/messages",
        json=_message("history-refresh", "读取当前后端结构", target="sunhe"),
    )
    run_id = sent.json()["dispatches"][0]["runId"]
    history = client.get(
        "/api/office/employees/sunhe/runs",
        params={"conversationKind": "private", "limit": 10},
    )
    assert history.status_code == 200
    assert [run["runId"] for run in history.json()["runs"]] == [run_id]


def test_stage7_forbidden_office_runtime_symbols_are_absent_from_production() -> None:
    backend_root = Path(__file__).parent.parent
    production_roots = (
        backend_root / "app" / "gateway",
        backend_root / "packages" / "harness" / "deerflow" / "office",
    )
    forbidden = (
        "OfficeRunService",
        "deerflow.office.runs",
        "default_thread_id",
        "capability_profile",
        "input_fingerprint",
    )

    matches: list[str] = []
    for root in production_roots:
        for source_path in root.rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            for symbol in forbidden:
                if symbol in source:
                    matches.append(f"{source_path.relative_to(backend_root)}:{symbol}")

    assert matches == []
