# 本文件属于 DeerFlow RunManager 模块：只管理 Run 的权威生命周期、运行任务、取消、恢复和持久化同步。
# 不负责 Office 消息路由、员工 FIFO、群聊保存、员工状态投影或 Agent 业务决策。
# 关联：runs/store 定义持久化边界，Gateway services 启动执行，Office 只查询和投影本模块事实。
"""In-memory run registry with optional persistent RunStore backing."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id
from deerflow.runtime.runs.store.base import RUN_USAGE_FIELDS
from deerflow.utils.time import now_iso as _now_iso

from .schemas import DisconnectMode, RunStatus

if TYPE_CHECKING:
    from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)


def _log_store_warning(message: str, *args: object, error: BaseException) -> None:
    """Log persistence failures without serializing exception payloads."""
    logger.warning(
        f"{message} [failure_type=%s]",
        *args,
        type(error).__name__,
    )

_RETRYABLE_SQLITE_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database is busy",
)

_RETRYABLE_SQLITE_ERROR_CODES = {
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
}

def _is_retryable_persistence_error(exc: BaseException) -> bool:
    """Return True for transient SQLite persistence failures.

    SQLite lock contention normally surfaces through either sqlite3 exceptions
    or SQLAlchemy wrappers.  The short bounded retry here protects run status
    finalization from transient writer pressure without hiding permanent
    failures forever.
    """

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        message = str(current).lower()
        if any(fragment in message for fragment in _RETRYABLE_SQLITE_MESSAGES):
            return True
        if isinstance(current, (sqlite3.OperationalError, sqlite3.DatabaseError)):
            error_code = getattr(current, "sqlite_errorcode", None)
            if error_code in _RETRYABLE_SQLITE_ERROR_CODES:
                return True
        for chained in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(chained, BaseException):
                pending.append(chained)
    return False


@dataclass(frozen=True)
class PersistenceRetryPolicy:
    """Bounded retry policy for short run-store writes."""

    max_attempts: int = 5
    initial_delay: float = 0.05
    max_delay: float = 1.0
    backoff_factor: float = 2.0


class RunPersistenceError(RuntimeError):
    """Raised when a run lifecycle transition cannot be made durable."""

    def __init__(self, operation: str, run_id: str, detail: str) -> None:
        super().__init__(f"{operation} failed for run {run_id}: {detail}")
        self.operation = operation
        self.run_id = run_id


@dataclass
class RunRecord:
    """Mutable record for a single run."""

    run_id: str
    thread_id: str
    assistant_id: str | None
    status: RunStatus
    on_disconnect: DisconnectMode
    user_id: str
    multitask_strategy: str = "reject"
    metadata: dict = field(default_factory=dict)
    kwargs: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    abort_action: str = "interrupt"
    error: str | None = None
    model_name: str | None = None
    store_only: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    # Per-model token breakdown
    token_usage_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    message_count: int = 0
    last_ai_message: str | None = None
    first_human_message: str | None = None
    events_finalized: bool = False
    last_event_seq: int = 0
    finalizing: bool = False
    # Completion hooks may publish messages and admit a successor after the
    # terminal commit. This is presentation state, not an admission lock.
    completion_pending: bool = False


class RunManager:
    """In-memory run registry with optional persistent RunStore backing.

    All mutations are protected by an asyncio lock. When a ``store`` is
    provided, serializable metadata is also persisted to the store so
    that run history survives process restarts.
    """

    def __init__(
        self,
        store: RunStore | None = None,
        *,
        persistence_retry_policy: PersistenceRetryPolicy | None = None,
    ) -> None:
        self._runs: dict[str, RunRecord] = {}
        # Secondary index: thread_id -> insertion-ordered run_id set (a dict is
        # used as an ordered set), maintained in lockstep with ``_runs`` so
        # per-thread queries avoid O(total in-memory runs) full scans while
        # preserving ``_runs`` iteration order (see ``_thread_records_locked``).
        self._runs_by_thread: dict[str, dict[str, None]] = {}
        self._lock = asyncio.Lock()
        self._admission_guard = asyncio.Lock()
        self._admission_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._transition_guard = asyncio.Lock()
        self._transition_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._store = store
        self._persistence_retry_policy = persistence_retry_policy or PersistenceRetryPolicy()

    @asynccontextmanager
    async def _thread_admission(self, thread_id: str) -> AsyncIterator[None]:
        async with self._admission_guard:
            lock, users = self._admission_locks.get(thread_id, (asyncio.Lock(), 0))
            self._admission_locks[thread_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            async with self._admission_guard:
                current = self._admission_locks.get(thread_id)
                if current is None or current[0] is not lock:
                    return
                remaining = current[1] - 1
                if remaining == 0:
                    self._admission_locks.pop(thread_id, None)
                else:
                    self._admission_locks[thread_id] = (lock, remaining)

    @asynccontextmanager
    async def _run_transition(self, run_id: str) -> AsyncIterator[None]:
        async with self._transition_guard:
            lock, users = self._transition_locks.get(run_id, (asyncio.Lock(), 0))
            self._transition_locks[run_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            async with self._transition_guard:
                current = self._transition_locks.get(run_id)
                if current is None or current[0] is not lock:
                    return
                remaining = current[1] - 1
                if remaining == 0:
                    self._transition_locks.pop(run_id, None)
                else:
                    self._transition_locks[run_id] = (lock, remaining)

    def _index_run_locked(self, record: RunRecord) -> None:
        """Register *record* in the thread index. Caller must hold ``self._lock``."""
        self._runs_by_thread.setdefault(record.thread_id, {})[record.run_id] = None

    def _unindex_run_locked(self, run_id: str, thread_id: str) -> None:
        """Drop *run_id* from the thread index. Caller must hold ``self._lock``."""
        bucket = self._runs_by_thread.get(thread_id)
        if bucket is not None:
            bucket.pop(run_id, None)
            if not bucket:
                self._runs_by_thread.pop(thread_id, None)

    def _thread_records_locked(self, thread_id: str) -> list[RunRecord]:
        """Return live in-memory records for *thread_id*. Caller must hold ``self._lock``.

        Uses the ``_runs_by_thread`` index for O(runs-in-thread) lookup instead of
        scanning every in-memory run. Correctness rests on the index and ``_runs``
        being mutated in lockstep under ``self._lock`` (no ``await`` between the two
        writes), so any holder of the lock sees them agree. The ``self._runs.get``
        filter is defense-in-depth, not reconciliation: it drops a stale id still in
        the index but already gone from ``_runs``, yet it cannot recover a run that is
        in ``_runs`` but missing from the index (such a run would be silently
        omitted). It guards only that one direction, should a future refactor ever
        break the lockstep invariant.
        """
        run_ids = self._runs_by_thread.get(thread_id)
        if not run_ids:
            return []
        return [record for run_id in run_ids if (record := self._runs.get(run_id)) is not None]

    @staticmethod
    def _store_put_payload(
        record: RunRecord,
        *,
        status: RunStatus | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "thread_id": record.thread_id,
            "assistant_id": record.assistant_id,
            "status": (status or record.status).value,
            "multitask_strategy": record.multitask_strategy,
            "metadata": record.metadata or {},
            "kwargs": record.kwargs or {},
            "error": error if error is not None else record.error,
            "created_at": record.created_at,
            "model_name": record.model_name,
            "events_finalized": record.events_finalized,
            "last_event_seq": record.last_event_seq,
            "user_id": record.user_id,
        }
        return payload

    async def _call_store_with_retry(
        self,
        operation_name: str,
        run_id: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run a short store operation with bounded retries for SQLite pressure."""
        policy = self._persistence_retry_policy
        attempt = 1
        delay = policy.initial_delay
        while True:
            try:
                return await operation()
            except Exception as exc:
                retryable = _is_retryable_persistence_error(exc)
                if attempt >= policy.max_attempts or not retryable:
                    raise
                _log_store_warning(
                    "Transient persistence failure during %s for run %s (attempt %d/%d); retrying",
                    operation_name,
                    run_id,
                    attempt,
                    policy.max_attempts,
                    error=exc,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                delay = min(policy.max_delay, delay * policy.backoff_factor if delay else policy.initial_delay)
                attempt += 1

    async def _persist_snapshot_to_store(self, run_id: str, payload: dict[str, Any]) -> bool:
        """Best-effort persist a previously captured run snapshot."""
        if self._store is None:
            return True
        try:
            await self._call_store_with_retry(
                "put",
                run_id,
                lambda: self._store.put(run_id, **payload),
            )
            return True
        except Exception as exc:
            _log_store_warning("Failed to persist run %s to store", run_id, error=exc)
            return False

    async def _persist_new_run_to_store(self, record: RunRecord) -> None:
        """Persist a newly created run record to the backing store.

        Initial run creation is part of the run visibility boundary: callers
        should not observe a run in memory unless its backing store row exists.
        Unlike follow-up status/model updates, failures are propagated so the
        caller can treat creation as failed. Rollback is the caller's
        responsibility after inserting the record into ``_runs``.
        """
        if self._store is None:
            return
        await self._call_store_with_retry(
            "put",
            record.run_id,
            lambda: self._store.put(record.run_id, **self._store_put_payload(record)),
        )

    async def _persist_to_store(self, record: RunRecord, *, error: str | None = None) -> bool:
        """Best-effort persist run record to backing store."""
        return await self._persist_snapshot_to_store(
            record.run_id,
            self._store_put_payload(record, error=error),
        )

    async def _persist_status(self, record: RunRecord, status: RunStatus, *, error: str | None = None) -> None:
        """Persist a status transition or raise before memory is mutated."""
        if self._store is None:
            return
        row_recovery_payload = self._store_put_payload(record, status=status, error=error)
        try:
            updated = await self._call_store_with_retry(
                "update_status",
                record.run_id,
                lambda: self._store.update_status(record.run_id, status.value, error=error),
            )
            if updated is False:
                # ``update_status`` is now guarded by ``status IN ('pending','running')``.
                # False can mean either:
                #   (a) the row was never persisted (initial ``put()`` failed) → recreate.
                #   (b) the row is already terminal.
                #       or a local cancel/completion race (``interrupted`` /
                #       ``success``). The log severity branches on which.
                existing = await self._store.get(record.run_id, user_id=record.user_id)
                if existing is not None:
                    existing_status = existing.get("status")
                    if existing_status == status.value:
                        return
                    raise RunPersistenceError(
                        "update_status",
                        record.run_id,
                        f"store already contains terminal status {existing_status!r}",
                    )
                await self._call_store_with_retry(
                    "put",
                    record.run_id,
                    lambda: self._store.put(record.run_id, **row_recovery_payload),
                )
        except RunPersistenceError:
            raise
        except Exception as exc:
            raise RunPersistenceError("update_status", record.run_id, str(exc)) from exc

    @staticmethod
    def _record_from_store(row: dict[str, Any]) -> RunRecord:
        """Build a read-only runtime record from a serialized store row.

        NULL status/on_disconnect columns (e.g. from rows written before those
        columns were added) default to ``pending`` and ``cancel`` respectively.
        """
        return RunRecord(
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            assistant_id=row.get("assistant_id"),
            status=RunStatus(row.get("status") or RunStatus.pending.value),
            on_disconnect=DisconnectMode(row.get("on_disconnect") or DisconnectMode.cancel.value),
            multitask_strategy=row.get("multitask_strategy") or "reject",
            metadata=row.get("metadata") or {},
            kwargs=row.get("kwargs") or {},
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            user_id=row["user_id"],
            error=row.get("error"),
            model_name=row.get("model_name"),
            store_only=True,
            total_input_tokens=row.get("total_input_tokens") or 0,
            total_output_tokens=row.get("total_output_tokens") or 0,
            total_tokens=row.get("total_tokens") or 0,
            llm_call_count=row.get("llm_call_count") or 0,
            lead_agent_tokens=row.get("lead_agent_tokens") or 0,
            subagent_tokens=row.get("subagent_tokens") or 0,
            middleware_tokens=row.get("middleware_tokens") or 0,
            token_usage_by_model=row.get("token_usage_by_model") or {},
            message_count=row.get("message_count") or 0,
            last_ai_message=row.get("last_ai_message"),
            first_human_message=row.get("first_human_message"),
            events_finalized=bool(row.get("events_finalized", False)),
            last_event_seq=int(row.get("last_event_seq") or 0),
        )

    async def finalize_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        last_event_seq: int,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_tokens: int = 0,
        llm_call_count: int = 0,
        lead_agent_tokens: int = 0,
        subagent_tokens: int = 0,
        middleware_tokens: int = 0,
        token_usage_by_model: dict[str, dict[str, int]] | None = None,
        message_count: int = 0,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
        error: str | None = None,
    ) -> None:
        """Atomically expose a durable terminal run and its final event boundary."""
        if status not in {
            RunStatus.success,
            RunStatus.error,
            RunStatus.timeout,
            RunStatus.interrupted,
        }:
            raise ValueError(f"Run final status must be terminal, got {status.value!r}")
        if last_event_seq < 0:
            raise ValueError("last_event_seq must be non-negative")

        completion = {
            "status": status.value,
            "last_event_seq": int(last_event_seq),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "llm_call_count": llm_call_count,
            "lead_agent_tokens": lead_agent_tokens,
            "subagent_tokens": subagent_tokens,
            "middleware_tokens": middleware_tokens,
            "token_usage_by_model": token_usage_by_model,
            "message_count": message_count,
            "last_ai_message": last_ai_message,
            "first_human_message": first_human_message,
            "error": error,
        }

        async with self._run_transition(run_id):
            async with self._lock:
                record = self._runs.get(run_id)
            if record is None:
                raise RunPersistenceError("finalize_run", run_id, "run is not active in this process")
            if record.events_finalized:
                if record.status is status and record.last_event_seq == last_event_seq:
                    return
                raise RunPersistenceError("finalize_run", run_id, "run was already finalized with a different boundary")

            if self._store is not None:
                try:
                    updated = await self._call_store_with_retry(
                        "finalize_run",
                        run_id,
                        lambda: self._store.finalize_run(run_id, **completion),
                    )
                    if updated is False:
                        existing = await self._store.get(run_id)
                        if not (
                            existing is not None
                            and existing.get("status") == status.value
                            and existing.get("events_finalized") is True
                            and int(existing.get("last_event_seq") or 0) == last_event_seq
                        ):
                            raise RunPersistenceError(
                                "finalize_run",
                                run_id,
                                "store rejected the active-to-terminal transition",
                            )
                except RunPersistenceError:
                    raise
                except Exception as exc:
                    raise RunPersistenceError("finalize_run", run_id, str(exc)) from exc

            async with self._lock:
                current = self._runs.get(run_id)
                if current is not None:
                    record = current
                record.status = status
                record.events_finalized = True
                record.last_event_seq = int(last_event_seq)
                record.total_input_tokens = total_input_tokens
                record.total_output_tokens = total_output_tokens
                record.total_tokens = total_tokens
                record.llm_call_count = llm_call_count
                record.lead_agent_tokens = lead_agent_tokens
                record.subagent_tokens = subagent_tokens
                record.middleware_tokens = middleware_tokens
                record.token_usage_by_model = token_usage_by_model or {}
                record.message_count = message_count
                record.last_ai_message = last_ai_message
                record.first_human_message = first_human_message
                record.error = error
                record.updated_at = _now_iso()

    async def update_run_completion(self, run_id: str, **kwargs) -> None:
        """Persist token usage and completion data to the backing store."""
        row_recovery_payload: dict[str, Any] | None = None
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                for key, value in kwargs.items():
                    if key == "status":
                        continue
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
                row_recovery_payload = self._store_put_payload(record, error=kwargs.get("error"))
        if self._store is None:
            return
        try:
            updated = await self._call_store_with_retry(
                "update_run_completion",
                run_id,
                lambda: self._store.update_run_completion(run_id, **kwargs),
            )
            if updated is False:
                if row_recovery_payload is None:
                    logger.warning("Failed to recreate missing run %s for completion persistence", run_id)
                    return
                if not await self._persist_snapshot_to_store(run_id, row_recovery_payload):
                    return
                recovered = await self._call_store_with_retry(
                    "update_run_completion",
                    run_id,
                    lambda: self._store.update_run_completion(run_id, **kwargs),
                )
                if recovered is False:
                    logger.warning("Run completion update for %s affected no rows after row recreation", run_id)
        except Exception as exc:
            _log_store_warning("Failed to persist run completion for %s", run_id, error=exc)

    async def update_run_progress(self, run_id: str, **kwargs) -> None:
        """Persist progress; terminal Runs accept usage only, never reply or lifecycle changes."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                if record.status != RunStatus.running:
                    kwargs = {key: value for key, value in kwargs.items() if key in RUN_USAGE_FIELDS}
                for key, value in kwargs.items():
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                if record.status == RunStatus.running:
                    record.updated_at = _now_iso()
        if self._store is not None:
            try:
                await self._store.update_run_progress(run_id, **kwargs)
            except Exception as exc:
                _log_store_warning("Failed to persist run progress for %s", run_id, error=exc)

    async def create(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
        user_id: str | _AutoSentinel = AUTO,
    ) -> RunRecord:
        """Create and persist a pending run in the local process."""
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.create")
        run_id = str(uuid.uuid4())
        now = _now_iso()
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            status=RunStatus.pending,
            on_disconnect=on_disconnect,
            multitask_strategy=multitask_strategy,
            metadata=metadata or {},
            kwargs=kwargs or {},
            user_id=resolved_user_id,
            created_at=now,
            updated_at=now,
        )
        async with self._thread_admission(thread_id):
            await self._persist_new_run_to_store(record)
            async with self._lock:
                self._runs[run_id] = record
                self._index_run_locked(record)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def get(
        self,
        run_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> RunRecord | None:
        """Return an owner-scoped run record by ID, or ``None``."""
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.get")
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record if record.user_id == resolved_user_id else None
        if self._store is None:
            return None
        try:
            row = await self._store.get(run_id, user_id=resolved_user_id)
        except Exception as exc:
            _log_store_warning("Failed to hydrate run %s from store", run_id, error=exc)
            return None
        # Re-check after store await: a concurrent create() may have inserted the
        # in-memory record while the store call was in flight.
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record if record.user_id == resolved_user_id else None
        if row is None:
            return None
        try:
            return self._record_from_store(row)
        except Exception as exc:
            _log_store_warning("Failed to map store row for run %s", run_id, error=exc)
            return None

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
        limit: int | None = 100,
    ) -> list[RunRecord]:
        """Return runs for a given thread, newest first, at most ``limit`` records.

        In-memory runs take precedence only when the same ``run_id`` exists in both
        memory and the backing store. The merged result is then sorted newest-first
        by ``created_at`` and trimmed to ``limit`` (default 100).

        Args:
            thread_id: The thread ID to filter by.
            user_id: Explicit owner or authenticated request context.
            limit: Maximum number of runs to return.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.list_by_thread")
        async with self._lock:
            memory_records = [
                record
                for record in self._thread_records_locked(thread_id)
                if record.user_id == resolved_user_id
            ]
        if self._store is None:
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        records_by_id = {record.run_id: record for record in memory_records}
        store_limit = limit
        try:
            rows = await self._store.list_by_thread(thread_id, user_id=resolved_user_id, limit=store_limit)
        except Exception as exc:
            if limit is None:
                raise
            _log_store_warning("Failed to hydrate runs for thread %s from store", thread_id, error=exc)
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        for row in rows:
            run_id = row.get("run_id")
            if run_id and run_id not in records_by_id:
                try:
                    records_by_id[run_id] = self._record_from_store(row)
                except Exception as exc:
                    if limit is None:
                        raise
                    _log_store_warning("Failed to map store row for run %s", run_id, error=exc)
        return sorted(records_by_id.values(), key=lambda record: record.created_at, reverse=True)[:limit]

    async def find_by_thread_metadata(
        self,
        thread_id: str,
        metadata: dict[str, Any],
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> RunRecord | None:
        """Return the newest owner-scoped run matching exact metadata fields."""
        if not metadata:
            raise ValueError("metadata filters are required")
        resolved_user_id = resolve_user_id(
            user_id,
            method_name="RunManager.find_by_thread_metadata",
        )
        async with self._lock:
            for record in reversed(self._thread_records_locked(thread_id)):
                if record.user_id != resolved_user_id:
                    continue
                record_metadata = record.metadata if isinstance(record.metadata, dict) else {}
                if all(record_metadata.get(key) == value for key, value in metadata.items()):
                    return record
        if self._store is None:
            return None
        row = await self._store.find_by_thread_metadata(
            thread_id,
            metadata,
            user_id=resolved_user_id,
        )
        return self._record_from_store(row) if row is not None else None

    async def list_successful_regenerate_sources(
        self,
        thread_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> set[str]:
        """Return all source runs superseded by successful regenerations.

        Unlike :meth:`list_by_thread`, this query is intentionally unbounded.
        Current-process records override matching persisted status: a latest
        in-memory failure must not inherit an older successful store snapshot.
        Store failures propagate because supersession filtering is required for
        correct pagination.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.list_successful_regenerate_sources")
        async with self._lock:
            memory_records = [record for record in self._thread_records_locked(thread_id) if record.user_id == resolved_user_id]

        sources = set(await self._store.list_successful_regenerate_sources(thread_id, user_id=resolved_user_id)) if self._store is not None else set()
        # _thread_records_locked preserves the insertion order of the thread
        # index. Applying records oldest-to-newest makes the latest in-memory
        # regeneration attempt authoritative when several attempts reference
        # the same source run (for example, a failed retry after a success).
        for record in memory_records:
            source = record.metadata.get("regenerate_from_run_id")
            if not isinstance(source, str) or not source:
                continue
            sources.discard(source)
            if record.status == RunStatus.success:
                sources.add(source)
        return sources

    async def get_many_by_thread(
        self,
        thread_id: str,
        run_ids: set[str],
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> dict[str, RunRecord]:
        """Batch-load selected thread runs with in-memory records preferred."""
        if not run_ids:
            return {}
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.get_many_by_thread")
        async with self._lock:
            records_by_id = {record.run_id: record for record in self._thread_records_locked(thread_id) if record.run_id in run_ids and (record.user_id == resolved_user_id)}
        if self._store is None:
            return records_by_id

        remaining = run_ids - records_by_id.keys()
        if not remaining:
            return records_by_id
        try:
            rows = await self._store.get_many_by_thread(thread_id, set(remaining), user_id=resolved_user_id)
        except Exception as exc:
            _log_store_warning("Failed to batch-hydrate runs for thread %s", thread_id, error=exc)
            return records_by_id
        for run_id, row in rows.items():
            if run_id in records_by_id:
                continue
            try:
                records_by_id[run_id] = self._record_from_store(row)
            except Exception as exc:
                _log_store_warning("Failed to map store row for run %s", run_id, error=exc)
        return records_by_id

    async def update_metadata(self, run_id: str, patch: dict[str, Any], *, user_id: str) -> None:
        """Persist owner-scoped control metadata before publishing it in memory."""
        async with self._run_transition(run_id):
            record = await self.get(run_id, user_id=user_id)
            if record is None:
                raise FileNotFoundError('Run not found')
            updated = replace(record, metadata={**(record.metadata or {}), **patch}, updated_at=_now_iso())
            await self._persist_new_run_to_store(updated)
            record.metadata = updated.metadata
            record.updated_at = updated.updated_at

    async def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        """Persist an active-state transition.

        Terminal states must include the sealed event boundary and therefore
        can only be published through :meth:`finalize_run`.
        """
        if status not in {RunStatus.pending, RunStatus.running}:
            raise ValueError(
                f"Terminal run status {status.value!r} must use finalize_run"
            )
        async with self._run_transition(run_id):
            async with self._lock:
                record = self._runs.get(run_id)
            if record is None:
                logger.warning("set_status called for unknown run %s", run_id)
                return
            await self._persist_status(record, status, error=error)
            async with self._lock:
                current = self._runs.get(run_id)
                if current is None:
                    return
                current.status = status
                current.updated_at = _now_iso()
                if error is not None:
                    current.error = error
        logger.info("Run %s -> %s", run_id, status.value)

    async def set_finalizing(self, run_id: str, finalizing: bool) -> None:
        """Mark whether a run is performing post-cancel cleanup."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("set_finalizing called for unknown run %s", run_id)
                return
            record.finalizing = finalizing
            record.updated_at = _now_iso()

    async def set_completion_pending(self, run_id: str, pending: bool) -> None:
        """Track in-process completion publication without blocking successors."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                record.completion_pending = pending
                record.updated_at = _now_iso()

    async def wait_for_prior_finalizing(
        self,
        thread_id: str,
        run_id: str,
        *,
        poll_interval: float = 0.01,
    ) -> None:
        """Wait until older same-thread runs have finished post-cancel cleanup."""
        while True:
            async with self._lock:
                found_current = False
                prior_finalizing = False
                for record in self._thread_records_locked(thread_id):
                    if record.run_id == run_id:
                        found_current = True
                        break
                    if record.finalizing:
                        prior_finalizing = True

                if not found_current or not prior_finalizing:
                    return

            await asyncio.sleep(poll_interval)

    async def has_later_run(self, thread_id: str, run_id: str) -> bool:
        """Return whether a newer in-memory run has been admitted for the thread."""
        async with self._lock:
            seen_current = False
            for record in self._thread_records_locked(thread_id):
                if record.run_id == run_id:
                    seen_current = True
                    continue
                if seen_current:
                    return True
        return False

    async def has_later_started_run(self, thread_id: str, run_id: str) -> bool:
        """Return whether a newer same-thread run may have already advanced state."""
        async with self._lock:
            seen_current = False
            for record in self._thread_records_locked(thread_id):
                if record.run_id == run_id:
                    seen_current = True
                    continue
                if seen_current and (record.status != RunStatus.pending or record.finalizing):
                    return True
        return False

    async def _persist_model_name(self, run_id: str, model_name: str | None) -> None:
        """Best-effort persist model_name update to the backing store."""
        if self._store is None:
            return
        try:
            await self._call_store_with_retry(
                "update_model_name",
                run_id,
                lambda: self._store.update_model_name(run_id, model_name),
            )
        except Exception as exc:
            _log_store_warning("Failed to persist model_name update for run %s", run_id, error=exc)

    async def update_model_name(self, run_id: str, model_name: str | None) -> None:
        """Update the model name for a run."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("update_model_name called for unknown run %s", run_id)
                return
            record.model_name = model_name
            record.updated_at = _now_iso()
        await self._persist_model_name(run_id, model_name)
        logger.info("Run %s model_name=%s", run_id, model_name)

    async def cancel(
        self,
        run_id: str,
        *,
        action: str = "interrupt",
        user_id: str | _AutoSentinel = AUTO,
    ) -> CancelOutcome:
        """Signal owner-scoped cancellation without publishing a terminal run state.

        Persisted runs from a previous process can be inspected, but they are
        never treated as remotely cancellable. Startup recovery is responsible
        for moving those stale active rows to ``error``. The worker owns the
        durable terminal transition after its event journal is sealed.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.cancel")
        async with self._run_transition(run_id):
            async with self._lock:
                record = self._runs.get(run_id)
                if record is not None and record.user_id != resolved_user_id:
                    record = None
                if record is not None:
                    if record.status not in (RunStatus.pending, RunStatus.running):
                        return CancelOutcome.not_cancellable
                    record.abort_action = action
                    record.abort_event.set()
                    task_active = record.task is not None and not record.task.done()
                    record.finalizing = task_active
                    if task_active:
                        record.task.cancel()

            if record is not None:
                logger.info("Run %s cancellation requested (action=%s)", run_id, action)
                return CancelOutcome.cancelled

        if self._store is None:
            return CancelOutcome.unknown
        try:
            row = await self._store.get(run_id, user_id=resolved_user_id)
        except Exception as exc:
            _log_store_warning("Failed to fetch run %s from store during cancel", run_id, error=exc)
            return CancelOutcome.unknown
        if row is None:
            return CancelOutcome.unknown
        if row.get("status") in (RunStatus.pending.value, RunStatus.running.value):
            return CancelOutcome.not_active_locally
        return CancelOutcome.not_cancellable

    async def create_or_reject(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
        model_name: str | None = None,
        user_id: str | _AutoSentinel = AUTO,
    ) -> RunRecord:
        """Create a run under the single-process thread concurrency contract.

        ``reject`` refuses a second active run. ``interrupt`` and ``rollback``
        persist the replacement first and only then signal older local workers,
        so a storage failure cannot destroy the task that was already running.
        Only those workers may publish their durable terminal status.
        """
        resolved_user_id = resolve_user_id(
            user_id,
            method_name="RunManager.create_or_reject",
        )
        supported_strategies = ("reject", "interrupt", "rollback")
        if multitask_strategy not in supported_strategies:
            raise UnsupportedStrategyError(
                f"Multitask strategy '{multitask_strategy}' is not yet supported. "
                f"Supported strategies: {', '.join(supported_strategies)}"
            )

        run_id = str(uuid.uuid4())
        now = _now_iso()
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            status=RunStatus.pending,
            on_disconnect=on_disconnect,
            multitask_strategy=multitask_strategy,
            metadata=metadata or {},
            kwargs=kwargs or {},
            user_id=resolved_user_id,
            created_at=now,
            updated_at=now,
            model_name=model_name,
        )
        async with self._thread_admission(thread_id):
            async with self._lock:
                local_inflight = [
                    current
                    for current in self._thread_records_locked(thread_id)
                    if current.user_id == resolved_user_id
                    and (
                        current.status in (RunStatus.pending, RunStatus.running)
                        or current.finalizing
                    )
                ]
                if multitask_strategy == "reject" and local_inflight:
                    raise ConflictError(
                        f"Thread {thread_id} already has an active run"
                    )

            # Do not hold the process-wide registry lock across persistence I/O.
            # The per-thread admission lock still serializes competing creators.
            await self._persist_new_run_to_store(record)

            async with self._lock:
                self._runs[run_id] = record
                self._index_run_locked(record)

                if multitask_strategy in ("interrupt", "rollback"):
                    for current in self._thread_records_locked(thread_id):
                        if (
                            current.run_id == run_id
                            or current.user_id != resolved_user_id
                            or current.finalizing
                            or current.status
                            not in (RunStatus.pending, RunStatus.running)
                        ):
                            continue
                        current.abort_action = multitask_strategy
                        current.abort_event.set()
                        task_active = (
                            current.task is not None
                            and not current.task.done()
                        )
                        current.finalizing = task_active
                        if task_active:
                            current.task.cancel()

        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def restore_inflight_runs(
        self,
        *,
        before: str | None = None,
    ) -> list[RunRecord]:
        """Register durable active rows without changing their identity or state.

        Startup recovery is deliberately split into two phases. This method only
        validates and registers the complete persisted set; the Gateway prepares
        every execution contract before launching any worker. A corrupt row must
        therefore fail startup without leaving a partially live registry.
        """
        if self._store is None:
            return []

        rows = await self._call_store_with_retry(
            "list_inflight",
            "*",
            lambda: self._store.list_inflight(before=before),
        )

        mapped: list[RunRecord] = []
        seen_run_ids: set[str] = set()
        for row in rows:
            record = self._record_from_store(row)
            if record.run_id in seen_run_ids:
                raise ValueError(f"Duplicate persisted inflight run_id: {record.run_id}")
            if record.status not in (RunStatus.pending, RunStatus.running):
                raise ValueError(
                    f"Persisted inflight run {record.run_id} has invalid status {record.status.value!r}"
                )
            if record.events_finalized:
                raise ValueError(
                    f"Persisted inflight run {record.run_id} is already event-finalized"
                )
            seen_run_ids.add(record.run_id)
            mapped.append(record)

        restored: list[RunRecord] = []
        async with self._lock:
            for record in mapped:
                if record.run_id in self._runs:
                    continue
                record.store_only = False
                self._runs[record.run_id] = record
                self._index_run_locked(record)
                restored.append(record)

        if restored:
            logger.info(
                "Registered %d persisted inflight run(s) for checkpoint recovery",
                len(restored),
            )
        return restored

    async def has_inflight(
        self,
        thread_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> bool:
        """Return whether the owner has a pending, running, or finalizing run."""
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.has_inflight")
        async with self._lock:
            return any(
                record.user_id == resolved_user_id
                and (
                    record.status in (RunStatus.pending, RunStatus.running)
                    or record.finalizing
                )
                for record in self._thread_records_locked(thread_id)
            )

    async def forget_thread(
        self,
        thread_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> int:
        """Discard every run record owned by *thread_id*, in memory and in the store.

        Clearing a conversation has to take its runs with it. A surviving in-memory
        record keeps the thread looking busy forever (both ``has_inflight`` and the
        group execution projection read these records), and a surviving store row is
        replayed by ``restore_inflight_runs`` on the next boot. Active runs are signalled
        first so no worker keeps writing to a thread that no longer exists — no
        precondition is checked, by design. Returns the number of runs forgotten.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="RunManager.forget_thread")
        forgotten_ids: set[str] = set()
        async with self._thread_admission(thread_id):
            async with self._lock:
                records = [
                    record
                    for record in self._thread_records_locked(thread_id)
                    if record.user_id == resolved_user_id
                ]
                owners = {record.run_id: record.user_id for record in records}
                forgotten_ids.update(owners)

            # Signal before unindexing: cancel() resolves the run through the in-memory
            # index, so the record must still be present when it runs.
            for run_id, owner_id in owners.items():
                try:
                    await self.cancel(run_id, action="interrupt", user_id=owner_id)
                except Exception:
                    logger.exception("Failed to signal cancellation for run %s", run_id)

            async with self._lock:
                for run_id in owners:
                    self._runs.pop(run_id, None)
                    self._unindex_run_locked(run_id, thread_id)

            if self._store is not None:
                # Completed Runs are intentionally not restored into RunManager on
                # Gateway startup. Read and delete RunStore in bounded batches so a
                # conversation reset also removes that store-only history instead of
                # letting the Client rediscover stale failures after remounting.
                while True:
                    rows = await self._store.list_by_thread(
                        thread_id,
                        user_id=resolved_user_id,
                        limit=100,
                    )
                    run_ids = [
                        str(row.get("run_id") or "").strip()
                        for row in rows
                        if str(row.get("run_id") or "").strip()
                    ]
                    if not run_ids:
                        break
                    for run_id in run_ids:
                        await self._call_store_with_retry(
                            "delete run",
                            run_id,
                            lambda run_id=run_id: self._store.delete(
                                run_id,
                                user_id=resolved_user_id,
                            ),
                        )
                        forgotten_ids.add(run_id)
        return len(forgotten_ids)

    async def cleanup(self, run_id: str, *, delay: float = 300) -> None:
        """Remove a run record after an optional delay."""
        if delay > 0:
            await asyncio.sleep(delay)
        async with self._lock:
            record = self._runs.pop(run_id, None)
            if record is not None:
                self._unindex_run_locked(run_id, record.thread_id)
        logger.debug("Run record %s cleaned up", run_id)

    async def shutdown(self, *, timeout: float = 5.0) -> None:
        """Cancel and bounded-await all in-flight runs on process shutdown.


        Chat runs execute in fire-and-forget background ``asyncio`` tasks that
        write checkpoints through a shared checkpointer. On shutdown the
        checkpointer's resources (e.g. the postgres connection pool owned by the
        gateway's ``AsyncExitStack``) are torn down; if a run task is still
        mid-graph at that point, langgraph's
        ``AsyncPregelLoop._checkpointer_put_after_previous`` runs its
        ``finally: await checkpointer.aput(...)`` against the closed pool. Because
        that put runs in a langgraph-internal task (not on ``run_agent``'s call
        stack), the resulting ``psycopg_pool.PoolClosed`` is not catchable by the
        worker and surfaces as an unhandled exception during ``asyncio.run()``
        shutdown (bytedance/deer-flow issue #3373).

        Draining in-flight runs *before* the checkpointer is closed lets each
        worker flush its final checkpoint, seal its event journal, and publish
        the terminal state through ``finalize_run``. The manager never invents
        a terminal status for a worker that did not finish that protocol. Such
        a row remains active and is finalized by startup recovery on the next
        Gateway process.
        """
        async with self._lock:
            inflight = [record for record in self._runs.values() if record.status in (RunStatus.pending, RunStatus.running) and record.task is not None and not record.task.done()]
            for record in inflight:
                record.abort_action = "interrupt"
                record.abort_event.set()
                record.finalizing = True
                record.task.cancel()  # type: ignore[union-attr]  # filtered above

        if not inflight:
            return

        tasks = [record.task for record in inflight]
        _, pending = await asyncio.wait(tasks, timeout=timeout)

        async with self._lock:
            unfinalized: list[RunRecord] = []
            for record in inflight:
                task = record.task
                if task not in pending and not task.cancelled():
                    task.exception()  # type: ignore[union-attr]  # done & not cancelled
                if not record.events_finalized:
                    unfinalized.append(record)

        if pending:
            logger.warning("Run drain exceeded %.1fs on shutdown; %d run task(s) still active and may race checkpointer teardown", timeout, len(pending))
        if unfinalized:
            logger.warning(
                "%d run(s) left shutdown without a sealed terminal boundary; startup recovery will finalize them",
                len(unfinalized),
            )
        logger.info("Drained %d in-flight run(s) on shutdown (%d settled within %.1fs)", len(inflight), len(inflight) - len(pending), timeout)


class CancelOutcome(StrEnum):
    """Result of a :meth:`RunManager.cancel` call."""

    cancelled = "cancelled"
    not_cancellable = "not_cancellable"
    not_active_locally = "not_active_locally"
    unknown = "unknown"


class ConflictError(Exception):
    """Raised when multitask_strategy=reject and thread has inflight runs."""


class UnsupportedStrategyError(Exception):
    """Raised when a multitask_strategy value is not yet implemented."""
