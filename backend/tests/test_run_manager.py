# 本文件属于 DeerFlow RunManager 测试模块：只验证权威 Run 生命周期、并发、取消、恢复和存储同步。
# 不负责 Office Mention、员工 FIFO、群聊保存、状态投影或业务路由测试。
# 关联：runtime/runs/manager.py 是被测核心，RunStore 提供可替换持久化实现。
"""Tests for RunManager."""

import asyncio
import inspect
import logging
import re
import sqlite3
from typing import Any

import pytest
from sqlalchemy.exc import DatabaseError as SQLAlchemyDatabaseError

from deerflow.runtime import DisconnectMode, RunManager, RunStatus
from deerflow.runtime.runs.manager import CancelOutcome, ConflictError, PersistenceRetryPolicy, RunPersistenceError
from deerflow.runtime.runs.store.memory import MemoryRunStore

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@pytest.fixture
def manager() -> RunManager:
    return RunManager()


@pytest.mark.anyio
async def test_runtime_finish_metadata_preserves_usage_and_survives_later_updates():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create('thread-finish', 'pm', user_id='owner')
    await manager.finalize_run(record.run_id, status=RunStatus.success, last_event_seq=3,
        total_tokens=123, last_ai_message='done')
    ended = '2026-10-28T01:02:03+00:00'
    await manager.update_metadata(record.run_id, {'office_runtime_finished_at': ended}, user_id='owner')
    await manager.update_metadata(record.run_id, {'later_projection': True}, user_id='owner')
    saved = await store.get(record.run_id, user_id='owner')
    assert saved['metadata']['office_runtime_finished_at'] == ended
    assert saved['total_tokens'] == 123
    assert saved['last_ai_message'] == 'done'
    assert saved['events_finalized'] is True


class FlakyStatusRunStore(MemoryRunStore):
    """Memory run store that simulates transient SQLite status-write failures."""

    def __init__(self, *, status_failures: int) -> None:
        super().__init__()
        self.status_failures = status_failures
        self.status_update_attempts = 0

    async def update_status(self, run_id, status, *, error=None):
        self.status_update_attempts += 1
        if self.status_failures > 0:
            self.status_failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return await super().update_status(run_id, status, error=error)


class MissingRowStatusRunStore(MemoryRunStore):
    """Memory run store that reports a missing row for status updates."""

    async def update_status(self, run_id, status, *, error=None):
        await super().update_status(run_id, status, error=error)
        return False


class PermanentStatusRunStore(MemoryRunStore):
    """Memory run store that simulates a permanent SQLAlchemy write failure."""

    def __init__(self) -> None:
        super().__init__()
        self.status_update_attempts = 0

    async def update_status(self, run_id, status, *, error=None):
        self.status_update_attempts += 1
        raise SQLAlchemyDatabaseError(
            "UPDATE runs SET status = :status WHERE run_id = :run_id",
            {"status": status, "run_id": run_id},
            sqlite3.DatabaseError("no such table: runs"),
        )


class FailingFinalizeRunStore(MemoryRunStore):
    """Memory run store that always fails atomic finalization."""

    def __init__(self) -> None:
        super().__init__()
        self.finalize_attempts = 0

    async def finalize_run(self, run_id, **kwargs):
        self.finalize_attempts += 1
        raise sqlite3.OperationalError("database is locked")


class MissingCompletionRunStore(MemoryRunStore):
    """Memory run store that reports one missing row for completion updates."""

    def __init__(self) -> None:
        super().__init__()
        self.completion_update_attempts = 0

    async def update_run_completion(self, run_id, *, status, **kwargs):
        self.completion_update_attempts += 1
        if self.completion_update_attempts == 1:
            return False
        return await super().update_run_completion(run_id, status=status, **kwargs)


class AlwaysMissingCompletionRunStore(MemoryRunStore):
    """Memory run store that keeps reporting missing rows for completion updates."""

    def __init__(self) -> None:
        super().__init__()
        self.completion_update_attempts = 0

    async def update_run_completion(self, run_id, *, status, **kwargs):
        self.completion_update_attempts += 1
        return False


class SecretBearingCompletionRunStore(MemoryRunStore):
    """Run store whose provider-style failure embeds a synthetic credential."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    async def update_run_completion(self, run_id, *, status, **kwargs):
        raise RuntimeError(f"database rejected credential {self.secret}")


async def _stored_statuses(store: MemoryRunStore, *run_ids: str) -> dict[str, Any]:
    rows = {}
    for run_id in run_ids:
        row = await store.get(run_id)
        rows[run_id] = row["status"] if row else None
    return rows


async def _finalize(
    manager: RunManager,
    run_id: str,
    status: RunStatus,
    *,
    error: str | None = None,
) -> None:
    await manager.finalize_run(
        run_id,
        status=status,
        last_event_seq=0,
        error=error,
    )


@pytest.mark.anyio
async def test_create_and_get(manager: RunManager):
    """Created run should be retrievable with new fields."""
    record = await manager.create(
        "thread-1",
        "lead_agent",
        metadata={"key": "val"},
        kwargs={"input": {}},
        multitask_strategy="reject",
    )
    assert record.status == RunStatus.pending
    assert record.thread_id == "thread-1"
    assert record.assistant_id == "lead_agent"
    assert record.metadata == {"key": "val"}
    assert record.kwargs == {"input": {}}
    assert record.multitask_strategy == "reject"
    assert ISO_RE.match(record.created_at)
    assert ISO_RE.match(record.updated_at)

    fetched = await manager.get(record.run_id)
    assert fetched is record


@pytest.mark.anyio
async def test_status_transitions(manager: RunManager):
    """Status should transition pending -> running -> success."""
    record = await manager.create("thread-1")
    assert record.status == RunStatus.pending

    await manager.set_status(record.run_id, RunStatus.running)
    assert record.status == RunStatus.running
    assert ISO_RE.match(record.updated_at)

    await _finalize(manager, record.run_id, RunStatus.success)
    assert record.status == RunStatus.success
    assert record.events_finalized is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "terminal_status",
    [
        RunStatus.success,
        RunStatus.error,
        RunStatus.timeout,
        RunStatus.interrupted,
    ],
)
async def test_set_status_rejects_terminal_states(
    manager: RunManager,
    terminal_status: RunStatus,
):
    """Terminal states must include an event boundary via finalize_run()."""
    record = await manager.create("thread-1")

    with pytest.raises(ValueError, match="must use finalize_run"):
        await manager.set_status(record.run_id, terminal_status)

    assert record.status == RunStatus.pending
    assert record.events_finalized is False


@pytest.mark.anyio
async def test_cancel_signals_worker(manager: RunManager):
    """Cancel should set abort_event and transition to interrupted."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    cancelled = await manager.cancel(record.run_id)
    assert cancelled == CancelOutcome.cancelled
    assert record.abort_event.is_set()
    assert record.abort_action == 'interrupt'
    assert record.status == RunStatus.running
    assert record.events_finalized is False


@pytest.mark.anyio
async def test_cancel_persists_interrupted_status_to_store():
    """Cancel should persist interrupted status to the backing store."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    cancelled = await manager.cancel(record.run_id)

    stored = await store.get(record.run_id)
    assert cancelled == CancelOutcome.cancelled
    assert stored is not None
    assert stored['status'] == 'running'
    assert stored['events_finalized'] is False


@pytest.mark.anyio
async def test_status_persistence_retries_transient_sqlite_lock():
    """Transient SQLite lock errors should not leave an active status stale."""
    store = FlakyStatusRunStore(status_failures=2)
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["status"] == "running"
    assert store.status_update_attempts == 3


@pytest.mark.anyio
async def test_status_persistence_recreates_missing_store_row():
    """An active status update should recreate a missing persisted run row."""
    store = MissingRowStatusRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await store.delete(record.run_id)

    await manager.set_status(record.run_id, RunStatus.running)

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["status"] == "running"


@pytest.mark.anyio
async def test_status_persistence_does_not_retry_permanent_sqlalchemy_errors():
    """Permanent SQLAlchemy failures should not be retried as SQLite pressure."""
    store = PermanentStatusRunStore()
    manager = RunManager(
        store=store,
        persistence_retry_policy=PersistenceRetryPolicy(max_attempts=5, initial_delay=0),
    )
    record = await manager.create("thread-1")

    with pytest.raises(RunPersistenceError, match="update_status"):
        await manager.set_status(record.run_id, RunStatus.running)

    assert store.status_update_attempts == 1
    assert record.status == RunStatus.pending


@pytest.mark.anyio
async def test_completion_persistence_recreates_missing_store_row():
    """Completion updates should recreate a missing row and persist final counters."""
    store = MissingCompletionRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)
    await _finalize(manager, record.run_id, RunStatus.success)
    await store.delete(record.run_id)

    await manager.update_run_completion(
        record.run_id,
        status="success",
        total_tokens=42,
        llm_call_count=2,
        last_ai_message="done",
    )

    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["status"] == "success"
    assert stored["total_tokens"] == 42
    assert stored["llm_call_count"] == 2
    assert stored["last_ai_message"] == "done"
    assert store.completion_update_attempts == 2


@pytest.mark.anyio
async def test_completion_persistence_warns_when_recreated_row_still_missing(caplog):
    """A second zero-row completion update after recreation should not be silent."""
    store = AlwaysMissingCompletionRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await _finalize(manager, record.run_id, RunStatus.success)
    caplog.set_level(logging.WARNING, logger="deerflow.runtime.runs.manager")

    await manager.update_run_completion(record.run_id, status="success", total_tokens=42)

    assert store.completion_update_attempts == 2
    assert "affected no rows after row recreation" in caplog.text


@pytest.mark.anyio
async def test_completion_persistence_does_not_log_secret_bearing_store_exception(caplog):
    """Store tracebacks must not turn run-local credentials into log data."""
    secret = "run-manager-synthetic-secret"
    store = SecretBearingCompletionRunStore(secret)
    manager = RunManager(store=store)
    record = await manager.create("thread-secret-log")
    caplog.set_level(logging.WARNING, logger="deerflow.runtime.runs.manager")

    await manager.update_run_completion(
        record.run_id,
        status="success",
        last_ai_message="safe completion",
    )

    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


def test_run_manager_does_not_emit_raw_exception_tracebacks():
    """All Run Store warning paths share the non-payload logging boundary."""
    assert "exc_info=True" not in inspect.getsource(RunManager)


@pytest.mark.anyio
async def test_restore_inflight_runs_registers_original_rows_without_finalizing_them():
    """Startup restore must keep the original Run identity and active state."""
    store = MemoryRunStore()
    pending_kwargs = {
        "execution_spec": {
            "version": 1,
            "mode": "new",
            "input": {"messages": [{"role": "user", "content": "生成项目说明"}]},
        }
    }
    running_kwargs = {
        "execution_spec": {
            "version": 1,
            "mode": "resume",
            "source_run_id": "source-run",
            "checkpoint_id": "checkpoint-7",
            "resume_payload": {"approved": True},
        }
    }
    await store.put(
        "pending-run",
        thread_id="thread-1",
        assistant_id="employee-one",
        user_id="owner-one",
        status="pending",
        kwargs=pending_kwargs,
        created_at="2026-01-01T00:00:00+00:00",
    )
    await store.put(
        "running-run",
        thread_id="thread-2",
        assistant_id="employee-two",
        user_id="owner-one",
        status="running",
        kwargs=running_kwargs,
        created_at="2026-01-01T00:00:01+00:00",
    )
    await store.put("success-run", thread_id="thread-1", user_id="owner-one", status="success", created_at="2026-01-01T00:00:02+00:00")
    manager = RunManager(store=store)

    restored = await manager.restore_inflight_runs(
        before="2026-01-01T00:00:02+00:00",
    )

    assert [record.run_id for record in restored] == ["pending-run", "running-run"]
    pending_row = await store.get("pending-run", user_id="owner-one")
    running_row = await store.get("running-run", user_id="owner-one")
    success_row = await store.get("success-run", user_id="owner-one")
    assert {
        "pending-run": pending_row["status"] if pending_row else None,
        "running-run": running_row["status"] if running_row else None,
        "success-run": success_row["status"] if success_row else None,
    } == {
        "pending-run": "pending",
        "running-run": "running",
        "success-run": "success",
    }
    assert all(record.store_only is False for record in restored)
    assert all(record.events_finalized is False for record in restored)
    assert all(record.task is None for record in restored)
    assert restored[0].assistant_id == "employee-one"
    assert restored[0].user_id == "owner-one"
    assert restored[0].kwargs == pending_kwargs
    assert restored[1].kwargs == running_kwargs


@pytest.mark.anyio
async def test_restore_inflight_runs_skips_live_local_run():
    """Startup restore must not replace a Run already owned by this process."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    restored = await manager.restore_inflight_runs()

    stored = await store.get(record.run_id)
    assert restored == []
    assert stored["status"] == "running"
    assert await manager.get(record.run_id) is record


@pytest.mark.anyio
async def test_restore_inflight_runs_is_atomic_when_any_persisted_row_is_invalid():
    """One corrupt row must not leave a partially registered recovery set."""

    class InvalidInflightStore(MemoryRunStore):
        async def list_inflight(self, *, before=None):
            rows = await super().list_inflight(before=before)
            rows.append({"run_id": "broken-run", "status": "running"})
            return rows

    store = InvalidInflightStore()
    await store.put("valid-run", thread_id="thread-1", status="running")
    manager = RunManager(store=store)

    with pytest.raises(KeyError, match="thread_id"):
        await manager.restore_inflight_runs()

    hydrated = await manager.get("valid-run")
    assert hydrated is not None
    assert hydrated.status == RunStatus.running
    assert hydrated.store_only is True
    assert await manager.has_inflight("thread-1") is False


@pytest.mark.anyio
async def test_cancel_not_inflight(manager: RunManager):
    """Cancelling a completed run should return not_cancellable."""
    record = await manager.create("thread-1")
    await _finalize(manager, record.run_id, RunStatus.success)

    cancelled = await manager.cancel(record.run_id)
    assert cancelled == CancelOutcome.not_cancellable


@pytest.mark.anyio
async def test_list_by_thread(manager: RunManager, monkeypatch: pytest.MonkeyPatch):
    """Same thread should return multiple runs."""
    timestamps = iter(
        [
            "2026-01-01T00:00:00.000001+00:00",
            "2026-01-01T00:00:00.000002+00:00",
            "2026-01-01T00:00:00.000003+00:00",
        ]
    )
    monkeypatch.setattr("deerflow.runtime.runs.manager._now_iso", lambda: next(timestamps))

    r1 = await manager.create("thread-1")
    r2 = await manager.create("thread-1")
    await manager.create("thread-2")

    runs = await manager.list_by_thread("thread-1")
    assert len(runs) == 2
    # Newest first: r2 was created after r1.
    assert runs[0].run_id == r2.run_id
    assert runs[1].run_id == r1.run_id


@pytest.mark.anyio
async def test_list_by_thread_is_stable_when_timestamps_tie(manager: RunManager, monkeypatch: pytest.MonkeyPatch):
    """Ordering should be stable (insertion order) even when timestamps tie."""
    monkeypatch.setattr("deerflow.runtime.runs.manager._now_iso", lambda: "2026-01-01T00:00:00+00:00")

    r1 = await manager.create("thread-1")
    r2 = await manager.create("thread-1")

    runs = await manager.list_by_thread("thread-1")
    assert [run.run_id for run in runs] == [r1.run_id, r2.run_id]


@pytest.mark.anyio
async def test_has_inflight(manager: RunManager):
    """has_inflight should be True when a run is pending or running."""
    record = await manager.create("thread-1")
    assert await manager.has_inflight("thread-1") is True

    await _finalize(manager, record.run_id, RunStatus.success)
    assert await manager.has_inflight("thread-1") is False


@pytest.mark.anyio
async def test_cleanup(manager: RunManager):
    """After cleanup, the run should be gone."""
    record = await manager.create("thread-1")
    run_id = record.run_id

    await manager.cleanup(run_id, delay=0)
    assert await manager.get(run_id) is None


@pytest.mark.anyio
async def test_forget_thread_deletes_store_only_history_after_restart():
    """Conversation reset must remove completed runs not loaded in memory."""
    store = MemoryRunStore()
    original_manager = RunManager(store=store)
    old_run = await original_manager.create("thread-1", user_id="owner-a")
    await _finalize(original_manager, old_run.run_id, RunStatus.error, error="old provider error")

    # A fresh Gateway restores only in-flight Runs. Completed history therefore
    # exists only in RunStore, which is exactly the state group reset sees after
    # an application restart.
    restarted_manager = RunManager(store=store)
    assert restarted_manager._runs == {}
    assert [row["run_id"] for row in await store.list_by_thread("thread-1", user_id="owner-a")] == [old_run.run_id]

    forgotten = await restarted_manager.forget_thread("thread-1", user_id="owner-a")

    assert forgotten == 1
    assert await store.list_by_thread("thread-1", user_id="owner-a") == []


@pytest.mark.anyio
async def test_set_status_with_error(manager: RunManager):
    """Error message should be stored on the record."""
    record = await manager.create("thread-1")
    await _finalize(
        manager,
        record.run_id,
        RunStatus.error,
        error="Something went wrong",
    )
    assert record.status == RunStatus.error
    assert record.error == "Something went wrong"


@pytest.mark.anyio
async def test_get_nonexistent(manager: RunManager):
    """Getting a nonexistent run should return None."""
    assert await manager.get("does-not-exist") is None


@pytest.mark.anyio
async def test_get_hydrates_store_only_run():
    """Store-only runs should be readable after process restart."""
    store = MemoryRunStore()
    await store.put(
        "run-store-only",
        thread_id="thread-1",
        assistant_id="lead_agent",
        status="success",
        multitask_strategy="reject",
        metadata={"source": "store"},
        kwargs={"input": "value"},
        created_at="2026-01-01T00:00:00+00:00",
        model_name="model-a",
    )
    manager = RunManager(store=store)

    record = await manager.get("run-store-only")

    assert record is not None
    assert record.run_id == "run-store-only"
    assert record.thread_id == "thread-1"
    assert record.assistant_id == "lead_agent"
    assert record.status == RunStatus.success
    assert record.on_disconnect == DisconnectMode.cancel
    assert record.metadata == {"source": "store"}
    assert record.kwargs == {"input": "value"}
    assert record.model_name == "model-a"
    assert record.task is None
    assert record.store_only is True


@pytest.mark.anyio
async def test_get_hydrates_run_with_null_enum_fields():
    """Rows with NULL status/on_disconnect must hydrate with safe defaults, not raise."""
    store = MemoryRunStore()
    # Simulate a SQL row where the nullable status column is NULL
    await store.put(
        "run-null-status",
        thread_id="thread-1",
        status=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    manager = RunManager(store=store)

    record = await manager.get("run-null-status")

    assert record is not None
    assert record.status == RunStatus.pending
    assert record.on_disconnect == DisconnectMode.cancel
    assert record.store_only is True


@pytest.mark.anyio
async def test_list_by_thread_hydrates_run_with_null_enum_fields():
    """list_by_thread must not skip rows with NULL status; applies safe defaults."""
    store = MemoryRunStore()
    await store.put(
        "run-null-status-list",
        thread_id="thread-null",
        status=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    manager = RunManager(store=store)

    runs = await manager.list_by_thread("thread-null")

    assert len(runs) == 1
    assert runs[0].run_id == "run-null-status-list"
    assert runs[0].status == RunStatus.pending
    assert runs[0].on_disconnect == DisconnectMode.cancel


@pytest.mark.anyio
async def test_create_record_is_not_store_only(manager: RunManager):
    """In-memory records created via create() must have store_only=False."""
    record = await manager.create("thread-1")
    assert record.store_only is False


@pytest.mark.anyio
async def test_create_rolls_back_in_memory_record_on_store_failure():
    """create() must fail and hide the run when the initial store write fails."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    store.put = AsyncMock(side_effect=RuntimeError("db down"))
    manager = RunManager(store=store)

    with pytest.raises(RuntimeError, match="db down"):
        await manager.create("thread-1")

    assert manager._runs == {}
    assert await manager.list_by_thread("thread-1") == []


@pytest.mark.anyio
async def test_create_rolls_back_in_memory_record_on_store_cancellation():
    """create() must also roll back when cancelled during the initial store write."""
    store = MemoryRunStore()

    async def cancelled_put(run_id, **kwargs):
        raise asyncio.CancelledError

    store.put = cancelled_put
    manager = RunManager(store=store)

    with pytest.raises(asyncio.CancelledError):
        await manager.create("thread-1")

    assert manager._runs == {}
    assert await manager.list_by_thread("thread-1") == []


@pytest.mark.anyio
async def test_create_does_not_expose_run_until_store_persist_completes():
    """A pending store write must not block unrelated readers or expose the run."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    original_put = store.put
    put_started = asyncio.Event()
    allow_put = asyncio.Event()

    async def blocking_put(run_id, **kwargs):
        put_started.set()
        await allow_put.wait()
        return await original_put(run_id, **kwargs)

    store.put = blocking_put
    create_task = asyncio.create_task(manager.create("thread-1"))
    list_task = None

    try:
        await put_started.wait()
        list_task = asyncio.create_task(manager.list_by_thread("thread-1"))
        runs_before_persist = await asyncio.wait_for(list_task, timeout=0.1)
        assert runs_before_persist == []

        allow_put.set()
        record = await create_task
        runs = await manager.list_by_thread("thread-1")

        assert [run.run_id for run in runs] == [record.run_id]
    finally:
        allow_put.set()
        cleanup_tasks = []
        for task in (list_task, create_task):
            if task is None:
                continue
            if not task.done():
                task.cancel()
            cleanup_tasks.append(task)
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_get_prefers_in_memory_record_over_store():
    """In-memory records retain task/control state when store has same run."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await store.update_status(record.run_id, "success")

    fetched = await manager.get(record.run_id)

    assert fetched is record
    assert fetched.status == RunStatus.pending


@pytest.mark.anyio
async def test_list_by_thread_merges_store_runs_newest_first():
    """list_by_thread should merge memory and store rows with memory precedence."""
    store = MemoryRunStore()
    await store.put("old-store", thread_id="thread-1", status="success", created_at="2026-01-01T00:00:00+00:00")
    await store.put("other-thread", thread_id="thread-2", status="success", created_at="2026-01-03T00:00:00+00:00")
    manager = RunManager(store=store)
    memory_record = await manager.create("thread-1")

    runs = await manager.list_by_thread("thread-1")

    assert [run.run_id for run in runs] == [memory_record.run_id, "old-store"]
    assert runs[0] is memory_record


@pytest.mark.anyio
async def test_create_defaults(manager: RunManager):
    """Create with no optional args should use defaults."""
    record = await manager.create("thread-1")
    assert record.metadata == {}
    assert record.kwargs == {}
    assert record.multitask_strategy == "reject"
    assert record.assistant_id is None


@pytest.mark.anyio
async def test_model_name_create_or_reject():
    """create_or_reject should accept and persist model_name."""
    from deerflow.runtime.runs.schemas import DisconnectMode

    store = MemoryRunStore()
    mgr = RunManager(store=store)

    record = await mgr.create_or_reject(
        "thread-1",
        assistant_id="lead_agent",
        on_disconnect=DisconnectMode.cancel,
        metadata={"key": "val"},
        kwargs={"input": {}},
        multitask_strategy="reject",
        model_name="anthropic.claude-sonnet-4-20250514-v1:0",
    )
    assert record.model_name == "anthropic.claude-sonnet-4-20250514-v1:0"
    assert record.status == RunStatus.pending

    # Verify model_name was persisted to store
    stored = await store.get(record.run_id)
    assert stored is not None
    assert stored["model_name"] == "anthropic.claude-sonnet-4-20250514-v1:0"

    # Verify retrieval returns the model_name via in-memory record
    fetched = await mgr.get(record.run_id)
    assert fetched is not None
    assert fetched.model_name == "anthropic.claude-sonnet-4-20250514-v1:0"


@pytest.mark.anyio
async def test_create_or_reject_interrupt_persists_interrupted_status_to_store():
    """interrupt strategy should persist interrupted status for old runs."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)

    new = await manager.create_or_reject("thread-1", multitask_strategy="interrupt")

    stored_old = await store.get(old.run_id)
    assert new.run_id != old.run_id
    assert old.status == RunStatus.running
    assert old.abort_event.is_set()
    assert old.abort_action == 'interrupt'
    assert stored_old is not None
    assert stored_old['status'] == 'running'


@pytest.mark.anyio
async def test_create_or_reject_does_not_interrupt_old_run_when_new_run_store_write_fails():
    """A failed new-run persist must not cancel the existing inflight run."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)
    store.put = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(RuntimeError, match="db down"):
        await manager.create_or_reject("thread-1", multitask_strategy="interrupt")

    stored_old = await store.get(old.run_id)
    assert list(manager._runs) == [old.run_id]
    assert old.status == RunStatus.running
    assert old.abort_event.is_set() is False
    assert stored_old is not None
    assert stored_old["status"] == "running"


@pytest.mark.anyio
async def test_create_or_reject_does_not_interrupt_old_run_when_new_run_store_write_is_cancelled():
    """Cancellation during new-run persist must not cancel the existing run."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)

    async def cancelled_put(run_id, **kwargs):
        raise asyncio.CancelledError

    store.put = cancelled_put

    with pytest.raises(asyncio.CancelledError):
        await manager.create_or_reject("thread-1", multitask_strategy="interrupt")

    stored_old = await store.get(old.run_id)
    assert list(manager._runs) == [old.run_id]
    assert old.status == RunStatus.running
    assert old.abort_event.is_set() is False
    assert stored_old is not None
    assert stored_old["status"] == "running"


@pytest.mark.anyio
async def test_create_or_reject_rollback_persists_interrupted_status_to_store():
    """rollback strategy should persist interrupted status for old runs."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    old = await manager.create("thread-1")
    await manager.set_status(old.run_id, RunStatus.running)

    new = await manager.create_or_reject("thread-1", multitask_strategy="rollback")

    stored_old = await store.get(old.run_id)
    assert new.run_id != old.run_id
    assert old.status == RunStatus.running
    assert old.abort_event.is_set()
    assert old.abort_action == 'rollback'
    assert stored_old is not None
    assert stored_old['status'] == 'running'


@pytest.mark.anyio
async def test_model_name_default_is_none():
    """create_or_reject without model_name should default to None."""
    from deerflow.runtime.runs.schemas import DisconnectMode

    store = MemoryRunStore()
    mgr = RunManager(store=store)

    record = await mgr.create_or_reject(
        "thread-1",
        on_disconnect=DisconnectMode.cancel,
        model_name=None,
    )
    assert record.model_name is None

    stored = await store.get(record.run_id)
    assert stored["model_name"] is None


# ---------------------------------------------------------------------------
# Store fallback tests (simulates gateway restart scenario)
# ---------------------------------------------------------------------------


@pytest.fixture
def manager_with_store() -> RunManager:
    """RunManager backed by a MemoryRunStore."""
    return RunManager(store=MemoryRunStore())


@pytest.mark.anyio
async def test_list_by_thread_returns_store_records_after_restart(manager_with_store: RunManager):
    """After in-memory state is cleared (simulating restart), list_by_thread
    should still return runs from the persistent store."""
    mgr = manager_with_store
    r1 = await mgr.create("thread-1", "agent-1")
    await _finalize(mgr, r1.run_id, RunStatus.success)
    r2 = await mgr.create("thread-1", "agent-2")
    await _finalize(mgr, r2.run_id, RunStatus.error, error="boom")

    # Clear in-memory dict to simulate a restart
    mgr._runs.clear()

    runs = await mgr.list_by_thread("thread-1")
    assert len(runs) == 2
    statuses = {r.run_id: r.status for r in runs}
    assert statuses[r1.run_id] == RunStatus.success
    assert statuses[r2.run_id] == RunStatus.error
    # Verify other fields survive the round-trip
    for r in runs:
        assert r.thread_id == "thread-1"
        assert ISO_RE.match(r.created_at)


@pytest.mark.anyio
async def test_list_by_thread_merges_in_memory_and_store(manager_with_store: RunManager):
    """In-memory runs should be included alongside store-only records."""
    mgr = manager_with_store

    # Create a run and let it complete (will be in both memory and store)
    r1 = await mgr.create("thread-1")
    await _finalize(mgr, r1.run_id, RunStatus.success)

    # Simulate restart: clear memory, then create a new in-memory run
    mgr._runs.clear()
    r2 = await mgr.create("thread-1")

    runs = await mgr.list_by_thread("thread-1")
    assert len(runs) == 2
    run_ids = {r.run_id for r in runs}
    assert r1.run_id in run_ids
    assert r2.run_id in run_ids

    # r2 should be the in-memory record (has live state)
    r2_record = next(r for r in runs if r.run_id == r2.run_id)
    assert r2_record is r2  # same object reference


@pytest.mark.anyio
async def test_list_by_thread_no_store():
    """Without a store, list_by_thread should only return in-memory runs."""
    mgr = RunManager()
    await mgr.create("thread-1")

    mgr._runs.clear()
    runs = await mgr.list_by_thread("thread-1")
    assert runs == []


@pytest.mark.anyio
async def test_get_returns_in_memory_record(manager_with_store: RunManager):
    """get should return the in-memory record when available."""
    mgr = manager_with_store
    r1 = await mgr.create("thread-1", "agent-1")

    result = await mgr.get(r1.run_id)
    assert result is r1  # same object


@pytest.mark.anyio
async def test_get_falls_back_to_store(manager_with_store: RunManager):
    """get should return a record from the store when not in memory."""
    mgr = manager_with_store
    r1 = await mgr.create("thread-1", "agent-1")
    await _finalize(mgr, r1.run_id, RunStatus.success)

    mgr._runs.clear()

    result = await mgr.get(r1.run_id)
    assert result is not None
    assert result.run_id == r1.run_id
    assert result.status == RunStatus.success
    assert result.thread_id == "thread-1"
    assert result.assistant_id == "agent-1"


@pytest.mark.anyio
async def test_get_falls_back_to_store_with_user_filter():
    """get should honor user_id when reading store-only records."""
    store = MemoryRunStore()
    await store.put("run-1", thread_id="thread-1", user_id="user-1", status="success")
    mgr = RunManager(store=store)

    allowed = await mgr.get("run-1", user_id="user-1")
    denied = await mgr.get("run-1", user_id="user-2")
    assert allowed is not None
    assert denied is None


@pytest.mark.anyio
async def test_get_returns_none_for_unknown(manager_with_store: RunManager):
    """get should return None for a run ID that doesn't exist anywhere."""
    result = await manager_with_store.get("nonexistent-run-id")
    assert result is None


@pytest.mark.anyio
async def test_get_store_failure_is_graceful():
    """If the store raises, get should return None instead of propagating."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    store.get = AsyncMock(side_effect=RuntimeError("db down"))
    mgr = RunManager(store=store)

    result = await mgr.get("some-id")
    assert result is None


@pytest.mark.anyio
async def test_list_by_thread_store_failure_is_graceful():
    """If the store raises, list_by_thread should return only in-memory runs."""
    from unittest.mock import AsyncMock

    store = MemoryRunStore()
    store.list_by_thread = AsyncMock(side_effect=RuntimeError("db down"))
    mgr = RunManager(store=store)

    r1 = await mgr.create("thread-1")
    runs = await mgr.list_by_thread("thread-1")
    assert len(runs) == 1
    assert runs[0].run_id == r1.run_id


@pytest.mark.anyio
async def test_list_by_thread_falls_back_to_store_with_user_filter():
    """list_by_thread should return only the requesting user's store records."""
    store = MemoryRunStore()
    await store.put("run-1", thread_id="thread-1", user_id="user-1", status="success")
    await store.put("run-2", thread_id="thread-1", user_id="user-2", status="success")
    mgr = RunManager(store=store)

    runs = await mgr.list_by_thread("thread-1", user_id="user-1")
    assert [r.run_id for r in runs] == ["run-1"]


@pytest.mark.anyio
async def test_find_by_thread_metadata_hydrates_exact_persisted_run():
    store = MemoryRunStore()
    await store.put(
        "run-match",
        thread_id="thread-1",
        user_id="user-1",
        status="success",
        metadata={"office_message_id": "message-1", "kind": "group"},
    )
    manager = RunManager(store=store)

    record = await manager.find_by_thread_metadata(
        "thread-1",
        {"office_message_id": "message-1", "kind": "group"},
        user_id="user-1",
    )

    assert record is not None
    assert record.run_id == "run-match"
    assert record.store_only is True


# ---------------------------------------------------------------------------
# Per-thread index (thread_id -> run_ids): keeps per-thread queries
# O(runs-in-thread) instead of scanning every in-memory run, and stays
# consistent with ``_runs`` across create / cleanup / rollback.
# ---------------------------------------------------------------------------


class _FailingPutRunStore(MemoryRunStore):
    """Memory run store whose every ``put`` fails (non-retryably)."""

    async def put(self, run_id, **kwargs):
        raise ValueError("simulated persist failure")

@pytest.mark.anyio
async def test_thread_index_scopes_runs_per_thread(manager: RunManager):
    a1 = await manager.create("thread-a")
    a2 = await manager.create("thread-a")
    b1 = await manager.create("thread-b")

    # The index mirrors _runs membership, bucketed by thread.
    assert set(manager._runs_by_thread["thread-a"]) == {a1.run_id, a2.run_id}
    assert set(manager._runs_by_thread["thread-b"]) == {b1.run_id}

    # Per-thread queries return only that thread's runs (no cross-thread leak).
    assert {r.run_id for r in await manager.list_by_thread("thread-a")} == {a1.run_id, a2.run_id}
    assert {r.run_id for r in await manager.list_by_thread("thread-b")} == {b1.run_id}
    assert await manager.list_by_thread("thread-missing") == []


@pytest.mark.anyio
async def test_thread_index_preserves_insertion_order(manager: RunManager):
    # The index is insertion-ordered (dict-as-ordered-set) so list_by_thread
    # keeps the stable tie-breaking the full-scan implementation guaranteed.
    first = await manager.create("thread-a")
    second = await manager.create("thread-a")
    assert list(manager._runs_by_thread["thread-a"]) == [first.run_id, second.run_id]


@pytest.mark.anyio
async def test_thread_index_cleanup_prunes_run_and_empty_bucket(manager: RunManager):
    a1 = await manager.create("thread-a")
    a2 = await manager.create("thread-a")

    await manager.cleanup(a1.run_id, delay=0)
    assert a1.run_id not in manager._runs
    assert set(manager._runs_by_thread["thread-a"]) == {a2.run_id}

    await manager.cleanup(a2.run_id, delay=0)
    # Empty buckets are pruned so the index cannot grow without bound.
    assert "thread-a" not in manager._runs_by_thread
    assert await manager.list_by_thread("thread-a") == []


@pytest.mark.anyio
async def test_has_inflight_reflects_index(manager: RunManager):
    record = await manager.create("thread-a")
    assert await manager.has_inflight("thread-a") is True
    assert await manager.has_inflight("thread-b") is False

    await _finalize(manager, record.run_id, RunStatus.success)
    assert await manager.has_inflight("thread-a") is False


@pytest.mark.anyio
async def test_create_or_reject_inflight_is_thread_scoped(manager: RunManager):
    await manager.create_or_reject("thread-a", multitask_strategy="reject")
    # A different thread is unaffected by thread-a's active run.
    await manager.create_or_reject("thread-b", multitask_strategy="reject")
    # A second active run on the same thread is rejected.
    with pytest.raises(ConflictError):
        await manager.create_or_reject("thread-a", multitask_strategy="reject")


@pytest.mark.anyio
async def test_failed_create_unindexes_run():
    manager = RunManager(store=_FailingPutRunStore())
    with pytest.raises(ValueError):
        await manager.create("thread-a")
    # A rolled-back run must leave no trace in either _runs or the index.
    assert manager._runs == {}
    assert "thread-a" not in manager._runs_by_thread


@pytest.mark.anyio
async def test_failed_create_or_reject_unindexes_run():
    # Symmetric to test_failed_create_unindexes_run: create_or_reject has its own
    # insert + rollback-unindex site, so a persist failure there must also leave
    # neither _runs nor the index holding the rolled-back run. This closes the last
    # mutation path not exercised by an index-consistency test.
    manager = RunManager(store=_FailingPutRunStore())
    with pytest.raises(ValueError):
        await manager.create_or_reject("thread-a", multitask_strategy="reject")
    assert manager._runs == {}
    assert "thread-a" not in manager._runs_by_thread
