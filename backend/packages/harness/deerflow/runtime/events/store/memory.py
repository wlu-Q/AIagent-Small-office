"""In-memory RunEventStore implementation.

Thread-safe for single-process async usage (all mutations happen on one event
loop). Every read and mutation is owner-scoped.
"""

from __future__ import annotations

import bisect
from datetime import UTC, datetime

from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id


class MemoryRunEventStore(RunEventStore):
    def __init__(self) -> None:
        self._events: dict[str, list[dict]] = {}
        self._messages: dict[str, list[dict]] = {}
        self._events_by_run: dict[str, dict[str, list[dict]]] = {}
        self._messages_by_run: dict[str, dict[str, list[dict]]] = {}
        self._seq_counters: dict[str, int] = {}

    def _next_seq(self, thread_id: str) -> int:
        next_value = self._seq_counters.get(thread_id, 0) + 1
        self._seq_counters[thread_id] = next_value
        return next_value

    def _put_one(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_type: str,
        category: str,
        user_id: str,
        content: str | dict = "",
        metadata: dict | None = None,
        created_at: str | None = None,
    ) -> dict:
        record = {
            "thread_id": thread_id,
            "run_id": run_id,
            "user_id": user_id,
            "event_type": event_type,
            "category": category,
            "content": content,
            "metadata": metadata or {},
            "seq": self._next_seq(thread_id),
            "created_at": created_at or datetime.now(UTC).isoformat(),
        }
        self._events.setdefault(thread_id, []).append(record)
        self._events_by_run.setdefault(thread_id, {}).setdefault(run_id, []).append(record)
        if category == "message":
            self._messages.setdefault(thread_id, []).append(record)
            self._messages_by_run.setdefault(thread_id, {}).setdefault(run_id, []).append(record)
        return record

    def _replace_thread_events(self, thread_id: str, events: list[dict]) -> None:
        if not events:
            self._events.pop(thread_id, None)
            self._messages.pop(thread_id, None)
            self._events_by_run.pop(thread_id, None)
            self._messages_by_run.pop(thread_id, None)
            self._seq_counters.pop(thread_id, None)
            return

        self._events[thread_id] = events
        messages = [event for event in events if event.get("category") == "message"]
        self._messages[thread_id] = messages
        events_by_run: dict[str, list[dict]] = {}
        messages_by_run: dict[str, list[dict]] = {}
        for event in events:
            run_id = event["run_id"]
            events_by_run.setdefault(run_id, []).append(event)
            if event.get("category") == "message":
                messages_by_run.setdefault(run_id, []).append(event)
        self._events_by_run[thread_id] = events_by_run
        self._messages_by_run[thread_id] = messages_by_run

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
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.put")
        return self._put_one(
            thread_id=thread_id,
            run_id=run_id,
            user_id=resolved_user_id,
            event_type=event_type,
            category=category,
            content=content,
            metadata=metadata,
            created_at=created_at,
        )

    async def put_batch(
        self,
        events,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.put_batch")
        return [self._put_one(**event, user_id=resolved_user_id) for event in events]

    async def list_messages(
        self,
        thread_id,
        *,
        limit=50,
        before_seq=None,
        after_seq=None,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.list_messages")
        messages = [event for event in self._messages.get(thread_id, []) if event.get("user_id") == resolved_user_id]
        if before_seq is not None:
            hi = bisect.bisect_left(messages, before_seq, key=lambda event: event["seq"])
            return messages[max(0, hi - limit) : hi]
        if after_seq is not None:
            lo = bisect.bisect_right(messages, after_seq, key=lambda event: event["seq"])
            return messages[lo : lo + limit]
        return messages[-limit:]

    async def list_events(
        self,
        thread_id,
        run_id,
        *,
        event_types=None,
        task_id=None,
        limit=500,
        after_seq=None,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.list_events")
        events = [
            event
            for event in self._events_by_run.get(thread_id, {}).get(run_id, [])
            if event.get("user_id") == resolved_user_id
        ]
        if event_types is not None:
            events = [event for event in events if event.get("event_type") in event_types]
        if task_id is not None:
            events = [event for event in events if (event.get("metadata") or {}).get("task_id") == task_id]
        if after_seq is not None:
            events = [event for event in events if event.get("seq", 0) > after_seq]
        return events[:limit]

    async def list_messages_by_run(
        self,
        thread_id,
        run_id,
        *,
        limit=50,
        before_seq=None,
        after_seq=None,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.list_messages_by_run")
        messages = [
            event
            for event in self._messages_by_run.get(thread_id, {}).get(run_id, [])
            if event.get("user_id") == resolved_user_id
        ]
        lo = 0 if after_seq is None else bisect.bisect_right(messages, after_seq, key=lambda event: event["seq"])
        hi = len(messages) if before_seq is None else bisect.bisect_left(messages, before_seq, key=lambda event: event["seq"])
        window = messages[lo:hi]
        return window[:limit] if after_seq is not None else window[-limit:]

    async def get_last_visible_ai_seq_by_run(
        self,
        thread_id,
        run_ids,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.get_last_visible_ai_seq_by_run")
        result: dict[str, int] = {}
        messages_by_run = self._messages_by_run.get(thread_id, {})
        for run_id in run_ids:
            for event in reversed(messages_by_run.get(run_id, [])):
                if event.get("user_id") != resolved_user_id:
                    continue
                caller = str((event.get("metadata") or {}).get("caller", ""))
                if (
                    event.get("category") == "message"
                    and event.get("event_type") == "llm.ai.response"
                    and not caller.startswith("middleware:")
                ):
                    result[run_id] = event["seq"]
                    break
        return result

    async def get_last_event_seq(
        self,
        thread_id: str,
        run_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> int:
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.get_last_event_seq")
        events = [
            event
            for event in self._events_by_run.get(thread_id, {}).get(run_id, [])
            if event.get("user_id") == resolved_user_id
        ]
        return int(events[-1]["seq"]) if events else 0

    async def count_messages(
        self,
        thread_id,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.count_messages")
        return sum(1 for event in self._messages.get(thread_id, []) if event.get("user_id") == resolved_user_id)

    async def delete_by_thread(
        self,
        thread_id,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.delete_by_thread")
        all_events = self._events.get(thread_id, [])
        remaining = [event for event in all_events if event.get("user_id") != resolved_user_id]
        removed = len(all_events) - len(remaining)
        self._replace_thread_events(thread_id, remaining)
        return removed

    async def delete_by_run(
        self,
        thread_id,
        run_id,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryRunEventStore.delete_by_run")
        all_events = self._events.get(thread_id, [])
        remaining = [
            event
            for event in all_events
            if not (event.get("run_id") == run_id and event.get("user_id") == resolved_user_id)
        ]
        removed = len(all_events) - len(remaining)
        self._replace_thread_events(thread_id, remaining)
        return removed
