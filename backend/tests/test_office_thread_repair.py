# 本文件属于 Office Thread 自愈与重置测试模块：只验证线程归属行补建、群聊上下文清空和存储清空原语。
# 不负责消息路由、Run 执行、工作流状态机或前端交互测试。
# 关联：office/employees.py 提供自愈与重置，group_messages.py 与 workflows.py 提供清空原语。
"""Regression cover for the half-reset that stranded the PM group thread.

Deleting a thread ownership row while its agent and thread directories survive makes
every run start 404 forever, because ``thread_store.check_access`` gates the run and
nothing ever recreates the row. These tests pin the repair path that closes that hole.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from deerflow.config.paths import Paths
from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.thread_ids import employee_thread_id
from deerflow.office.workflows import WorkflowStore
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.runs.manager import RunManager

MODEL_PROFILE_ID = "profile-primary"


def build_employee_service(tmp_path: Path) -> tuple[OfficeEmployeeService, MemoryThreadMetaStore, InMemorySaver]:
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    checkpointer = InMemorySaver()
    service = OfficeEmployeeService(
        paths=Paths(tmp_path / ".deer-flow"),
        thread_store=thread_store,
        checkpointer=checkpointer,
        run_manager=RunManager(),
        user_id="default",
        model_profile_exists=lambda profile_id: profile_id == MODEL_PROFILE_ID,
        workstation_ids=("desk-01", "desk-02", "desk-03"),
    )
    return service, thread_store, checkpointer


def employee_payload(employee_id: str = "frontend-a") -> dict[str, str]:
    return {
        "id": employee_id,
        "templateId": "frontend-engineer",
        "displayName": "周礼",
        "avatar": "adam",
        "modelProfileId": MODEL_PROFILE_ID,
    }


def checkpoint_step(checkpointer: InMemorySaver, thread_id: str) -> int | None:
    """The ``step`` marker distinguishes real history from the empty base checkpoint."""
    snapshot = checkpointer.get_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    return None if snapshot is None else int(snapshot.metadata["step"])


def seed_history(checkpointer: InMemorySaver, thread_id: str, employee_id: str, kind: str) -> None:
    checkpointer.put(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        empty_checkpoint(),
        {"step": 7, "source": "loop", "writes": None, "parents": {},
         "employee_id": employee_id, "office_conversation_kind": kind},
        {},
    )


@pytest.mark.asyncio
async def test_builtin_pm_repairs_a_missing_group_thread_row(tmp_path: Path) -> None:
    service, thread_store, _ = build_employee_service(tmp_path)
    group_thread = employee_thread_id("default", "pm", "group", is_pm=True)
    await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)
    await thread_store.delete(group_thread, user_id="default")

    assert await thread_store.get(group_thread, user_id="default") is None

    await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)

    restored = await thread_store.get(group_thread, user_id="default")
    assert restored is not None
    assert restored["metadata"]["office_employee_id"] == "pm"
    assert restored["metadata"]["office_conversation_kind"] == "group"


@pytest.mark.asyncio
async def test_repair_threads_restores_rows_and_never_overwrites_history(tmp_path: Path) -> None:
    service, thread_store, checkpointer = build_employee_service(tmp_path)
    employee = await service.create_employee(employee_payload())
    group_thread = service.thread_id(employee["id"], "group")
    private_thread = service.thread_id(employee["id"], "private")
    seed_history(checkpointer, group_thread, employee["id"], "group")
    seed_history(checkpointer, private_thread, employee["id"], "private")
    await thread_store.delete(group_thread, user_id="default")

    repaired = await service.repair_threads()

    assert {"employeeId": employee["id"], "threadId": group_thread} in repaired
    assert await thread_store.get(group_thread, user_id="default") is not None
    assert await thread_store.get(private_thread, user_id="default") is not None
    # Repair is additive: surviving history must not be reset to the base checkpoint.
    assert checkpoint_step(checkpointer, group_thread) == 7
    assert checkpoint_step(checkpointer, private_thread) == 7


@pytest.mark.asyncio
async def test_repair_threads_is_idempotent_and_reports_nothing_when_healthy(tmp_path: Path) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)
    employee = await service.create_employee(employee_payload())

    assert await service.repair_threads() == []
    assert await service.repair_threads() == []
    assert sorted(item["id"] for item in await service.list_employees()) == [employee["id"], "pm"]


@pytest.mark.asyncio
async def test_reset_conversation_context_clears_group_history_but_keeps_ownership(tmp_path: Path) -> None:
    service, thread_store, checkpointer = build_employee_service(tmp_path)
    employee = await service.create_employee(employee_payload())
    group_thread = service.thread_id(employee["id"], "group")
    private_thread = service.thread_id(employee["id"], "private")
    seed_history(checkpointer, group_thread, employee["id"], "group")
    seed_history(checkpointer, private_thread, employee["id"], "private")

    returned = await service.reset_conversation_context(employee["id"], kind="group")

    assert returned == group_thread
    # The base checkpoint replaces the history without removing the thread itself.
    assert checkpoint_step(checkpointer, group_thread) == -1
    assert checkpoint_step(checkpointer, private_thread) == 7
    # Deleting these rows is precisely the bug this module guards against.
    assert await thread_store.get(group_thread, user_id="default") is not None
    assert await thread_store.get(private_thread, user_id="default") is not None


@pytest.mark.asyncio
async def test_reset_conversation_context_leaves_an_unknown_thread_untouched(tmp_path: Path) -> None:
    service, thread_store, checkpointer = build_employee_service(tmp_path)
    employee = await service.create_employee(employee_payload())
    group_thread = service.thread_id(employee["id"], "group")
    await thread_store.delete(group_thread, user_id="default")

    assert await service.reset_conversation_context(employee["id"], kind="group") == group_thread
    # A reset must not silently recreate the ownership row it was asked to clear.
    assert await thread_store.get(group_thread, user_id="default") is None
    # The unchanged base checkpoint proves the reset returned early instead of writing.
    assert checkpoint_step(checkpointer, group_thread) == -1


def test_group_message_store_clear_resets_cursor_and_sequence(tmp_path: Path) -> None:
    store = GroupMessageStore(paths=Paths(tmp_path / ".deer-flow"), user_id="default")
    store.append(message_id="m1", sender_kind="user", sender_id="default", content="你好")
    store.append(
        message_id="m2", sender_kind="pm", sender_id="pm",
        sender_display_name="项目经理", content="收到",
    )
    store.advance_pm_cursor(2)

    assert store.clear() == 2

    assert store.list() == []
    assert store.get_pm_cursor() == 0
    assert store.read_pm_delta().messages == ()
    # A stale AUTOINCREMENT high-water mark would hide the next message from the client,
    # which tracks nextAfterSequence and would never request a sequence above it.
    assert store.append(
        message_id="m3", sender_kind="user", sender_id="default", content="再来",
    ).sequence == 1
    assert len(store.read_pm_delta().messages) == 1


def test_workflow_store_clear_is_scoped_to_one_project(tmp_path: Path) -> None:
    store = WorkflowStore(paths=Paths(tmp_path / ".deer-flow"), user_id="default")
    from office_history_fixture import seed_history
    seed_history(store, workflow_id="a", project_id="project-a")
    seed_history(store, workflow_id="b", project_id="project-b")

    assert store.clear(project_id="project-a") == 1

    assert store.current(project_id="project-a") is None
    assert store.list(project_id="project-a") == ()
    assert store.current(project_id="project-b") is not None

    with pytest.raises(ValueError):
        store.clear(project_id="")
