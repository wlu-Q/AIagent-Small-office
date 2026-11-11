# 本文件属于 Office Run 查询投影测试模块：只验证员工归属与 DeerFlow Run、Event、Thread 历史投影。
# 不负责 Run 启动、消息投递、员工排队、状态投影或 Checkpointer 内部状态测试。
# 关联：run_view.py 是被测模块，RunManager 与 RunEventStore 提供唯一执行事实。
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.office.run_view import OfficeRunView
from deerflow.runtime.runs.execution import RunExecutionSpecV1, RunLaunchContractV1
from deerflow.runtime.runs.schemas import RunStatus


class EmployeeServiceStub:
    @staticmethod
    def validate_employee_id(employee_id: str) -> str:
        if employee_id not in {"employee-one", "employee-two"}:
            raise ValueError("unknown employee")
        return employee_id

    @staticmethod
    def get_employee_summary(employee_id: str) -> dict[str, object]:
        return {"id": employee_id, "canDelegate": False}

    @staticmethod
    def thread_id(employee_id: str, kind: str) -> str:
        return f"office__default__{employee_id}__{kind}"


class RunManagerStub:
    def __init__(self, records: list[SimpleNamespace]) -> None:
        self.records = records

    async def get(self, run_id: str, *, user_id: str):
        return next(
            (
                record
                for record in self.records
                if record.run_id == run_id and record.user_id == user_id
            ),
            None,
        )

    async def list_by_thread(self, thread_id: str, *, user_id: str, limit: int):
        return [
            record
            for record in self.records[:limit]
            if record.thread_id == thread_id and record.user_id == user_id
        ]

    async def find_by_thread_metadata(self, thread_id, metadata, *, user_id):
        return next(
            (
                record
                for record in self.records
                if record.thread_id == thread_id
                and record.user_id == user_id
                and all(record.metadata.get(key) == value for key, value in metadata.items())
            ),
            None,
        )


class EventStoreStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, int | None]] = []

    async def list_events(
        self,
        thread_id: str,
        run_id: str,
        *,
        limit: int,
        after_seq: int | None,
    ) -> list[dict[str, object]]:
        self.calls.append((thread_id, run_id, limit, after_seq))
        return [{"seq": 4, "type": "message"}]


class CheckpointerStub:
    def __init__(self, messages=None) -> None:
        self.messages = list(messages or [])
        self.calls: list[dict[str, object]] = []

    async def aget_tuple(self, config):
        self.calls.append(config)
        return SimpleNamespace(
            checkpoint={"channel_values": {"messages": self.messages}}
        )


def _record(
    *,
    run_id: str = "run-one",
    employee_id: str = "employee-one",
    conversation_kind: str = "private",
    user_id: str = "default",
    last_ai_message: str | None = "answer",
):
    return SimpleNamespace(
        run_id=run_id,
        user_id=user_id,
        assistant_id=employee_id,
        thread_id=f"office__default__{employee_id}__{conversation_kind}",
        status=RunStatus.success,
        created_at=10,
        updated_at=20,
        error=None,
        model_name="model-one",
        llm_call_count=1,
        message_count=2,
        last_ai_message=last_ai_message,
        events_finalized=True,
        finalizing=False,
        completion_pending=False,
        last_event_seq=4,
        total_input_tokens=11,
        total_output_tokens=7,
        total_tokens=18,
        kwargs={},
        metadata={
            "office_employee_id": employee_id,
            "office_conversation_kind": conversation_kind,
            "office_message_id": "message-one",
            "office_task_id": "task-one",
            "office_task_summary": "question",
        },
    )


def _view(record=None, *, messages=None):
    records = [record or _record()]
    event_store = EventStoreStub()
    checkpointer = CheckpointerStub(messages)
    return (
        OfficeRunView(
            employee_service=EmployeeServiceStub(),
            run_manager=RunManagerStub(records),
            event_store=event_store,
            checkpointer=checkpointer,
            user_id="default",
        ),
        event_store,
        checkpointer,
    )


@pytest.mark.anyio
async def test_history_links_native_user_ids_to_distinct_office_sources():
    first, second = _record(run_id="first"), _record(run_id="second")
    first.metadata["office_message_id"] = "send-one"
    second.metadata["office_message_id"] = "send-two"
    view, _, checkpoint = _view(first, messages=[
        HumanMessage(id="native-one__user", content="相同的内容", additional_kwargs={"run_id": "first"}),
        {"type": "human", "id": "native-two", "content": "相同的内容", "additional_kwargs": {"run_id": "second"}},
        AIMessage(id="answer", content="回复", additional_kwargs={"run_id": "second"}),
    ])
    view.run_manager.records.append(second)
    messages = (await view.list_conversation_messages("employee-one", "private"))["messages"]
    assert [m["messageId"] for m in messages] == ["native-one__user", "native-two", "answer"]
    assert [m.get("sourceMessageId") for m in messages] == ["send-one", "send-two", None]
    assert checkpoint.messages[0].id == "native-one__user"


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["user", "employee", "thread", "missing", "no-source"])
async def test_history_source_identity_never_crosses_boundaries_or_guesses(boundary):
    record = _record()
    if boundary == "user": record.user_id = "other-user"
    if boundary == "employee": record.assistant_id = "employee-two"
    if boundary == "thread": record.thread_id = "office__default__employee-one__group"
    if boundary == "no-source": record.metadata.pop("office_message_id")
    view, _, _ = _view(record, messages=[
        HumanMessage(id="native__user", content="question", additional_kwargs={"run_id": "run-one"}),
    ])
    if boundary == "missing": view.run_manager.records = []
    messages = (await view.list_conversation_messages("employee-one", "private"))["messages"]
    assert messages[0]["messageId"] == "native__user"
    assert "sourceMessageId" not in messages[0]


@pytest.mark.anyio
async def test_run_view_projects_deerflow_run_without_checkpoint_fields() -> None:
    view, _, _ = _view()

    result = await view.get_run("employee-one", "run-one")

    assert result["runId"] == "run-one"
    assert result["employeeId"] == "employee-one"
    assert result["taskId"] == "task-one"
    assert result["taskSummary"] == "question"
    assert result["lastAiMessage"] == "answer"
    assert "latestCheckpointId" not in result
    assert "checkpointId" not in result


@pytest.mark.anyio
@pytest.mark.parametrize("pending_field", ["finalizing", "completion_pending"])
async def test_run_view_keeps_completion_hook_visible_after_events_are_sealed(pending_field) -> None:
    record = _record()
    setattr(record, pending_field, True)
    view, _, _ = _view(record)
    result = await view.get_run("employee-one", "run-one")
    assert result["eventsFinalized"] is True
    assert result["finalizing"] is True
    setattr(record, pending_field, False)
    assert (await view.get_run("employee-one", "run-one"))["finalizing"] is False


@pytest.mark.anyio
async def test_run_view_rejects_cross_employee_run_access() -> None:
    view, _, _ = _view()

    with pytest.raises(FileNotFoundError, match="Run not found"):
        await view.get_run("employee-two", "run-one")


@pytest.mark.anyio
async def test_run_view_resolves_the_owned_run_for_cancel_command() -> None:
    view, _, _ = _view()

    employee_id, record = await view.resolve_run("employee-one", "run-one")

    assert employee_id == "employee-one"
    assert record.run_id == "run-one"


@pytest.mark.anyio
async def test_run_view_reads_events_from_deerflow_event_store() -> None:
    view, event_store, _ = _view()

    result = await view.list_events("employee-one", "run-one", after_seq=3, limit=10)

    assert result == {
        "events": [{"seq": 4, "type": "message"}],
        "nextAfterSeq": 4,
    }
    assert event_store.calls == [
        ("office__default__employee-one__private", "run-one", 10, 3)
    ]


@pytest.mark.anyio
async def test_run_view_lists_completed_runs_from_the_owned_conversation_thread() -> None:
    records = [
        _record(run_id="run-private", conversation_kind="private"),
        _record(run_id="run-group", conversation_kind="group"),
        _record(
            run_id="run-other-employee",
            employee_id="employee-two",
            conversation_kind="private",
        ),
    ]
    view = OfficeRunView(
        employee_service=EmployeeServiceStub(),
        run_manager=RunManagerStub(records),
        event_store=EventStoreStub(),
        checkpointer=CheckpointerStub(),
        user_id="default",
    )

    result = await view.list_runs("employee-one", "private", limit=10)

    assert [run["runId"] for run in result["runs"]] == ["run-private"]
    assert result["runs"][0]["status"] == "success"
    assert result["runs"][0]["eventsFinalized"] is True


@pytest.mark.anyio
async def test_run_view_projects_only_user_visible_messages_from_deerflow_checkpoint() -> None:
    long_answer = "a" * 2501
    view, _, checkpointer = _view(
        messages=[
            HumanMessage(
                id="human-one",
                content="wrapped prompt",
                additional_kwargs={
                    "original_user_content": "question",
                    "run_id": "run-one",
                    "timestamp": "2026-09-01T08:00:00Z",
                },
            ),
            HumanMessage(
                id="hidden-human",
                content="internal context",
                additional_kwargs={"hide_from_ui": True},
            ),
            AIMessage(id="ai-one", content=long_answer),
            ToolMessage(
                id="tool-one",
                content="tool output",
                tool_call_id="call-tool",
            ),
            ToolMessage(
                id="human-answer",
                content="D:/workspace",
                tool_call_id="call-clarify",
                additional_kwargs={
                    "hide_from_ui": True,
                    "human_input_response": {
                        "version": 1,
                        "kind": "human_input_response",
                        "source": "ask_clarification",
                        "request_id": "clarification:call-clarify",
                        "response_kind": "text",
                        "value": "D:/workspace",
                    },
                },
            ),
        ]
    )

    result = await view.list_conversation_messages(
        "employee-one",
        "private",
        limit=20,
    )

    assert result == {
        "messages": [
            {
                "messageId": "human-one",
                "sourceMessageId": "message-one",
                "runId": "run-one",
                "role": "user",
                "content": "question",
                "createdAt": "2026-09-01T08:00:00Z",
            },
            {
                "messageId": "ai-one",
                "runId": None,
                "role": "assistant",
                "content": long_answer,
                "createdAt": None,
            },
            {
                "messageId": "human-answer",
                "runId": None,
                "role": "user",
                "content": "D:/workspace",
                "createdAt": None,
            },
        ]
    }
    assert checkpointer.calls == [
        {
            "configurable": {
                "thread_id": "office__default__employee-one__private",
                "checkpoint_ns": "",
            }
        }
    ]


@pytest.mark.anyio
async def test_run_view_finds_an_existing_message_run_inside_employee_boundary() -> None:
    view, _, _ = _view()

    record = await view.find_message_run(
        "employee-one",
        "private",
        "message-one",
    )
    missing = await view.find_message_run(
        "employee-two",
        "private",
        "message-one",
    )

    assert record is not None and record.run_id == "run-one"
    assert missing is None


def test_run_view_model_binding_fails_closed_for_invalid_launch_contract() -> None:
    invalid_record = SimpleNamespace(
        kwargs={
            "launch_contract": {
                "version": 1,
                "model_profile_id": "profile-primary",
                "context": {
                    "runtime_model_id": "mimo-v2.5",
                    "apiKey": "must-not-project",
                },
            }
        }
    )

    assert OfficeRunView.model_binding(invalid_record) == (None, None)


def test_run_view_model_binding_keeps_profile_without_inventing_runtime_model() -> None:
    contract = RunLaunchContractV1.build(
        model_profile_id="profile-primary",
        execution_spec=RunExecutionSpecV1(
            version=1,
            mode="new",
            input={"messages": [{"role": "user", "content": "生成说明"}]},
        ),
        config={},
        context={"model_name": "office-selected-model"},
        stream_modes=["values"],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    )
    record = SimpleNamespace(kwargs={"launch_contract": contract.to_dict()})

    assert OfficeRunView.model_binding(record) == ("profile-primary", None)


@pytest.mark.anyio
async def test_run_view_requires_an_explicit_supported_conversation_kind() -> None:
    view, _, _ = _view()

    with pytest.raises(ValueError, match="Unsupported conversation kind"):
        await view.list_conversation_messages("employee-one", "unknown")


@pytest.mark.anyio
async def test_private_history_filters_nonpublic_blocks_and_metadata():
    view, _, _ = _view(messages=[
        AIMessage(id='mixed', content=[{'type': 'reasoning', 'text': 'PRIVATE'}, {'type': 'text', 'text': '公开正文'}]),
        AIMessage(id='hidden', content='HIDDEN', additional_kwargs={'hidden': True}),
        {'role': 'assistant', 'id': 'dict', 'content': [{'type': 'thinking', 'text': 'SECRET'}, {'type': 'text', 'text': '可见'}]},
    ])
    messages = (await view.list_conversation_messages('employee-one', 'private'))['messages']
    assert [message['content'] for message in messages] == ['公开正文', '可见']


@pytest.mark.anyio
async def test_idless_checkpoint_window_changes_do_not_reuse_another_message_identity():
    view, _, checkpoint = _view(messages=[{'role': 'assistant', 'content': '旧回复'}, {'role': 'assistant', 'content': '新回复'}])
    before = (await view.list_conversation_messages('employee-one', 'private'))['messages']
    checkpoint.messages = [{'role': 'assistant', 'content': '新回复'}]
    after = (await view.list_conversation_messages('employee-one', 'private'))['messages']
    assert after[0]['messageId'] == before[1]['messageId']
    assert after[0]['messageId'] != before[0]['messageId']
