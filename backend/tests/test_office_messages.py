# 本文件属于 Office 消息命令与 HTTP 映射测试模块：只验证 Mention 路由、输入校验和窄管道调用。
# 不负责员工 FIFO 内部实现、Run 生命周期、历史存储、状态投影或 Agent Loop 测试。
# 关联：office/messages.py 提供确定性路由，Gateway Office Router 把请求交给 message_pipeline.py。
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import office
from deerflow.config.paths import Paths
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.messages import OfficeMessageCommand, resolve_message_command
from deerflow.office.run_gate import QueuedOfficeRun
from deerflow.runtime.user_context import get_effective_user_id

EMPLOYEES = [
    {"id": "pm", "displayName": "项目经理", "canDelegate": True},
    {"id": "frontend-a", "displayName": "前端甲", "canDelegate": False},
    {"id": "backend-b", "displayName": "后端乙", "canDelegate": False},
]


class FakeEmployeeService:
    async def list_employees(self):
        return list(EMPLOYEES)


def test_private_message_requires_one_regular_employee_and_no_mentions() -> None:
    resolved = resolve_message_command(
        OfficeMessageCommand("private", "check the page", "frontend-a", ()),
        EMPLOYEES,
    )

    assert resolved.trigger_employee_ids == ("frontend-a",)
    assert resolved.preselected_employee_ids == ()
    with pytest.raises(ValueError, match="target_employee_id is required"):
        resolve_message_command(OfficeMessageCommand("private", "hello", None, ()), EMPLOYEES)
    with pytest.raises(ValueError, match="does not accept mentions"):
        resolve_message_command(
            OfficeMessageCommand("private", "hello", "frontend-a", ("backend-b",)),
            EMPLOYEES,
        )
    with pytest.raises(ValueError, match="PM does not have a private conversation"):
        resolve_message_command(OfficeMessageCommand("private", "hello", "pm", ()), EMPLOYEES)


def test_group_mentions_use_stable_ids_and_pm_is_the_only_trigger() -> None:
    resolved = resolve_message_command(
        OfficeMessageCommand(
            "group",
            "build a calendar",
            None,
            ("frontend-a", "pm", "backend-b", "frontend-a"),
        ),
        EMPLOYEES,
    )

    assert resolved.mention_employee_ids == ("frontend-a", "pm", "backend-b")
    assert resolved.trigger_employee_ids == ("pm",)
    assert resolved.preselected_employee_ids == ("frontend-a", "backend-b")
    with pytest.raises(FileNotFoundError, match="Employee not found"):
        resolve_message_command(
            OfficeMessageCommand("group", "hello", None, ("前端甲",)),
            EMPLOYEES,
        )
    with pytest.raises(ValueError, match="does not accept target_employee_id"):
        resolve_message_command(
            OfficeMessageCommand("group", "hello", "frontend-a", ()),
            EMPLOYEES,
        )


class ApiRunService:
    def __init__(self, store: GroupMessageStore, user_id: str) -> None:
        self.user_id = user_id
        self.store = store
        self.dispatches = []
        self.history_requests = []
        self.resume_requests = []

    async def send(self, message_id, command, **_kwargs):
        if not isinstance(message_id, str):
            raise ValueError("message_id must be a string")
        resolved = resolve_message_command(command, EMPLOYEES)
        if not resolved.trigger_employee_ids:
            return {
                "messageId": message_id,
                "conversationKind": resolved.conversation_kind,
                "triggerEmployeeIds": [],
                "preselectedEmployeeIds": [],
                "dispatches": [],
            }
        employee_id = resolved.trigger_employee_ids[0]
        self.dispatches.append(
            SimpleNamespace(idempotency_key=(message_id, employee_id))
        )
        self.store.append(
            message_id=message_id,
            sender_kind="user",
            sender_id=self.user_id,
            content=resolved.text,
            mention_employee_ids=resolved.mention_employee_ids,
        )
        return {
            "messageId": message_id,
            "conversationKind": resolved.conversation_kind,
            "triggerEmployeeIds": list(resolved.trigger_employee_ids),
            "preselectedEmployeeIds": list(resolved.preselected_employee_ids),
            "dispatches": [
                {
                    "employeeId": employee_id,
                    "queueId": f"queue-{employee_id}",
                    "started": True,
                    "runId": f"run-{employee_id}",
                }
            ],
        }

    async def list_conversation_messages(self, employee_id, kind, *, limit):
        self.history_requests.append((employee_id, kind, limit))
        return {
            "messages": [
                {"messageId": "input-1", "runId": "run-1", "role": "user", "content": "question", "createdAt": "1"},
                {"messageId": "run:run-1:final", "runId": "run-1", "role": "assistant", "content": "answer", "createdAt": "2"},
            ]
        }

    async def resume(self, employee_id, run_id, *, source, request_id, text, **_kwargs):
        self.resume_requests.append((employee_id, run_id, source, request_id, text))
        return {
            "employeeId": employee_id,
            "sourceRunId": run_id,
            "queueId": f"resume:{run_id}:{request_id}",
            "started": True,
            "runId": "run-resumed",
        }

class ApiQueueGate:
    def __init__(self) -> None:
        item = QueuedOfficeRun(
            "queue-1",
            "frontend-a",
            "direct",
            "queued",
            1,
            conversation_kind="private",
        )
        self.items = {item.queue_id: item}

    async def list_pending(self):
        return list(self.items.values())

    async def edit(self, queue_id, text):
        item = self.items.get(queue_id)
        if item is None:
            raise FileNotFoundError(f"Queued run not found: {queue_id}")
        edited = replace(item, text=text)
        self.items[queue_id] = edited
        return edited

    async def delete(self, queue_id):
        item = self.items.pop(queue_id, None)
        if item is None:
            raise FileNotFoundError(f"Queued run not found: {queue_id}")
        return item


def build_office_message_api(tmp_path):
    user_id = get_effective_user_id()
    store = GroupMessageStore(paths=Paths(tmp_path / ".deer-flow"), user_id=user_id)
    store.append(
        message_id="visible-1",
        sender_kind="user",
        sender_id=user_id,
        content="visible group text",
        created_at=1,
    )
    run_service = ApiRunService(store, user_id)
    queue_gate = ApiQueueGate()
    app = FastAPI()
    app.include_router(office.router)
    app.dependency_overrides[office.get_office_message_pipeline] = lambda: run_service
    app.dependency_overrides[office.get_office_run_view] = lambda: run_service
    app.state.office_group_message_store = store
    app.state.office_employee_run_gate = queue_gate
    return TestClient(app), store, run_service, queue_gate


def test_office_message_api_maps_message_history_and_queue(tmp_path) -> None:
    client, store, run_service, queue_gate = build_office_message_api(tmp_path)

    sent = client.post(
        "/api/office/messages",
        json={
            "messageId": "message-api",
            "conversationKind": "group",
            "text": "build the page",
            "mentionEmployeeIds": ["frontend-a"],
        },
    )
    group = client.get("/api/office/group/messages?afterSequence=0&limit=10")
    history = client.get("/api/office/employees/frontend-a/conversations/private/messages?limit=20")
    queued = client.get("/api/office/queue")
    edited = client.patch("/api/office/queue/queue-1", json={"text": "edited text"})
    deleted = client.delete("/api/office/queue/queue-1")

    assert sent.status_code == 200
    assert sent.json()["dispatches"][0]["runId"] == "run-frontend-a"
    assert run_service.dispatches[0].idempotency_key == ("message-api", "frontend-a")
    assert [message.message_id for message in store.list()] == ["visible-1", "message-api"]
    assert [message["messageId"] for message in group.json()["messages"]] == ["visible-1", "message-api"]
    assert group.json()["nextAfterSequence"] == 2
    assert [message["role"] for message in history.json()["messages"]] == ["user", "assistant"]
    assert run_service.history_requests == [("frontend-a", "private", 20)]
    assert queued.json()["items"][0]["targetEmployeeId"] == "frontend-a"
    assert queued.json()["items"][0]["conversationKind"] == "private"
    assert edited.json()["item"]["text"] == "edited text"
    assert deleted.json()["item"]["queueId"] == "queue-1"
    assert queue_gate.items == {}


def test_office_message_api_rejects_invalid_commands_and_queue_mutations(tmp_path) -> None:
    client, _, _, _ = build_office_message_api(tmp_path)

    invalid_mentions = client.post(
        "/api/office/messages",
        json={
            "messageId": "message-invalid",
            "conversationKind": "group",
            "text": "hello",
            "mentionEmployeeIds": "frontend-a",
        },
    )
    missing_private_target = client.post(
        "/api/office/messages",
        json={
            "messageId": "message-private",
            "conversationKind": "private",
            "text": "hello",
            "mentionEmployeeIds": [],
        },
    )
    invalid_kind = client.get("/api/office/employees/frontend-a/conversations/unknown/messages")
    mention_edit = client.patch(
        "/api/office/queue/queue-1",
        json={"text": "edited", "mentionEmployeeIds": ["backend-b"]},
    )
    numeric_message_id = client.post(
        "/api/office/messages",
        json={
            "messageId": 1,
            "conversationKind": "group",
            "text": "hello",
            "mentionEmployeeIds": [],
        },
    )
    numeric_text = client.post(
        "/api/office/messages",
        json={
            "messageId": "message-numeric-text",
            "conversationKind": "group",
            "text": 1,
            "mentionEmployeeIds": [],
        },
    )
    numeric_private_target = client.post(
        "/api/office/messages",
        json={
            "messageId": "message-numeric-target",
            "conversationKind": "private",
            "text": "hello",
            "targetEmployeeId": 1,
            "mentionEmployeeIds": [],
        },
    )

    assert invalid_mentions.status_code == 400
    assert missing_private_target.status_code == 400
    assert invalid_kind.status_code == 400
    assert mention_edit.status_code == 400
    assert numeric_message_id.status_code == 400
    assert numeric_text.status_code == 400
    assert numeric_private_target.status_code == 400


def test_group_message_api_rejects_cross_user_store(monkeypatch, tmp_path) -> None:
    client, _, _, _ = build_office_message_api(tmp_path)
    monkeypatch.setattr(office, "get_effective_user_id", lambda: "another-user")

    response = client.get("/api/office/group/messages")

    assert response.status_code == 403


def test_legacy_office_command_routes_are_physically_removed(tmp_path) -> None:
    client, _, _, _ = build_office_message_api(tmp_path)

    responses = [
        client.patch("/api/office/employees/frontend-a", json={"state": "idle"}),
        client.post("/api/office/delegation/runs", json={"message": "legacy"}),
        client.post("/api/office/employees/frontend-a/routing/resolve", json={}),
        client.post("/api/office/employees/frontend-a/runs", json={"message": "legacy"}),
        client.post("/api/office/employees/frontend-a/runs/run-1/branch", json={}),
        client.get("/api/office/message-dispatches/queue-frontend-a"),
        client.get("/api/office/artifacts"),
        client.get("/api/office/runtime-events"),
    ]

    assert all(response.status_code in {404, 405} for response in responses)


def test_office_resume_maps_plain_user_answer_to_the_existing_interrupted_run(tmp_path) -> None:
    client, _, run_service, _ = build_office_message_api(tmp_path)

    response = client.post(
        "/api/office/employees/frontend-a/runs/run-interrupted/resume",
        json={
            "source": "ask_clarification",
            "requestId": "clarification:call-one",
            "text": "D:/workspace",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "employeeId": "frontend-a",
        "sourceRunId": "run-interrupted",
        "queueId": "resume:run-interrupted:clarification:call-one",
        "started": True,
        "runId": "run-resumed",
    }
    assert run_service.resume_requests == [
        (
            "frontend-a",
            "run-interrupted",
            "ask_clarification",
            "clarification:call-one",
            "D:/workspace",
        )
    ]
