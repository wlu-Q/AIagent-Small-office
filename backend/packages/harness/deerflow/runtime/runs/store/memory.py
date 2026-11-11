# 本文件属于 DeerFlow 内存 RunStore 模块：只为开发和测试保存用户隔离的 Run 元数据。
# 不负责 Run 执行、持久化恢复、Office 员工状态、消息排队、Checkpoint 或 Event。
# 关联：base.py 定义存储合同，manager.py 在内存配置下使用本实现。
"""In-memory RunStore. Used when database.backend=memory (default) and in tests.

Equivalent to the original RunManager._runs dict behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from deerflow.runtime.runs.store.base import RUN_USAGE_FIELDS, RunStore
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id


class MemoryRunStore(RunStore):
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        # Secondary index: thread_id -> insertion-ordered run_id set (a dict is
        # used as an ordered set), maintained in lockstep with ``_runs`` so
        # per-thread queries avoid O(total in-memory runs) full scans. Mirrors
        # the index ``RunManager`` keeps over its own in-memory records.
        self._runs_by_thread: dict[str, dict[str, None]] = {}

    def _index_run(self, run_id: str, thread_id: str) -> None:
        """Register *run_id* under *thread_id* in the secondary index."""
        self._runs_by_thread.setdefault(thread_id, {})[run_id] = None

    def _unindex_run(self, run_id: str, thread_id: str) -> None:
        """Drop *run_id* from the *thread_id* bucket, removing the bucket when empty."""
        bucket = self._runs_by_thread.get(thread_id)
        if bucket is not None:
            bucket.pop(run_id, None)
            if not bucket:
                self._runs_by_thread.pop(thread_id, None)

    async def put(
        self,
        run_id,
        *,
        thread_id,
        assistant_id=None,
        user_id: str | _AutoSentinel = AUTO,
        model_name=None,
        status="pending",
        multitask_strategy="reject",
        metadata=None,
        kwargs=None,
        error=None,
        created_at=None,
        events_finalized=False,
        last_event_seq=0,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunStore.put")
        now = datetime.now(UTC).isoformat()
        self._runs[run_id] = {
            **self._runs.get(run_id, {}),
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": resolved_user_id,
            "model_name": model_name,
            "status": status,
            "multitask_strategy": multitask_strategy,
            "metadata": metadata or {},
            "kwargs": kwargs or {},
            "error": error,
            "events_finalized": bool(events_finalized),
            "last_event_seq": int(last_event_seq),
            "created_at": created_at or now,
            "updated_at": now,
        }
        self._index_run(run_id, thread_id)

    async def get(self, run_id, *, user_id: str | _AutoSentinel = AUTO):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunStore.get")
        run = self._runs.get(run_id)
        if run is None or run.get("user_id") != resolved_user_id:
            return None
        return run

    async def list_by_thread(self, thread_id, *, user_id: str | _AutoSentinel = AUTO, limit=100):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunStore.list_by_thread")
        # Use the thread index for an O(runs-in-thread) lookup instead of
        # scanning every run. ``self._runs.get`` is defense-in-depth: it drops a
        # stale id still in the index but already gone from ``_runs``.
        run_ids = self._runs_by_thread.get(thread_id)
        if not run_ids:
            return []
        indexed_results = [
            (position, run)
            for position, run_id in enumerate(run_ids)
            if (run := self._runs.get(run_id)) is not None and run.get("user_id") == resolved_user_id
        ]
        indexed_results.sort(key=lambda item: (item[1]["created_at"], item[0]), reverse=True)
        return [run for _, run in indexed_results[:limit]]

    async def find_by_thread_metadata(
        self,
        thread_id,
        metadata,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(
            user_id,
            method_name="MemoryRunStore.find_by_thread_metadata",
        )
        run_ids = self._runs_by_thread.get(thread_id) or ()
        for run_id in reversed(run_ids):
            run = self._runs.get(run_id)
            if run is None or run.get("user_id") != resolved_user_id:
                continue
            run_metadata = run.get("metadata") or {}
            if all(run_metadata.get(key) == value for key, value in metadata.items()):
                return run
        return None

    async def list_successful_regenerate_sources(
        self,
        thread_id,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(
            user_id,
            method_name="MemoryRunStore.list_successful_regenerate_sources",
        )
        run_ids = self._runs_by_thread.get(thread_id) or ()
        sources: set[str] = set()
        for run_id in run_ids:
            run = self._runs.get(run_id)
            if run is None or run.get("status") != "success":
                continue
            if run.get("user_id") != resolved_user_id:
                continue
            source = (run.get("metadata") or {}).get("regenerate_from_run_id")
            if isinstance(source, str) and source:
                sources.add(source)
        return sources

    async def get_many_by_thread(
        self,
        thread_id,
        run_ids,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(
            user_id,
            method_name="MemoryRunStore.get_many_by_thread",
        )
        thread_run_ids = self._runs_by_thread.get(thread_id) or ()
        return {
            run_id: run
            for run_id in thread_run_ids
            if run_id in run_ids
            and (run := self._runs.get(run_id)) is not None
            and run.get("user_id") == resolved_user_id
        }

    async def update_status(self, run_id, status, *, error=None):
        run = self._runs.get(run_id)
        if run is None:
            return False
        # Guard: only transition rows that are still active. ``interrupted``
        # is included for the rollback path (``interrupted → error`` finalize).
        if run["status"] not in ("pending", "running", "interrupted"):
            return False
        run["status"] = status
        if error is not None:
            run["error"] = error
        run["updated_at"] = datetime.now(UTC).isoformat()
        return True

    async def update_model_name(self, run_id, model_name):
        if run_id in self._runs:
            self._runs[run_id]["model_name"] = model_name
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def delete(self, run_id, *, user_id: str | _AutoSentinel = AUTO):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunStore.delete")
        run = self._runs.get(run_id)
        if run is None or run.get("user_id") != resolved_user_id:
            return
        self._runs.pop(run_id, None)
        self._unindex_run(run_id, run["thread_id"])

    async def update_run_completion(self, run_id, *, status, **kwargs):
        if run_id in self._runs:
            self._runs[run_id]["status"] = status
            for key, value in kwargs.items():
                if value is not None:
                    self._runs[run_id][key] = value
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()
            return True
        return False

    async def finalize_run(self, run_id, *, status, last_event_seq, **kwargs):
        run = self._runs.get(run_id)
        if run is None:
            return False
        if run["status"] not in ("pending", "running", "interrupted"):
            return False
        run["status"] = status
        run["events_finalized"] = True
        run["last_event_seq"] = int(last_event_seq)
        for key, value in kwargs.items():
            if value is not None:
                run[key] = value
        run["updated_at"] = datetime.now(UTC).isoformat()
        return True

    async def update_run_progress(self, run_id, **kwargs):
        if run_id in self._runs:
            running = self._runs[run_id].get('status') == 'running'
            for key, value in kwargs.items():
                if value is not None and (running or key in RUN_USAGE_FIELDS):
                    self._runs[run_id][key] = value
            if running:
                self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def list_pending(self, *, before=None):
        now = before or datetime.now(UTC).isoformat()
        results = [r for r in self._runs.values() if r["status"] == "pending" and r["created_at"] <= now]
        results.sort(key=lambda r: r["created_at"])
        return results

    async def list_inflight(self, *, before=None):
        now = before or datetime.now(UTC).isoformat()
        results = [r for r in self._runs.values() if r["status"] in ("pending", "running") and r["created_at"] <= now]
        results.sort(key=lambda r: r["created_at"])
        return results

    async def aggregate_tokens_by_thread(self, thread_id: str, *, include_active: bool = False) -> dict[str, Any]:
        statuses = ("success", "error", "running") if include_active else ("success", "error")
        # Use the thread index for an O(runs-in-thread) lookup instead of
        # scanning every run in the process (mirrors ``list_by_thread``).
        run_ids = self._runs_by_thread.get(thread_id) or ()
        completed = [run for run_id in run_ids if (run := self._runs.get(run_id)) is not None and run.get("status") in statuses]
        by_model: dict[str, dict] = {}
        for r in completed:
            for model, usage in (r.get("token_usage_by_model") or {}).items():
                entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
                entry["tokens"] += usage.get("total_tokens", 0)
                entry["runs"] += 1
        return {
            "total_tokens": sum(r.get("total_tokens", 0) for r in completed),
            "total_input_tokens": sum(r.get("total_input_tokens", 0) for r in completed),
            "total_output_tokens": sum(r.get("total_output_tokens", 0) for r in completed),
            "total_runs": len(completed),
            "by_model": by_model,
            "by_caller": {
                "lead_agent": sum(r.get("lead_agent_tokens", 0) for r in completed),
                "subagent": sum(r.get("subagent_tokens", 0) for r in completed),
                "middleware": sum(r.get("middleware_tokens", 0) for r in completed),
            },
        }
