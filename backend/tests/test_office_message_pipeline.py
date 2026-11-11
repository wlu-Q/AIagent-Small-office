# 本文件属于 Office 消息执行管道测试模块：只验证消息投递、single-flight、员工门禁和排队启动。
# 不负责 Mention 解析细节、Run 历史、状态投影或 DeerFlow Agent Loop 内部行为测试。
# 关联：message_pipeline.py 是被测模块，messages.py、run_gate.py 与 run_launch.py 提供管道边界。
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Interrupt

from deerflow.config.paths import Paths
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.message_pipeline import MessageDispatchRequest, OfficeMessagePipeline
from deerflow.office.messages import OfficeMessageCommand
from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun
from deerflow.office.run_launch import OfficeRunLauncher
from deerflow.runtime.runs.schemas import RunStatus

EMPLOYEES = [
    {
        "id": "pm",
        "displayName": "项目经理",
        "avatar": "ash",
        "enabled": True,
        "modelProfileId": "profile-primary",
        "canDelegate": True,
    },
    {
        "id": "frontend-a",
        "displayName": "前端员工",
        "avatar": "ash",
        "enabled": True,
        "modelProfileId": "profile-primary",
        "canDelegate": False,
    },
    {
        "id": "backend-b",
        "displayName": "后端员工",
        "avatar": "ash",
        "enabled": True,
        "modelProfileId": "profile-primary",
        "canDelegate": False,
    },
]


class EmployeeServiceStub:
    user_id = "user-a"

    async def list_employees(self):
        return list(EMPLOYEES)

    @staticmethod
    def validate_employee_id(employee_id: str) -> str:
        if employee_id not in {"pm", "frontend-a", "backend-b"}:
            raise ValueError("unknown employee")
        return employee_id

    @staticmethod
    def get_employee_summary(employee_id: str):
        return next(employee for employee in EMPLOYEES if employee["id"] == employee_id)

    @staticmethod
    def thread_id(employee_id: str, kind: str) -> str:
        return f"office__user-a__{employee_id}__{kind}"


class RunManagerStub:
    def __init__(self, record=None) -> None:
        self.record = record

    async def find_by_thread_metadata(self, *_args, **_kwargs):
        return self.record


class RunLauncherStub:
    def __init__(self) -> None:
        self.employee_ids: list[str] = []
        self.calls: list[dict[str, object]] = []
        self.resume_calls: list[dict[str, object]] = []
        self.launched = asyncio.Event()
        self.resume_launched = asyncio.Event()

    async def launch(self, employee_id, model_profile_id, execution_spec, **kwargs):
        self.employee_ids.append(employee_id)
        self.calls.append(
            {
                "employee_id": employee_id,
                "model_profile_id": model_profile_id,
                "execution_spec": execution_spec,
                **kwargs,
            }
        )
        self.launched.set()
        return SimpleNamespace(run_id=f"run-{employee_id}")

    async def resume(self, source_record, resume_payload, **kwargs):
        self.resume_calls.append(
            {
                "source_record": source_record,
                "resume_payload": resume_payload,
                **kwargs,
            }
        )
        self.resume_launched.set()
        return SimpleNamespace(run_id=f"run-resumed-{source_record.assistant_id}")


class BlockingRunLauncherStub(RunLauncherStub):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def launch(self, employee_id, model_profile_id, execution_spec, **kwargs):
        self.employee_ids.append(employee_id)
        self.calls.append(
            {
                "employee_id": employee_id,
                "model_profile_id": model_profile_id,
                "execution_spec": execution_spec,
                **kwargs,
            }
        )
        self.launched.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return SimpleNamespace(run_id=f"run-{employee_id}")


class RacingEmployeeRunGate(EmployeeRunGate):
    async def get_admission(self, queue_id: str):
        admission = await super().get_admission(queue_id)
        if queue_id.startswith("resume:") and admission is None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        return admission


class FailOnceRunLauncherStub(RunLauncherStub):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def launch(self, employee_id, _model_profile_id, _execution_spec, **kwargs):
        self.calls += 1
        self.employee_ids.append(employee_id)
        if self.calls == 1:
            raise RuntimeError("launch failed")
        return SimpleNamespace(run_id=f"run-{employee_id}")


class RunViewStub:
    def __init__(self, record=None) -> None:
        self.record = record
        self.run_manager = SimpleNamespace(list_by_thread=AsyncMock(return_value=[]))

    async def find_message_run(self, *_args):
        return self.record

    async def resolve_run(self, employee_id, run_id):
        if self.record is None or self.record.run_id != run_id:
            raise FileNotFoundError("Run not found")
        return employee_id, self.record

    async def find_resume_run(self, *_args):
        return None

    @staticmethod
    def serialize_run(employee_id, record):
        return {"runId": record.run_id, "employeeId": employee_id}

    @staticmethod
    def task_summary(record):
        return record.metadata.get("office_task_summary")


class ProjectServiceStub:
    def __init__(self, binding=None) -> None:
        self.binding = binding

    def get_binding(self):
        return self.binding


def _pipeline(
    tmp_path: Path,
    gate: EmployeeRunGate | None = None,
    launcher: RunLauncherStub | None = None,
    run_manager: RunManagerStub | None = None,
    project_service=None,
    employee_service=None,
):
    selected_launcher = launcher or RunLauncherStub()
    store = GroupMessageStore(paths=Paths(tmp_path / ".deer-flow"), user_id="user-a")
    pipeline = OfficeMessagePipeline(
        user_id="user-a",
        employee_service=employee_service or EmployeeServiceStub(),
        group_store=store,
        employee_run_gate=gate or EmployeeRunGate(),
        run_launcher=selected_launcher,
        run_view=RunViewStub((run_manager or RunManagerStub()).record),
        project_service=project_service,
    )
    record = pipeline.run_view.record
    selected_launcher.checkpointer = SimpleNamespace(aget_tuple=AsyncMock(return_value=None))
    if record is not None and getattr(record, 'status', None) == RunStatus.interrupted:
        record.user_id = 'user-a'
        record.thread_id = pipeline.employee_service.thread_id(record.assistant_id, record.metadata['office_conversation_kind'])
        request_id = {'run-group-interrupted-sequence': 'clarification:group-sequence',
                      'run-group-resume-no-reread': 'clarification:no-reread'}.get(record.run_id)
        request_id = request_id or ('clarification:group-one' if record.metadata['office_conversation_kind'] == 'group' else 'clarification:call-one')
        selected_launcher.checkpointer.aget_tuple.return_value = SimpleNamespace(metadata={'deerflow_run_id': record.run_id},
            pending_writes=[('task', '__interrupt__', (Interrupt(value={'kind': 'human_input_request',
                'source': 'ask_clarification', 'request_id': request_id}),))])
    return pipeline, selected_launcher, store


@pytest.mark.anyio
async def test_group_without_mentions_only_saves_visible_text(tmp_path: Path) -> None:
    pipeline, launcher, store = _pipeline(tmp_path)

    result = await pipeline.send(
        "message-visible",
        OfficeMessageCommand("group", "大家看一下", None, ()),
        start_run=object(),
        request=object(),
    )

    assert result["dispatches"] == []
    assert launcher.employee_ids == []
    assert [message.content for message in store.list()] == ["大家看一下"]


@pytest.mark.anyio
async def test_different_idle_employees_launch_independently(tmp_path: Path) -> None:
    pipeline, launcher, store = _pipeline(tmp_path)

    result = await pipeline.send(
        "message-parallel",
        OfficeMessageCommand(
            "group",
            "前后端分别实现",
            None,
            ("frontend-a", "backend-b"),
        ),
        start_run=object(),
        request=object(),
    )

    assert [dispatch["started"] for dispatch in result["dispatches"]] == [True, True]
    assert launcher.employee_ids == ["frontend-a", "backend-b"]
    assert [message.message_id for message in store.list()] == ["message-parallel"]


@pytest.mark.anyio
async def test_idle_employee_acceptance_returns_real_run_id(tmp_path: Path) -> None:
    pipeline, _launcher, _store = _pipeline(tmp_path)

    result = await pipeline.send(
        "message-run-id",
        OfficeMessageCommand("private", "读取项目结构", "frontend-a", ()),
        start_run=object(),
        request=object(),
    )

    assert result["dispatches"][0]["started"] is True
    assert result["dispatches"][0]["runId"] == "run-frontend-a"


@pytest.mark.anyio
async def test_busy_employee_starts_after_its_own_fifo_releases(tmp_path: Path) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker", "backend-b", "direct", "busy", 1, conversation_kind="group"
        )
    )
    pipeline, launcher, _ = _pipeline(tmp_path, gate)

    result = await pipeline.send(
        "message-queued",
        OfficeMessageCommand("group", "实现后端", None, ("backend-b",)),
        start_run=object(),
        request=object(),
    )

    assert result["dispatches"][0]["started"] is False
    assert launcher.employee_ids == []

    await gate.release(blocker.lease_id or "")
    await asyncio.wait_for(launcher.launched.wait(), timeout=2)

    assert launcher.employee_ids == ["backend-b"]


@pytest.mark.anyio
async def test_private_and_group_messages_use_distinct_threads_and_current_text(
    tmp_path: Path,
) -> None:
    gate = EmployeeRunGate()
    pipeline, launcher, _ = _pipeline(tmp_path, gate=gate)

    private = await pipeline.send(
        "message-private",
        OfficeMessageCommand("private", "private text", "frontend-a", ()),
        start_run=object(),
        request=object(),
    )
    private_admission = await gate.get_admission(private["dispatches"][0]["queueId"])
    await gate.release(private_admission.lease_id or "")
    await pipeline.send(
        "message-group",
        OfficeMessageCommand("group", "group text", None, ("frontend-a",)),
        start_run=object(),
        request=object(),
    )

    assert [call["thread_id"] for call in launcher.calls] == [
        "office__user-a__frontend-a__private",
        "office__user-a__frontend-a__group",
    ]
    assert [
        call["execution_spec"].input["messages"][0]["content"]
        for call in launcher.calls
    ] == ["private text", "group text"]


@pytest.mark.anyio
async def test_queued_message_launches_with_edited_text(tmp_path: Path) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker", "backend-b", "direct", "busy", 1, conversation_kind="group"
        )
    )
    pipeline, launcher, _ = _pipeline(tmp_path, gate=gate)
    queued = await pipeline.send(
        "message-edit",
        OfficeMessageCommand("private", "old text", "backend-b", ()),
        start_run=object(),
        request=object(),
    )

    await gate.edit(queued["dispatches"][0]["queueId"], "new text")
    await gate.release(blocker.lease_id or "")
    await asyncio.wait_for(launcher.launched.wait(), timeout=1)

    assert launcher.calls[0]["execution_spec"].input == {
        "messages": [{"role": "user", "content": "new text"}]
    }


@pytest.mark.anyio
async def test_queued_group_message_persists_edited_text_when_run_starts(
    tmp_path: Path,
) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker", "backend-b", "direct", "busy", 1, conversation_kind="group"
        )
    )
    pipeline, launcher, store = _pipeline(tmp_path, gate=gate)
    queued = await pipeline.send(
        "message-group-edit",
        OfficeMessageCommand("group", "old text", None, ("backend-b",)),
        start_run=object(),
        request=object(),
    )

    queue_id = queued["dispatches"][0]["queueId"]
    await gate.edit(queue_id, "new text")
    await gate.release(blocker.lease_id or "")
    await asyncio.wait_for(launcher.launched.wait(), timeout=1)

    stored = store.find("message-group-edit")
    assert stored is not None
    assert stored.content == "new text"
    assert launcher.calls[0]["execution_spec"].input == {
        "messages": [{"role": "user", "content": "new text"}]
    }

    active = await gate.get_admission(queue_id)
    if active is not None and active.lease_id is not None:
        await gate.release(active.lease_id)


@pytest.mark.anyio
async def test_interrupted_run_resumes_through_the_existing_employee_gate(
    tmp_path: Path,
) -> None:
    source = SimpleNamespace(
        run_id="run-interrupted",
        assistant_id="frontend-a",
        status=RunStatus.interrupted,
        metadata={"office_conversation_kind": "private"},
    )
    pipeline, launcher, _ = _pipeline(
        tmp_path,
        run_manager=RunManagerStub(source),
    )

    result = await pipeline.resume(
        "frontend-a",
        "run-interrupted",
        source="ask_clarification",
        request_id="clarification:call-one",
        text="D:/workspace",
        start_run=object(),
        request=object(),
    )

    assert result["started"] is True
    assert result["runId"] == "run-resumed-frontend-a"
    assert launcher.resume_calls[0]["resume_payload"] == {
        "version": 1,
        "kind": "human_input_response",
        "source": "ask_clarification",
        "request_id": "clarification:call-one",
        "response_kind": "text",
        "value": "D:/workspace",
    }


@pytest.mark.anyio
async def test_concurrent_identical_resume_requests_start_only_one_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deerflow.office.message_pipeline.time.time", lambda: 1.0)
    source = SimpleNamespace(
        run_id="run-interrupted",
        assistant_id="frontend-a",
        status=RunStatus.interrupted,
        metadata={"office_conversation_kind": "private"},
    )
    gate = RacingEmployeeRunGate()
    pipeline, launcher, _ = _pipeline(
        tmp_path,
        gate=gate,
        run_manager=RunManagerStub(source),
    )

    first, second = await asyncio.gather(*(
        pipeline.resume(
            "frontend-a",
            "run-interrupted",
            source="ask_clarification",
            request_id="clarification:call-one",
            text="D:/workspace",
            start_run=object(),
            request=object(),
        )
        for _ in range(2)
    ))

    assert first == second
    assert len(launcher.resume_calls) == 1


@pytest.mark.anyio
async def test_group_clarification_answer_uses_the_existing_group_message_store(
    tmp_path: Path,
) -> None:
    source = SimpleNamespace(
        run_id="run-group-interrupted",
        assistant_id="frontend-a",
        status=RunStatus.interrupted,
        metadata={"office_conversation_kind": "group"},
    )
    pipeline, _, store = _pipeline(
        tmp_path,
        run_manager=RunManagerStub(source),
    )

    await pipeline.resume(
        "frontend-a",
        "run-group-interrupted",
        source="ask_clarification",
        request_id="clarification:group-one",
        text="使用 React",
        start_run=object(),
        request=object(),
    )

    messages = store.list()
    assert [(message.sender_kind, message.sender_id, message.content) for message in messages] == [
        ("user", "user-a", "使用 React")
    ]
    assert messages[0].message_id == (
        "resume:run-group-interrupted:clarification:group-one"
    )
    assert messages[0].mention_employee_ids == ("frontend-a",)


@pytest.mark.anyio
async def test_group_resume_passes_answer_sequence_for_checkpoint_cursor_confirmation(
    tmp_path: Path,
) -> None:
    source = SimpleNamespace(
        run_id="run-group-interrupted-sequence",
        assistant_id="pm",
        status=RunStatus.interrupted,
        metadata={"office_conversation_kind": "group"},
    )
    pipeline, launcher, store = _pipeline(
        tmp_path,
        run_manager=RunManagerStub(source),
    )

    await pipeline.resume(
        "pm",
        "run-group-interrupted-sequence",
        source="ask_clarification",
        request_id="clarification:group-sequence",
        text="先完成核心功能",
        start_run=object(),
        request=object(),
    )

    answer = store.list()[0]
    assert launcher.resume_calls[0]["office_group_sequence_end"] == answer.sequence


@pytest.mark.anyio
@pytest.mark.parametrize('employee_id,kind', [('pm', 'group'), ('frontend-a', 'group'), ('frontend-a', 'private')])
@pytest.mark.parametrize('busy', [False, True])
async def test_new_ask_after_group_pause_resumes_without_releasing_old_work(tmp_path, employee_id, kind, busy):
    source = SimpleNamespace(run_id='new-ask', assistant_id=employee_id, status=RunStatus.interrupted,
                             metadata={'office_conversation_kind': kind})
    pipeline, launcher, store = _pipeline(tmp_path, run_manager=RunManagerStub(source))
    gate = pipeline.employee_run_gate
    await gate.admit(QueuedOfficeRun('old-held', employee_id, 'direct', 'old work', 1,
                                     conversation_kind='group'), hold=True)
    await pipeline.group_execution.pause()
    blocker = await gate.admit(QueuedOfficeRun('busy', employee_id, 'direct', 'other work', 2,
                                               conversation_kind='private')) if busy else None
    arguments = dict(source='ask_clarification',
                     request_id='clarification:group-one' if kind == 'group' else 'clarification:call-one',
                     text='使用这个名称', start_run=object(), request=object())
    try:
        result = await pipeline.resume(employee_id, source.run_id, **arguments)
        assert result['started'] is not busy
        assert not await gate.is_held(result['queueId'])
        repeated = await pipeline.resume(employee_id, source.run_id, **arguments)
        assert repeated['queueId'] == result['queueId']
        if busy:
            assert not store.list()
            await gate.release(blocker.lease_id)
            await asyncio.wait_for(launcher.resume_launched.wait(), timeout=1)
        assert len(launcher.resume_calls) == 1
        assert launcher.resume_calls[0]['source_record'].run_id == source.run_id
        assert [message.content for message in store.list()] == (['使用这个名称'] if kind == 'group' else [])
        assert pipeline.group_execution.paused
        assert await gate.is_held('old-held')
        assert [item.queue_id for item in await gate.list_pending()] == ['old-held']
    finally:
        await pipeline.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize('pause_case', ['source-paused', 'pause-during-answer', 'stop-settling'])
async def test_ask_resume_still_respects_its_own_pause(tmp_path, monkeypatch, pause_case):
    source = SimpleNamespace(run_id='paused-ask', assistant_id='pm', status=RunStatus.interrupted,
                             metadata={'office_conversation_kind': 'group'})
    pipeline, launcher, store = _pipeline(tmp_path, run_manager=RunManagerStub(source))
    await pipeline.group_execution.pause()
    if pause_case == 'source-paused':
        source.metadata = {**source.metadata, 'office_control': 'paused'}
    elif pause_case == 'stop-settling':
        await pipeline.employee_run_gate.pause_group()
    else:
        validate = pipeline._validate_answer

        async def pause_during_answer(*args):
            waiting = await validate(*args)
            pipeline.group_execution.epoch += 1
            return waiting

        monkeypatch.setattr(pipeline, '_validate_answer', pause_during_answer)
    try:
        result = await pipeline.resume('pm', source.run_id, source='ask_clarification',
                                       request_id='clarification:group-one', text='使用这个名称',
                                       start_run=object(), request=object())
        assert not result['started']
        assert await pipeline.employee_run_gate.is_held(result['queueId'])
        assert not launcher.resume_calls
        assert not store.list()
    finally:
        await pipeline.shutdown()


@pytest.mark.anyio
async def test_group_resume_does_not_reread_pm_group_delta(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = SimpleNamespace(
        run_id="run-group-resume-no-reread",
        assistant_id="pm",
        status=RunStatus.interrupted,
        metadata={"office_conversation_kind": "group"},
    )
    pipeline, launcher, store = _pipeline(
        tmp_path,
        run_manager=RunManagerStub(source),
    )

    def fail_if_read():
        raise AssertionError("Resume must not rebuild the PM group delta")

    monkeypatch.setattr(store, "read_pm_delta", fail_if_read)

    result = await pipeline.resume(
        "pm",
        source.run_id,
        source="ask_clarification",
        request_id="clarification:no-reread",
        text="继续实现核心功能",
        start_run=object(),
        request=object(),
    )

    assert result["runId"] == "run-resumed-pm"
    assert launcher.resume_calls[0]["resume_payload"]["value"] == "继续实现核心功能"


@pytest.mark.anyio
async def test_queued_resume_uses_edited_answer_and_keeps_private_identity(
    tmp_path: Path,
) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker",
            "frontend-a",
            "direct",
            "busy",
            1,
            conversation_kind="private",
        )
    )
    source = SimpleNamespace(
        run_id="run-interrupted",
        assistant_id="frontend-a",
        status=RunStatus.interrupted,
        metadata={"office_conversation_kind": "private"},
    )
    pipeline, launcher, _ = _pipeline(
        tmp_path,
        gate=gate,
        run_manager=RunManagerStub(source),
    )

    result = await pipeline.resume(
        "frontend-a",
        "run-interrupted",
        source="ask_clarification",
        request_id="clarification:call-one",
        text="old answer",
        start_run=object(),
        request=object(),
    )
    assert result["started"] is False
    queued_item = (await gate.list_pending("frontend-a"))[0]
    assert queued_item.conversation_kind == "private"

    await gate.edit(result["queueId"], "new answer")
    await gate.release(blocker.lease_id or "")
    await asyncio.wait_for(launcher.resume_launched.wait(), timeout=1)

    assert launcher.resume_calls[0]["resume_payload"]["value"] == "new answer"


@pytest.mark.anyio
async def test_deleted_queued_message_never_launches(tmp_path: Path) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker", "backend-b", "direct", "busy", 1, conversation_kind="group"
        )
    )
    pipeline, launcher, _ = _pipeline(tmp_path, gate=gate)
    queued = await pipeline.send(
        "message-delete",
        OfficeMessageCommand("private", "queued", "backend-b", ()),
        start_run=object(),
        request=object(),
    )

    await gate.delete(queued["dispatches"][0]["queueId"])
    await asyncio.sleep(0)
    await gate.release(blocker.lease_id or "")
    await asyncio.sleep(0)

    assert launcher.employee_ids == []
    assert await gate.list_pending() == []


@pytest.mark.anyio
async def test_queued_message_reads_project_binding_when_it_really_starts(
    tmp_path: Path,
) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker", "backend-b", "direct", "busy", 1, conversation_kind="group"
        )
    )
    project_service = ProjectServiceStub(binding=SimpleNamespace(project_id="project-a"))
    pipeline, launcher, _ = _pipeline(
        tmp_path,
        gate=gate,
        project_service=project_service,
    )
    await pipeline.send(
        "message-project",
        OfficeMessageCommand("private", "implement", "backend-b", ()),
        start_run=object(),
        request=object(),
    )

    project_service.binding = SimpleNamespace(project_id="project-b")
    await gate.release(blocker.lease_id or "")
    await asyncio.wait_for(launcher.launched.wait(), timeout=1)

    assert launcher.calls[0]["project_binding"].project_id == "project-b"


@pytest.mark.anyio
async def test_existing_run_is_reused_only_when_message_content_matches(
    tmp_path: Path,
) -> None:
    existing = SimpleNamespace(
        run_id="run-existing",
        assistant_id="frontend-a",
        metadata={
            "office_task_summary": "same text",
            "office_queue_id": "queue-existing",
        },
    )
    pipeline, launcher, _ = _pipeline(
        tmp_path,
        run_manager=RunManagerStub(existing),
    )

    repeated = await pipeline.send(
        "message-existing",
        OfficeMessageCommand("private", "same text", "frontend-a", ()),
        start_run=object(),
        request=object(),
    )
    with pytest.raises(ValueError, match="different content"):
        await pipeline.send(
            "message-existing",
            OfficeMessageCommand("private", "changed", "frontend-a", ()),
            start_run=object(),
            request=object(),
        )

    assert repeated["dispatches"] == [
        {
            "runId": "run-existing",
            "employeeId": "frontend-a",
            "queueId": "queue-existing",
            "started": True,
        }
    ]
    assert launcher.employee_ids == []


@pytest.mark.anyio
async def test_queued_message_identity_rejects_changed_content(tmp_path: Path) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker", "backend-b", "direct", "busy", 1, conversation_kind="group"
        )
    )
    pipeline, _, _ = _pipeline(tmp_path, gate=gate)
    await pipeline.send(
        "message-queued-conflict",
        OfficeMessageCommand("private", "first", "backend-b", ()),
        start_run=object(),
        request=object(),
    )

    with pytest.raises(ValueError, match="different content"):
        await pipeline.send(
            "message-queued-conflict",
            OfficeMessageCommand("private", "second", "backend-b", ()),
            start_run=object(),
            request=object(),
        )

    await pipeline.shutdown()
    await gate.release(blocker.lease_id or "")


@pytest.mark.anyio
async def test_pm_run_receives_only_preselected_employee_ids_as_metadata(
    tmp_path: Path,
) -> None:
    pipeline, launcher, _ = _pipeline(tmp_path)

    result = await pipeline.send(
        "message-pm",
        OfficeMessageCommand(
            "group",
            "build calendar",
            None,
            ("frontend-a", "pm", "backend-b"),
        ),
        start_run=object(),
        request=object(),
    )

    assert result["triggerEmployeeIds"] == ["pm"]
    assert launcher.employee_ids == ["pm"]
    metadata = dict(launcher.calls[0]["message_metadata"])
    assert datetime.fromisoformat(metadata.pop('office_work_started_at')).tzinfo is not None
    assert metadata == {
        "office_message_id": "message-pm",
        "office_conversation_kind": "group",
        "office_preselected_employee_ids": ["frontend-a", "backend-b"],
        "office_queue_id": result["dispatches"][0]["queueId"],
        "office_group_sequence_end": 1,
    }
    assert launcher.calls[0]["execution_spec"].input["messages"] == [
        {
            "role": "user",
            "content": "[群聊增量上下文]\n[当前 @PM 请求 message_id=message-pm]\nbuild calendar",
        }
    ]


@pytest.mark.anyio
async def test_pm_group_input_marks_current_request_after_unmentioned_history(
    tmp_path: Path,
) -> None:
    pipeline, launcher, _store = _pipeline(tmp_path)

    await pipeline.send(
        "group-context",
        OfficeMessageCommand("group", "今天办公室天气怎么样？", None, ()),
        start_run=object(),
        request=object(),
    )
    await pipeline.send(
        "group-pm-request",
        OfficeMessageCommand(
            "group",
            "我想给日程管理系统增加团队共享日历功能，前端由周礼负责。",
            None,
            ("pm", "frontend-a"),
        ),
        start_run=object(),
        request=object(),
    )

    assert launcher.calls[0]["execution_spec"].input["messages"] == [
        {
            "role": "user",
            "content": (
                "[群聊增量上下文]\n"
                "[群聊上下文 message_id=group-context 群聊用户 user-a] 今天办公室天气怎么样？\n"
                "[当前 @PM 请求 message_id=group-pm-request]\n"
                "我想给日程管理系统增加团队共享日历功能，前端由周礼负责。"
            ),
        }
    ]


@pytest.mark.anyio
async def test_queued_pm_scans_group_delta_only_after_its_fifo_starts(
    tmp_path: Path,
) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "pm-blocker", "pm", "direct", "busy", 1, conversation_kind="group"
        )
    )
    pipeline, launcher, store = _pipeline(tmp_path, gate=gate)

    queued = await pipeline.send(
        "message-pm-queued",
        OfficeMessageCommand("group", "@PM 安排开发", None, ("pm",)),
        start_run=object(),
        request=object(),
    )
    await pipeline.send(
        "message-during-wait",
        OfficeMessageCommand("group", "补充：需要导出功能", None, ()),
        start_run=object(),
        request=object(),
    )

    assert queued["dispatches"][0]["started"] is False
    assert launcher.calls == []
    assert store.get_pm_cursor() == 0

    await gate.release(blocker.lease_id or "")
    await asyncio.wait_for(launcher.launched.wait(), timeout=2)

    call = launcher.calls[0]
    assert call["message_metadata"]["office_group_sequence_end"] == 2
    assert call["execution_spec"].input["messages"] == [
        {
            "role": "user",
            "content": (
                "[群聊增量上下文]\n"
                "[群聊上下文 message_id=message-during-wait 群聊用户 user-a] 补充：需要导出功能\n"
                "[当前 @PM 请求 message_id=message-pm-queued]\n"
                "@PM 安排开发"
            ),
        }
    ]


@pytest.mark.anyio
async def test_disabled_employee_cannot_enter_the_run_gate(tmp_path: Path) -> None:
    class DisabledEmployeeService(EmployeeServiceStub):
        @staticmethod
        def get_employee_summary(employee_id: str):
            employee = dict(
                next(employee for employee in EMPLOYEES if employee["id"] == employee_id)
            )
            employee["enabled"] = False
            return employee

    gate = EmployeeRunGate()
    pipeline, launcher, _ = _pipeline(
        tmp_path,
        gate=gate,
        employee_service=DisabledEmployeeService(),
    )

    with pytest.raises(RuntimeError, match="disabled"):
        await pipeline.send(
            "message-disabled",
            OfficeMessageCommand("private", "work", "frontend-a", ()),
            start_run=object(),
            request=object(),
        )

    assert launcher.employee_ids == []
    assert await gate.list_pending() == []


@pytest.mark.anyio
async def test_same_message_target_shares_one_dispatch_when_one_caller_is_cancelled(
    tmp_path: Path,
) -> None:
    launcher = BlockingRunLauncherStub()
    pipeline, _, _ = _pipeline(tmp_path, launcher=launcher)
    command = OfficeMessageCommand("private", "实现页面", "frontend-a", ())

    first = asyncio.create_task(
        pipeline.send("message-shared", command, start_run=object(), request=object())
    )
    await asyncio.wait_for(launcher.launched.wait(), timeout=1)
    second = asyncio.create_task(
        pipeline.send("message-shared", command, start_run=object(), request=object())
    )
    await asyncio.sleep(0)

    first.cancel()
    with suppress(asyncio.CancelledError):
        await first
    assert launcher.cancelled.is_set() is False

    launcher.release.set()
    result = await asyncio.wait_for(second, timeout=1)

    assert launcher.employee_ids == ["frontend-a"]
    assert result["dispatches"][0]["runId"] == "run-frontend-a"


@pytest.mark.anyio
async def test_same_message_target_rejects_different_content_while_dispatching(
    tmp_path: Path,
) -> None:
    launcher = BlockingRunLauncherStub()
    pipeline, _, _ = _pipeline(tmp_path, launcher=launcher)
    first = asyncio.create_task(
        pipeline.send(
            "message-conflict",
            OfficeMessageCommand("private", "first", "frontend-a", ()),
            start_run=object(),
            request=object(),
        )
    )
    await asyncio.wait_for(launcher.launched.wait(), timeout=1)

    with pytest.raises(ValueError, match="different content"):
        await pipeline.send(
            "message-conflict",
            OfficeMessageCommand("private", "second", "frontend-a", ()),
            start_run=object(),
            request=object(),
        )

    launcher.release.set()
    await asyncio.wait_for(first, timeout=1)


@pytest.mark.anyio
async def test_launch_failure_forgets_single_flight_and_releases_employee_lease(
    tmp_path: Path,
) -> None:
    launcher = FailOnceRunLauncherStub()
    gate = EmployeeRunGate()
    pipeline, _, _ = _pipeline(tmp_path, gate=gate, launcher=launcher)
    command = OfficeMessageCommand("private", "实现页面", "frontend-a", ())

    with pytest.raises(RuntimeError, match="launch failed"):
        await pipeline.send(
            "message-retry",
            command,
            start_run=object(),
            request=object(),
        )

    result = await pipeline.send(
        "message-retry",
        command,
        start_run=object(),
        request=object(),
    )

    assert launcher.calls == 2
    assert result["dispatches"][0]["runId"] == "run-frontend-a"


@pytest.mark.anyio
async def test_unbound_write_rejection_releases_employee_lease(
    tmp_path: Path,
) -> None:
    class WriteEmployeeService(EmployeeServiceStub):
        @staticmethod
        def get_capability_index(_employee_id: str, *, model_name=None):
            return {
                "authorizedToolGroups": ["file:read", "file:write"],
                "effectiveTools": ["file:read", "file:write"],
                "unavailableAuthorizedToolGroups": [],
                "runtimeAvailableTools": ["read_file", "apply_patch"],
                "toolAvailabilityReasons": {},
            }

    async def unexpected_start_run(*_args, **_kwargs):
        raise AssertionError("unbound write must not create a DeerFlow Run")

    gate = EmployeeRunGate()
    employee_service = WriteEmployeeService()
    launcher = OfficeRunLauncher(
        employee_service=employee_service,
    )
    pipeline, _, _ = _pipeline(
        tmp_path,
        gate=gate,
        launcher=launcher,
        employee_service=employee_service,
    )

    with pytest.raises(RuntimeError, match="project binding is required"):
        await pipeline.send(
            "message-unbound-write",
            OfficeMessageCommand("private", "实现页面", "frontend-a", ()),
            start_run=unexpected_start_run,
            request=object(),
        )

    replacement = await gate.admit(
        QueuedOfficeRun(
            "replacement",
            "frontend-a",
            "direct",
            "next",
            1,
            conversation_kind="group",
        )
    )
    assert replacement.started is True


@pytest.mark.anyio
async def test_shutdown_cancels_and_awaits_inflight_dispatch_and_is_idempotent(
    tmp_path: Path,
) -> None:
    launcher = BlockingRunLauncherStub()
    gate = EmployeeRunGate()
    pipeline, _, _ = _pipeline(tmp_path, gate=gate, launcher=launcher)
    send_task = asyncio.create_task(
        pipeline.send(
            "message-shutdown",
            OfficeMessageCommand("private", "实现页面", "frontend-a", ()),
            start_run=object(),
            request=object(),
        )
    )
    await asyncio.wait_for(launcher.launched.wait(), timeout=1)

    try:
        await pipeline.shutdown()
        await pipeline.shutdown()
    finally:
        if not send_task.done():
            send_task.cancel()
        with suppress(asyncio.CancelledError):
            await send_task

    assert launcher.cancelled.is_set()
    replacement = await gate.admit(
        QueuedOfficeRun(
            "replacement",
            "frontend-a",
            "direct",
            "next",
            1,
            conversation_kind="group",
        )
    )
    assert replacement.started is True


@pytest.mark.anyio
async def test_shutdown_cancels_waiting_queue_task_before_it_can_launch(
    tmp_path: Path,
) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker", "backend-b", "direct", "busy", 1, conversation_kind="group"
        )
    )
    pipeline, launcher, _ = _pipeline(tmp_path, gate=gate)
    queued = await pipeline.send(
        "message-waiting-shutdown",
        OfficeMessageCommand("private", "实现接口", "backend-b", ()),
        start_run=object(),
        request=object(),
    )
    assert queued["dispatches"][0]["started"] is False

    await pipeline.shutdown()
    released = await gate.release(blocker.lease_id or "")
    await asyncio.sleep(0)

    assert launcher.employee_ids == []
    if released is not None and released.next_admission is not None:
        await gate.release(released.next_admission.lease_id or "")


@pytest.mark.anyio
async def test_cancelled_queued_start_releases_lease_after_admission(
    tmp_path: Path,
) -> None:
    gate = EmployeeRunGate()
    blocker = await gate.admit(
        QueuedOfficeRun(
            "blocker-before-queued-start",
            "frontend-a",
            "direct",
            "busy",
            1,
            conversation_kind="private",
        )
    )
    pipeline, launcher, _ = _pipeline(tmp_path, gate=gate)
    queued = await gate.admit(
        QueuedOfficeRun(
            "queued-cancel-before-launch",
            "frontend-a",
            "direct",
            "implement calendar UI",
            2,
            conversation_kind="private",
        )
    )
    assert queued.started is False

    started = asyncio.Event()
    continue_start = asyncio.Event()

    async def on_started(_text: str) -> None:
        started.set()
        await continue_start.wait()

    dispatch = MessageDispatchRequest(
        message_id="message-cancel-before-launch",
        employee_id="frontend-a",
        conversation_kind="private",
        text="implement calendar UI",
        preselected_employee_ids=(),
    )
    task = asyncio.create_task(
        pipeline._start_queued_message(
            dispatch,
            queued.queue_id,
            on_started,
            start_run=object(),
            request=object(),
        )
    )

    released = await gate.release(blocker.lease_id or "")
    assert released is not None
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    replacement = await gate.admit(
        QueuedOfficeRun(
            "replacement-after-queued-cancel",
            "frontend-a",
            "direct",
            "continue calendar UI",
            3,
            conversation_kind="private",
        )
    )
    assert replacement.started is True
    assert launcher.employee_ids == []
