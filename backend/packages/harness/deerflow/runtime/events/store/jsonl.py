"""Owner-scoped JSONL RunEventStore implementation.

Each run is stored at
``.deer-flow/users/{user_id}/threads/{thread_id}/runs/{run_id}.jsonl``.
File I/O is offloaded with ``asyncio.to_thread``. This backend is intended for
single-process deployments; use ``DbRunEventStore`` for multi-process writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id

logger = logging.getLogger(__name__)
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


class JsonlRunEventStore(RunEventStore):
    def __init__(self, base_dir: str | Path | None = None):
        self._base_dir = Path(base_dir) if base_dir else Path(".deer-flow")
        self._seq_counters: dict[tuple[str, str], int] = {}
        self._write_locks: dict[tuple[str, str], asyncio.Lock] = {}

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        if not value or not _SAFE_ID_PATTERN.match(value):
            raise ValueError(f"Invalid {label}: must be alphanumeric/dash/underscore, got {value!r}")
        return value

    def _key(self, user_id: str, thread_id: str) -> tuple[str, str]:
        return (
            self._validate_id(user_id, "user_id"),
            self._validate_id(thread_id, "thread_id"),
        )

    def _get_write_lock(self, user_id: str, thread_id: str) -> asyncio.Lock:
        return self._write_locks.setdefault(self._key(user_id, thread_id), asyncio.Lock())

    def _thread_dir(self, user_id: str, thread_id: str) -> Path:
        validated_user_id, validated_thread_id = self._key(user_id, thread_id)
        return self._base_dir / "users" / validated_user_id / "threads" / validated_thread_id / "runs"

    def _run_file(self, user_id: str, thread_id: str, run_id: str) -> Path:
        self._validate_id(run_id, "run_id")
        return self._thread_dir(user_id, thread_id) / f"{run_id}.jsonl"

    def _next_seq(self, user_id: str, thread_id: str) -> int:
        key = self._key(user_id, thread_id)
        next_value = self._seq_counters.get(key, 0) + 1
        self._seq_counters[key] = next_value
        return next_value

    def _compute_max_seq(self, user_id: str, thread_id: str) -> int:
        max_seq = 0
        thread_dir = self._thread_dir(user_id, thread_id)
        if not thread_dir.exists():
            return max_seq
        for path in thread_dir.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    max_seq = max(max_seq, int(record.get("seq", 0)))
                except (json.JSONDecodeError, TypeError, ValueError):
                    logger.debug("Skipping malformed JSONL line in %s", path)
        return max_seq

    async def _ensure_seq_loaded(self, user_id: str, thread_id: str) -> None:
        key = self._key(user_id, thread_id)
        if key not in self._seq_counters:
            self._seq_counters[key] = await asyncio.to_thread(
                self._compute_max_seq,
                user_id,
                thread_id,
            )

    def _read_thread_events(self, user_id: str, thread_id: str) -> list[dict]:
        events: list[dict] = []
        thread_dir = self._thread_dir(user_id, thread_id)
        if not thread_dir.exists():
            return events
        for path in sorted(thread_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed JSONL line in %s", path)
        events.sort(key=lambda event: event.get("seq", 0))
        return events

    def _read_run_events(self, user_id: str, thread_id: str, run_id: str) -> list[dict]:
        path = self._run_file(user_id, thread_id, run_id)
        if not path.exists():
            return []
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line in %s", path)
        events.sort(key=lambda event: event.get("seq", 0))
        return events

    def _append_records(self, path: Path, records: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(record, default=str, ensure_ascii=False) + "\n" for record in records)
        with path.open("a", encoding="utf-8") as file:
            file.write(payload)

    def _delete_thread_files(self, user_id: str, thread_id: str) -> None:
        thread_dir = self._thread_dir(user_id, thread_id)
        if not thread_dir.exists():
            return
        for path in thread_dir.glob("*.jsonl"):
            path.unlink()

    def _delete_run_file(self, user_id: str, thread_id: str, run_id: str) -> None:
        path = self._run_file(user_id, thread_id, run_id)
        if path.exists():
            path.unlink()

    async def put(
        self,
        *,
        thread_id,
        run_id,
        event_type,
        category,
        content="",
        metadata=None,
        created_at=None,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="JsonlRunEventStore.put")
        async with self._get_write_lock(resolved_user_id, thread_id):
            await self._ensure_seq_loaded(resolved_user_id, thread_id)
            record = {
                "thread_id": thread_id,
                "run_id": run_id,
                "user_id": resolved_user_id,
                "event_type": event_type,
                "category": category,
                "content": content,
                "metadata": metadata or {},
                "seq": self._next_seq(resolved_user_id, thread_id),
                "created_at": created_at or datetime.now(UTC).isoformat(),
            }
            path = self._run_file(resolved_user_id, thread_id, run_id)
            await asyncio.to_thread(self._append_records, path, [record])
            return record

    async def put_batch(
        self,
        events,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        if not events:
            return []
        resolved_user_id = resolve_user_id(user_id, method_name="JsonlRunEventStore.put_batch")
        thread_ids = {event["thread_id"] for event in events}
        if len(thread_ids) != 1:
            raise ValueError(f"put_batch requires one thread; got {thread_ids!r}")
        thread_id = events[0]["thread_id"]
        async with self._get_write_lock(resolved_user_id, thread_id):
            await self._ensure_seq_loaded(resolved_user_id, thread_id)
            records: list[dict[str, Any]] = []
            run_ids = {event["run_id"] for event in events}
            if len(run_ids) != 1:
                raise ValueError(f"put_batch requires one run; got {run_ids!r}")
            for event in events:
                records.append(
                    {
                        "thread_id": thread_id,
                        "run_id": event["run_id"],
                        "user_id": resolved_user_id,
                        "event_type": event["event_type"],
                        "category": event["category"],
                        "content": event.get("content", ""),
                        "metadata": event.get("metadata") or {},
                        "seq": self._next_seq(resolved_user_id, thread_id),
                        "created_at": event.get("created_at") or datetime.now(UTC).isoformat(),
                    }
                )
            path = self._run_file(resolved_user_id, thread_id, events[0]["run_id"])
            await asyncio.to_thread(self._append_records, path, records)
            return records

    async def list_messages(self, thread_id, *, limit=50, before_seq=None, after_seq=None, user_id: str | _AutoSentinel = AUTO):
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.list_messages")
        messages = [event for event in await asyncio.to_thread(self._read_thread_events, owner, thread_id) if event.get("category") == "message"]
        if before_seq is not None:
            messages = [event for event in messages if event["seq"] < before_seq]
            return messages[-limit:]
        if after_seq is not None:
            messages = [event for event in messages if event["seq"] > after_seq]
            return messages[:limit]
        return messages[-limit:]

    async def list_events(self, thread_id, run_id, *, event_types=None, task_id=None, limit=500, after_seq=None, user_id: str | _AutoSentinel = AUTO):
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.list_events")
        events = await asyncio.to_thread(self._read_run_events, owner, thread_id, run_id)
        if event_types is not None:
            events = [event for event in events if event.get("event_type") in event_types]
        if task_id is not None:
            events = [event for event in events if (event.get("metadata") or {}).get("task_id") == task_id]
        if after_seq is not None:
            events = [event for event in events if event.get("seq", 0) > after_seq]
        return events[:limit]

    async def list_messages_by_run(self, thread_id, run_id, *, limit=50, before_seq=None, after_seq=None, user_id: str | _AutoSentinel = AUTO):
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.list_messages_by_run")
        messages = [event for event in await asyncio.to_thread(self._read_run_events, owner, thread_id, run_id) if event.get("category") == "message"]
        if before_seq is not None:
            messages = [event for event in messages if event.get("seq", 0) < before_seq]
        if after_seq is not None:
            messages = [event for event in messages if event.get("seq", 0) > after_seq]
            return messages[:limit]
        return messages[-limit:]

    async def get_last_visible_ai_seq_by_run(self, thread_id, run_ids, *, user_id: str | _AutoSentinel = AUTO):
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.get_last_visible_ai_seq_by_run")
        result: dict[str, int] = {}
        for run_id in run_ids:
            for event in reversed(await asyncio.to_thread(self._read_run_events, owner, thread_id, run_id)):
                caller = str((event.get("metadata") or {}).get("caller", ""))
                if event.get("category") == "message" and event.get("event_type") == "llm.ai.response" and not caller.startswith("middleware:"):
                    result[run_id] = event["seq"]
                    break
        return result

    async def get_last_event_seq(self, thread_id: str, run_id: str, *, user_id: str | _AutoSentinel = AUTO) -> int:
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.get_last_event_seq")
        events = await asyncio.to_thread(self._read_run_events, owner, thread_id, run_id)
        return int(events[-1].get("seq", 0)) if events else 0

    async def count_messages(self, thread_id, *, user_id: str | _AutoSentinel = AUTO):
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.count_messages")
        events = await asyncio.to_thread(self._read_thread_events, owner, thread_id)
        return sum(1 for event in events if event.get("category") == "message")

    async def delete_by_thread(self, thread_id, *, user_id: str | _AutoSentinel = AUTO):
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.delete_by_thread")
        key = self._key(owner, thread_id)
        async with self._get_write_lock(owner, thread_id):
            events = await asyncio.to_thread(self._read_thread_events, owner, thread_id)
            await asyncio.to_thread(self._delete_thread_files, owner, thread_id)
            self._seq_counters.pop(key, None)
            self._write_locks.pop(key, None)
            return len(events)

    async def delete_by_run(self, thread_id, run_id, *, user_id: str | _AutoSentinel = AUTO):
        owner = resolve_user_id(user_id, method_name="JsonlRunEventStore.delete_by_run")
        async with self._get_write_lock(owner, thread_id):
            events = await asyncio.to_thread(self._read_run_events, owner, thread_id, run_id)
            await asyncio.to_thread(self._delete_run_file, owner, thread_id, run_id)
            return len(events)
