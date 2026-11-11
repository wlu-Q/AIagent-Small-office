# 本文件属于 DeerFlow Run Worker 模块：只负责执行原生 Agent Run、持久化生命周期并发布 Runtime Event。
# 不负责 Office 消息路由、员工 FIFO、能力选择、公共群聊存储或维护第二套 Run 状态。
# 关联：RunManager 提供 Run 事实，Checkpointer 保存图状态，OfficeRunProjection 通过生命周期钩子接收薄投影。
"""Background agent execution.

Runs an agent graph inside an ``asyncio.Task``, publishing events to
a :class:`StreamBridge` as they are produced.

Uses ``graph.astream(stream_mode=[...])`` which gives correct full-state
snapshots for ``values`` mode, proper ``{node: writes}`` for ``updates``,
and ``(chunk, metadata)`` tuples for ``messages`` mode.

Note: ``events`` mode is not supported through the gateway — it requires
``graph.astream_events()`` which cannot simultaneously produce ``values``
snapshots.  The JS open-source LangGraph API server works around this via
internal checkpoint callbacks that are not exposed in the Python public API.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import os
import threading
import weakref
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal, cast

from langgraph.checkpoint.base import empty_checkpoint

from deerflow.agents.goal_state import GoalEvaluation, GoalState
from deerflow.config.app_config import AppConfig
from deerflow.runtime.context_keys import (
    CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY,
    OFFICE_PROJECT_PATH_CONTEXT_KEY,
    RUNTIME_BOUND_TOOL_NAMES_METADATA_KEY,
    RUNTIME_MODEL_ID_METADATA_KEY,
)
from deerflow.runtime.goal import (
    DEFAULT_MAX_GOAL_CONTINUATIONS,
    DEFAULT_MAX_NO_PROGRESS_CONTINUATIONS,
    GoalWriteConflict,
    _call_checkpointer_method,
    _is_visible_message,
    _message_type,
    attach_goal_evaluation,
    compute_no_progress_count,
    create_goal_evaluator_model,
    evaluate_goal_completion,
    goal_thread_lock,
    latest_visible_assistant_signature,
    make_goal_continuation_message,
    read_thread_goal,
    should_continue_goal,
    visible_conversation_signature,
    write_thread_goal,
)
from deerflow.runtime.runs.execution import CHECKPOINT_RUN_ID_METADATA_KEY
from deerflow.runtime.security import (
    collect_secret_values,
    redact_exception_traceback,
    redact_text,
    redact_value,
    redacting_checkpointer,
)
from deerflow.runtime.serialization import serialize
from deerflow.runtime.stream_bridge import StreamBridge
from deerflow.runtime.user_context import (
    get_effective_user_id,
    reset_current_user,
    resolve_runtime_user_id,
    set_current_user,
)
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY, get_current_trace_id, normalize_trace_id
from deerflow.tracing import inject_langfuse_metadata
from deerflow.utils.messages import message_to_text
from deerflow.workspace_changes import capture_workspace_snapshot, record_workspace_changes
from deerflow.workspace_changes.types import WorkspaceSnapshot

from .manager import RunManager, RunRecord
from .naming import resolve_root_run_name
from .schemas import RunStatus

logger = logging.getLogger(__name__)

_checkpoint_locks_guard = threading.Lock()
_checkpoint_locks_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()


def _log_redacted_warning(
    message: str,
    *args: Any,
    error: BaseException,
    secret_values: set[str] | frozenset[str],
) -> None:
    """Log a run-scoped exception without delegating raw traceback formatting."""
    logger.warning(
        f"{message} | %s",
        *args,
        redact_exception_traceback(error, secret_values),
    )


@asynccontextmanager
async def _checkpoint_thread_lock(thread_id: str) -> AsyncIterator[None]:
    """Serialize checkpoint mutations for one thread without blocking goal commands."""
    loop = asyncio.get_running_loop()
    with _checkpoint_locks_guard:
        locks = _checkpoint_locks_by_loop.get(loop)
        if locks is None:
            locks = {}
            _checkpoint_locks_by_loop[loop] = locks
        lock = locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[thread_id] = lock

    async with lock:
        yield


# Valid stream_mode values for LangGraph's graph.astream()
_VALID_LG_MODES = {"values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"}



def _build_runtime_context(
    thread_id: str,
    run_id: str,
    caller_context: Any | None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """Build the dict that becomes ``ToolRuntime.context`` for the run.

    Always includes ``thread_id`` and ``run_id``. Additional keys from the caller's
    ``config['context']`` (e.g. ``agent_name`` for the bootstrap flow — issue #2677)
    are merged in but never override ``thread_id``/``run_id``. The resolved
    ``AppConfig`` is added by the worker so tools can consume it without ambient
    global lookups.

    langgraph 1.1+ surfaces this as ``runtime.context`` via the parent runtime stored
    under ``config['configurable']['__pregel_runtime']`` — see
    ``langgraph.pregel.main`` where ``parent_runtime.merge(...)`` is invoked.
    """
    runtime_ctx: dict[str, Any] = {"thread_id": thread_id, "run_id": run_id}
    if isinstance(caller_context, dict):
        for key, value in caller_context.items():
            if key == CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY:
                continue
            runtime_ctx.setdefault(key, value)
    if app_config is not None:
        runtime_ctx["app_config"] = app_config
    return runtime_ctx


def _with_authoritative_thread_data(
    graph_input: Any,
    runtime_context: Mapping[str, Any],
) -> Any:
    if not isinstance(graph_input, Mapping) or 'thread_data' not in graph_input:
        return graph_input

    from langgraph.runtime import Runtime

    from deerflow.agents.middlewares.thread_data_middleware import ThreadDataMiddleware

    resolved = ThreadDataMiddleware(lazy_init=True).before_agent(
        {},
        Runtime(context=dict(runtime_context)),
    )
    thread_data = resolved.get('thread_data') if isinstance(resolved, Mapping) else None
    if not isinstance(thread_data, Mapping):
        raise RuntimeError('ThreadDataMiddleware did not resolve authoritative thread data')
    return {**graph_input, 'thread_data': dict(thread_data)}


@dataclass(frozen=True)
class RunContext:
    """Infrastructure dependencies for a single agent run.

    Groups checkpointer, store, and persistence-related singletons so that
    ``run_agent`` (and any future callers) receive one object instead of a
    growing list of keyword arguments.
    """

    checkpointer: Any
    store: Any | None = field(default=None)
    event_store: Any | None = field(default=None)
    run_events_config: Any | None = field(default=None)
    thread_store: Any | None = field(default=None)
    app_config: AppConfig | None = field(default=None)
    on_run_starting: Any | None = field(default=None)
    on_run_started: Any | None = field(default=None)
    on_run_input_checkpointed: Any | None = field(default=None)
    on_run_completed: Any | None = field(default=None)


class _AfterFirstSuccessfulCheckpoint:
    """Notify one lifecycle hook only after the delegated checkpoint write succeeds."""

    def __init__(self, delegate: Any, on_persisted: Any) -> None:
        self._delegate = delegate
        self._on_persisted = on_persisted
        self._notified = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._delegate.aput(*args, **kwargs)
        if not self._notified:
            self._notified = True
            await self._on_persisted()
        return result


class _RunOwnedCheckpointer:
    """Keep every checkpoint written by a Run owned by that Run.

    LangGraph can carry metadata forward when forking a checkpoint. The
    runtime Run ID is the ownership boundary used by resume validation, so it
    must be replaced on each new checkpoint write.
    """

    def __init__(self, delegate: Any, run_id: str) -> None:
        self._delegate = delegate
        self._run_id = run_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def _owned_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        owned = dict(metadata)
        owned[CHECKPOINT_RUN_ID_METADATA_KEY] = self._run_id
        return owned

    def put(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Mapping[str, Any],
        new_versions: Any,
    ) -> Any:
        return self._delegate.put(
            config,
            checkpoint,
            self._owned_metadata(metadata),
            new_versions,
        )

    async def aput(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Mapping[str, Any],
        new_versions: Any,
    ) -> Any:
        return await self._delegate.aput(
            config,
            checkpoint,
            self._owned_metadata(metadata),
            new_versions,
        )


_RECOVERY_SINGLETON_EVENT_TYPES = frozenset(
    {
        'runtime.tools.bound',
        'run.start',
        'run.end',
        'llm.human.input',
    }
)
_RECOVERY_MESSAGE_EVENT_TYPES = frozenset(
    {
        'llm.human.input',
        'llm.ai.response',
        'llm.tool.result',
    }
)
_RECOVERY_STATE_EVENT_TYPES = frozenset(
    _RECOVERY_SINGLETON_EVENT_TYPES | _RECOVERY_MESSAGE_EVENT_TYPES | {'llm.external.usage'}
)
_RECOVERY_EVENT_PAGE_SIZE = 500


def _run_completion_seed(record: RunRecord) -> dict[str, Any]:
    return {
        'total_input_tokens': record.total_input_tokens,
        'total_output_tokens': record.total_output_tokens,
        'total_tokens': record.total_tokens,
        'llm_call_count': record.llm_call_count,
        'lead_agent_tokens': record.lead_agent_tokens,
        'subagent_tokens': record.subagent_tokens,
        'middleware_tokens': record.middleware_tokens,
        'token_usage_by_model': copy.deepcopy(record.token_usage_by_model),
        'message_count': record.message_count,
        'last_ai_message': record.last_ai_message,
        'first_human_message': record.first_human_message,
    }


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _event_message_text(content: Any) -> str | None:
    if isinstance(content, str):
        text = content.strip()
        return text[:2000] if text else None
    if not isinstance(content, Mapping):
        return None

    value = content.get('content')
    if isinstance(value, str):
        text = value.strip()
        return text[:2000] if text else None
    if not isinstance(value, list):
        return None

    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        item_text = item.get('text')
        if not isinstance(item_text, str):
            item_text = item.get('content')
        if isinstance(item_text, str):
            parts.append(item_text)
    text = ''.join(parts).strip()
    return text[:2000] if text else None


def _event_usage(event: Mapping[str, Any]) -> dict[str, int]:
    metadata = event.get('metadata')
    content = event.get('content')
    candidates: list[Mapping[str, Any]] = []
    if event.get('event_type') == 'llm.external.usage' and isinstance(metadata, Mapping):
        candidates.append({**metadata, 'input_token_details': {'cache_read': metadata.get('cache_read_tokens', 0)}})
    if isinstance(metadata, Mapping) and isinstance(metadata.get('usage'), Mapping):
        candidates.append(metadata['usage'])
    if isinstance(content, Mapping) and isinstance(content.get('usage_metadata'), Mapping):
        candidates.append(content['usage_metadata'])

    best = {
        'input_tokens': 0,
        'output_tokens': 0,
        'total_tokens': 0,
        'cache_read_tokens': 0,
    }
    for candidate in candidates:
        input_tokens = _non_negative_int(candidate.get('input_tokens'))
        output_tokens = _non_negative_int(candidate.get('output_tokens'))
        total_tokens = _non_negative_int(candidate.get('total_tokens'))
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens
        input_details = candidate.get('input_token_details')
        cache_read_tokens = 0
        if isinstance(input_details, Mapping):
            cache_read_tokens = _non_negative_int(input_details.get('cache_read'))
        current = {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': total_tokens,
            'cache_read_tokens': cache_read_tokens,
        }
        if current['total_tokens'] > best['total_tokens']:
            best = current
    return best


def _event_model_name(event: Mapping[str, Any]) -> str:
    if event.get('event_type') == 'llm.external.usage':
        return str((event.get('metadata') or {}).get('model_name') or 'unknown')
    content = event.get('content')
    if isinstance(content, Mapping):
        response_metadata = content.get('response_metadata')
        if isinstance(response_metadata, Mapping):
            model_name = response_metadata.get('model_name') or response_metadata.get('model')
            if isinstance(model_name, str) and model_name.strip():
                return model_name.strip()
    return 'unknown'


def _event_caller(event: Mapping[str, Any]) -> str:
    metadata = event.get('metadata')
    if isinstance(metadata, Mapping):
        caller = metadata.get('caller')
        if isinstance(caller, str) and caller:
            return caller
    return 'lead_agent'


def _event_call_key(event: Mapping[str, Any]) -> tuple[str, int]:
    metadata = event.get('metadata')
    if isinstance(metadata, Mapping):
        call_index = _non_negative_int(metadata.get('llm_call_index'))
        if call_index > 0:
            return ('call', call_index)
    return ('event', _non_negative_int(event.get('seq')))


def _prefer_richer_ai_event(
    current: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
) -> Mapping[str, Any]:
    if current is None:
        return candidate
    current_usage = _event_usage(current)
    candidate_usage = _event_usage(candidate)
    if candidate_usage['total_tokens'] > current_usage['total_tokens']:
        return candidate
    if candidate_usage['total_tokens'] < current_usage['total_tokens']:
        return current
    if _event_message_text(candidate.get('content')) and not _event_message_text(
        current.get('content')
    ):
        return candidate
    return current


def _event_completion_seed(events: list[dict[str, Any]]) -> dict[str, Any]:
    external_events = {}
    ai_events_by_call: dict[tuple[str, int], Mapping[str, Any]] = {}
    human_message_keys: set[tuple[str, int | str]] = set()
    tool_message_keys: set[tuple[str, int | str]] = set()
    first_human_message: str | None = None
    last_ai_message: str | None = None

    for event in events:
        event_type = event.get('event_type')
        content = event.get('content')
        if event_type == 'llm.external.usage':
            source_id = (event.get('metadata') or {}).get('source_run_id')
            if source_id:
                external_events[source_id] = event
        elif event_type == 'llm.human.input':
            seq = _non_negative_int(event.get('seq'))
            human_message_keys.add(('event', seq))
            if first_human_message is None:
                first_human_message = _event_message_text(content)
        elif event_type == 'llm.tool.result':
            identity: int | str = _non_negative_int(event.get('seq'))
            if isinstance(content, Mapping):
                candidate_identity = content.get('tool_call_id') or content.get('id')
                if isinstance(candidate_identity, str) and candidate_identity:
                    identity = candidate_identity
            tool_message_keys.add(('tool', identity))
        elif event_type == 'llm.ai.response':
            key = _event_call_key(event)
            ai_events_by_call[key] = _prefer_richer_ai_event(
                ai_events_by_call.get(key),
                event,
            )
            if _event_caller(event) == 'lead_agent':
                text = _event_message_text(content)
                if text:
                    last_ai_message = text

    seed: dict[str, Any] = {
        'external_source_ids': list(external_events),
        'has_usage_events': False,
        'total_input_tokens': 0,
        'total_output_tokens': 0,
        'total_tokens': 0,
        'llm_call_count': 0,
        'lead_agent_tokens': 0,
        'subagent_tokens': 0,
        'middleware_tokens': 0,
        'token_usage_by_model': {},
        'message_count': len(human_message_keys)
        + len(tool_message_keys)
        + len(ai_events_by_call),
        'last_ai_message': last_ai_message,
        'first_human_message': first_human_message,
    }

    highest_call_index = 0
    for call_key, event in [*ai_events_by_call.items(), *[(('external', source), event) for source, event in external_events.items()]]:
        if call_key[0] == 'call':
            highest_call_index = max(highest_call_index, call_key[1])
        usage = _event_usage(event)
        total_tokens = usage['total_tokens']
        seed['total_input_tokens'] += usage['input_tokens']
        seed['total_output_tokens'] += usage['output_tokens']
        seed['total_tokens'] += total_tokens
        if total_tokens <= 0:
            continue

        seed['has_usage_events'] = True

        caller = _event_caller(event)
        if caller.startswith('subagent:'):
            seed['subagent_tokens'] += total_tokens
        elif caller.startswith('middleware:'):
            seed['middleware_tokens'] += total_tokens
        else:
            seed['lead_agent_tokens'] += total_tokens

        model_name = _event_model_name(event)
        bucket = seed['token_usage_by_model'].setdefault(
            model_name,
            {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0},
        )
        bucket['input_tokens'] += usage['input_tokens']
        bucket['output_tokens'] += usage['output_tokens']
        bucket['total_tokens'] += total_tokens
        if usage['cache_read_tokens'] > 0:
            bucket['cache_read_tokens'] = (
                bucket.get('cache_read_tokens', 0) + usage['cache_read_tokens']
            )

    seed['llm_call_count'] = max(highest_call_index, len(ai_events_by_call))
    return seed


def _event_message_identities(events: list[dict[str, Any]]) -> set[str]:
    identities: set[str] = set()
    for event in events:
        content = event.get('content')
        if not isinstance(content, Mapping):
            continue
        event_type = event.get('event_type')
        if event_type == 'llm.tool.result':
            tool_call_id = content.get('tool_call_id')
            if isinstance(tool_call_id, str) and tool_call_id:
                identities.add(f'tool:{tool_call_id}')
        elif event_type == 'llm.ai.response':
            message_id = content.get('id')
            if isinstance(message_id, str) and message_id:
                identities.add(f'message:{message_id}')
    return identities


def _merge_recovery_completion_seed(
    record_seed: dict[str, Any],
    event_seed: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(record_seed)
    for key in (
        'total_input_tokens',
        'total_output_tokens',
        'total_tokens',
        'llm_call_count',
        'lead_agent_tokens',
        'subagent_tokens',
        'middleware_tokens',
        'message_count',
    ):
        if key == 'message_count':
            merged[key] = max(_non_negative_int(record_seed.get(key)), _non_negative_int(event_seed.get(key)))
        elif event_seed.get('has_usage_events'):
            merged[key] = _non_negative_int(event_seed.get(key))
    if event_seed.get('has_usage_events'):
        merged['token_usage_by_model'] = copy.deepcopy(event_seed['token_usage_by_model'])
    merged['external_source_ids'] = event_seed.get('external_source_ids', [])

    record_message_count = _non_negative_int(record_seed.get('message_count'))
    event_message_count = _non_negative_int(event_seed.get('message_count'))
    if not merged.get('first_human_message') and event_seed.get('first_human_message'):
        merged['first_human_message'] = event_seed['first_human_message']
    if (
        event_seed.get('last_ai_message')
        and event_message_count >= record_message_count
    ):
        merged['last_ai_message'] = event_seed['last_ai_message']
    return merged


async def _load_recovery_state(
    event_store: Any,
    *,
    record: RunRecord,
) -> tuple[dict[str, Any], set[str], set[str]]:
    events: list[dict[str, Any]] = []
    after_seq: int | None = None
    while True:
        page = await event_store.list_events(
            record.thread_id,
            record.run_id,
            event_types=sorted(_RECOVERY_STATE_EVENT_TYPES),
            limit=_RECOVERY_EVENT_PAGE_SIZE,
            after_seq=after_seq,
        )
        if not page:
            break
        events.extend(page)
        last_seq = _non_negative_int(page[-1].get('seq'))
        if after_seq is not None and last_seq <= after_seq:
            raise RuntimeError(
                f'Recovery event cursor did not advance for run {record.run_id}'
            )
        after_seq = last_seq
        if len(page) < _RECOVERY_EVENT_PAGE_SIZE:
            break

    event_types = {
        event_type
        for event in events
        if isinstance((event_type := event.get('event_type')), str)
    }
    completion_seed = _merge_recovery_completion_seed(
        _run_completion_seed(record),
        _event_completion_seed(events),
    )
    return completion_seed, event_types, _event_message_identities(events)


def _install_runtime_context(config: dict, runtime_context: dict[str, Any]) -> None:
    existing_context = config.get("context")
    if isinstance(existing_context, dict):
        existing_context.setdefault("thread_id", runtime_context["thread_id"])
        existing_context.setdefault("run_id", runtime_context["run_id"])
        if DEERFLOW_TRACE_METADATA_KEY in runtime_context:
            existing_context.setdefault(DEERFLOW_TRACE_METADATA_KEY, runtime_context[DEERFLOW_TRACE_METADATA_KEY])
        if "app_config" in runtime_context:
            existing_context["app_config"] = runtime_context["app_config"]
        if CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY in runtime_context:
            existing_context[CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY] = runtime_context[CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY]
        return

    config["context"] = dict(runtime_context)


def _compute_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return "app_config" in inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=128)
def _cached_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    return _compute_agent_factory_supports_app_config(agent_factory)


def _agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return _cached_agent_factory_supports_app_config(agent_factory)
    except TypeError:
        # Some callable instances are unhashable; fall back to a direct check.
        return _compute_agent_factory_supports_app_config(agent_factory)


class _SubagentEventBuffer:
    """Buffer subagent ``task_*`` step events and flush them in one locked batch (#3779).

    The live SSE bridge already forwards these events for real-time display; this
    additionally writes them so the subtask card's step history survives a reload.

    ``RunEventStore.put`` is documented as a low-frequency path — on Postgres each
    call opens its own transaction and takes a per-thread advisory lock. A deep
    subagent (``general-purpose`` runs up to ``max_turns=150``) emits hundreds of
    ``task_running`` steps on the hot stream loop, so persisting each with
    ``put()`` would serialize against the run's own message-batch writer. This
    accumulates recognized subagent events and writes them with ``put_batch``,
    which acquires the lock once per batch, honoring the store's contract.

    Best-effort: a missing store (run_events not configured) or an unrecognized
    chunk is a no-op, flush failures are logged but never propagate into the
    stream loop. A one-second timer bounds visibility for polling clients;
    terminal ``subagent.end`` events flush eagerly and serialize with any batch
    already being written, so history is durable before the run is finalized.
    """

    #: Flush once this many events are buffered, bounding memory and reload lag on
    #: a single deep subagent without paying a per-step lock.
    FLUSH_THRESHOLD = 25
    # Office polls persisted events instead of consuming the live SSE bridge.
    # A short task or a slow model must not hide its progress until completion.
    FLUSH_INTERVAL = 1.0

    def __init__(
        self,
        event_store: Any | None,
        thread_id: str,
        run_id: str,
        *,
        secret_values: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self._event_store = event_store
        self._thread_id = thread_id
        self._run_id = run_id
        self._secret_values = frozenset(secret_values)
        self._pending: list[dict[str, Any]] = []
        self._flush_timer: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()

    async def add(self, chunk: Any) -> None:
        """Buffer one custom chunk; bound the batch by size, delay or task end."""
        if self._event_store is None:
            return
        # Lazy import: importing deerflow.subagents at module load triggers its
        # package __init__ (executor → agents → tools → task_tool), which imports
        # back from deerflow.subagents and deadlocks at gateway startup. Deferring
        # it to call time (after all modules are loaded) breaks that cycle.
        from deerflow.subagents.step_events import subagent_run_event

        record = subagent_run_event(chunk)
        if record is None:
            return
        self._pending.append(
            {
                "thread_id": self._thread_id,
                "run_id": self._run_id,
                "created_at": datetime.now(UTC).isoformat(),
                **record,
            }
        )
        if record["event_type"] == "subagent.end" or len(self._pending) >= self.FLUSH_THRESHOLD:
            await self.flush()
        elif self._flush_timer is None:
            self._flush_timer = asyncio.create_task(self._flush_after_delay())

    async def _flush_after_delay(self) -> None:
        await asyncio.sleep(self.FLUSH_INTERVAL)
        # Once persistence starts it must not be cancelled by a manual flush.
        self._flush_timer = None
        await self.flush()

    async def flush(self) -> None:
        """Persist buffered events in one ``put_batch`` call; swallow store errors."""
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None
        async with self._flush_lock:
            await self._persist_pending()

    async def _persist_pending(self) -> None:
        if self._event_store is None or not self._pending:
            return
        batch = self._pending
        self._pending = []
        try:
            await self._event_store.put_batch(batch)
        except Exception as exc:
            # Re-buffer the failed batch (ahead of any events queued since) so a
            # transient store error does not silently drop subagent step events.
            self._pending = batch + self._pending
            _log_redacted_warning(
                "Run %s: failed to persist %d subagent step event(s)",
                self._run_id,
                len(batch),
                error=exc,
                secret_values=self._secret_values,
            )


async def run_agent(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    owner_user: Any,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: Any,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    recovery: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
) -> None:
    """Run one persisted record under its explicit authenticated owner."""

    owner_token = set_current_user(owner_user)
    try:
        await _run_agent_core(
            bridge,
            run_manager,
            record,
            ctx=ctx,
            agent_factory=agent_factory,
            graph_input=graph_input,
            config=config,
            stream_modes=stream_modes,
            stream_subgraphs=stream_subgraphs,
            recovery=recovery,
            interrupt_before=interrupt_before,
            interrupt_after=interrupt_after,
        )
    finally:
        reset_current_user(owner_token)


async def _run_agent_core(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: Any,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    recovery: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
) -> None:
    """Execute an agent in the background, publishing events to *bridge*."""

    # Unpack infrastructure dependencies from RunContext.
    run_secret_values = collect_secret_values(ctx.app_config, config)
    run_id = record.run_id
    checkpointer = _RunOwnedCheckpointer(
        redacting_checkpointer(ctx.checkpointer, run_secret_values),
        run_id,
    )
    store = ctx.store
    event_store = ctx.event_store
    run_events_config = ctx.run_events_config
    thread_store = ctx.thread_store

    thread_id = record.thread_id
    requested_modes: set[str] = set(stream_modes or ["values"])
    pre_run_checkpoint_id: str | None = None
    pre_run_snapshot: dict[str, Any] | None = None
    pre_run_workspace_snapshot: WorkspaceSnapshot | None = None
    workspace_changes_user_id: str | None = None
    workspace_changes_workspace_path: str | None = None
    snapshot_capture_failed = False
    llm_error_fallback_message: str | None = None
    pending_human_input_request: dict[str, Any] | None = None
    final_status = RunStatus.error
    final_error: str | None = "Run ended before selecting a terminal outcome"
    # Message ids checkpointed *before* this run started. The stream loop uses
    # this set to mask out ``deerflow_error_fallback`` markers that belong to
    # earlier runs on the same thread — without it, one stale fallback in
    # history would mark every subsequent run on this thread as ``error``.
    pre_existing_message_ids: set[str] = set()

    journal = None
    # Buffers subagent step events for batched persistence (#3779); assigned once
    # streaming starts and flushed in the finally block. Pre-bound to None so the
    # finally is safe even if an exception fires before streaming begins.
    subagent_events: _SubagentEventBuffer | None = None

    # Track whether "events" was requested but skipped
    try:
        # Track whether "events" was requested but skipped
        if "events" in requested_modes:
            logger.info(
                "Run %s: 'events' stream_mode not supported in gateway (requires astream_events + checkpoint callbacks). Skipping.",
                run_id,
            )

        await run_manager.wait_for_prior_finalizing(thread_id, run_id)

        # Initialize RunJournal + write human_message event.
        # These are inside the try block so any exception (e.g. a DB
        # error writing the event) flows through the except/finally
        # path that publishes an "end" event to the SSE bridge —
        # otherwise a failure here would leave the stream hanging
        # with no terminator.
        if event_store is not None:
            from deerflow.runtime.journal import RunJournal

            initial_completion_data = None
            existing_event_types = None
            existing_message_identities = None
            if recovery:
                (
                    initial_completion_data,
                    existing_event_types,
                    existing_message_identities,
                ) = await _load_recovery_state(
                    event_store,
                    record=record,
                )

            journal = RunJournal(
                run_id=run_id,
                thread_id=thread_id,
                event_store=event_store,
                user_id=record.user_id,
                track_token_usage=getattr(run_events_config, "track_token_usage", True),
                flush_threshold=getattr(run_events_config, 'flush_threshold', 20),
                progress_reporter=lambda snapshot: run_manager.update_run_progress(run_id, **snapshot),
                initial_completion_data=initial_completion_data,
                existing_event_types=existing_event_types,
                existing_message_identities=existing_message_identities,
                secret_values=run_secret_values,
                is_resume=getattr(graph_input, "resume", None) is not None,
                flush_public_messages=bool((record.metadata or {}).get('office_employee_id')),
            )
            resume_value = getattr(graph_input, 'resume', None)
            if resume_value is not None:
                journal.record_human_input_response(resume_value)
            if recovery:
                configurable = config.get('configurable') or {}
                checkpoint_id = configurable.get('checkpoint_id')
                if isinstance(checkpoint_id, str) and checkpoint_id:
                    prior_last_event_seq = await event_store.get_last_event_seq(
                        thread_id,
                        run_id,
                    )
                    journal.record_recovery_started(
                        checkpoint_id=checkpoint_id,
                        prior_last_event_seq=prior_last_event_seq,
                    )
                    await journal.flush()

        if ctx.on_run_starting is not None:
            await ctx.on_run_starting(record)

        # 1. Mark running
        await run_manager.set_status(run_id, RunStatus.running)
        if ctx.on_run_started is not None:
            try:
                await ctx.on_run_started(record)
            except Exception as exc:
                _log_redacted_warning(
                    "Run started hook failed for %s (non-fatal)",
                    run_id,
                    error=exc,
                    secret_values=run_secret_values,
                )

        if event_store is not None:
            workspace_changes_user_id = get_effective_user_id()
            run_context = config.get('context')
            if isinstance(run_context, Mapping):
                raw_workspace_path = run_context.get(OFFICE_PROJECT_PATH_CONTEXT_KEY)
                if isinstance(raw_workspace_path, str) and raw_workspace_path:
                    workspace_changes_workspace_path = raw_workspace_path
            try:
                # Managed task results have their own Git baseline/result facts.
                # A diff of the bound user checkout would attribute user edits to this Run.
                pre_run_workspace_snapshot = None if (run_context or {}).get('office_work') else await capture_workspace_snapshot(
                    thread_id,
                    user_id=workspace_changes_user_id,
                    workspace_path=workspace_changes_workspace_path,
                )
            except Exception as exc:
                _log_redacted_warning(
                    "Could not capture pre-run workspace snapshot for run %s",
                    run_id,
                    error=exc,
                    secret_values=run_secret_values,
                )

        # Snapshot the latest pre-run checkpoint so rollback can restore it.
        if checkpointer is not None:
            try:
                config_for_check = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(config_for_check)
                if ckpt_tuple is not None:
                    ckpt_config = getattr(ckpt_tuple, "config", {}).get("configurable", {})
                    pre_run_checkpoint_id = ckpt_config.get("checkpoint_id")
                    pre_run_snapshot = {
                        "checkpoint_ns": ckpt_config.get("checkpoint_ns", ""),
                        "checkpoint": copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {})),
                        "metadata": copy.deepcopy(getattr(ckpt_tuple, "metadata", {})),
                        "pending_writes": copy.deepcopy(getattr(ckpt_tuple, "pending_writes", []) or []),
                    }
                    pre_existing_message_ids = _collect_pre_existing_message_ids(pre_run_snapshot)
            except Exception as exc:
                snapshot_capture_failed = True
                _log_redacted_warning(
                    "Could not capture pre-run checkpoint snapshot for run %s",
                    run_id,
                    error=exc,
                    secret_values=run_secret_values,
                )

        # 2. Publish metadata — useStream needs both run_id AND thread_id
        await bridge.publish(
            run_id,
            "metadata",
            {
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        # 3. Build the agent
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime

        # Inject runtime context so middlewares and tools (via ToolRuntime.context) can
        # access thread-level data. langgraph-cli does this automatically; we must do it
        # manually here because we drive the graph through ``agent.astream(config=...)``
        # without passing the official ``context=`` parameter.
        runtime_ctx = _build_runtime_context(
            thread_id,
            run_id,
            config.get('context'),
            ctx.app_config,
        )
        graph_input = _with_authoritative_thread_data(graph_input, runtime_ctx)
        runtime_ctx[CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY] = frozenset(pre_existing_message_ids)
        incoming_metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
        deerflow_trace_id = normalize_trace_id(incoming_metadata.get(DEERFLOW_TRACE_METADATA_KEY)) or get_current_trace_id()
        if deerflow_trace_id:
            runtime_ctx[DEERFLOW_TRACE_METADATA_KEY] = deerflow_trace_id
        # Expose the run-scoped journal under a sentinel key so middleware can
        # write audit events (e.g. SafetyFinishReasonMiddleware recording
        # suppressed tool calls). Double-underscore prefix marks it as a
        # runtime-internal channel; user code must not depend on the key name.
        if journal is not None:
            runtime_ctx["__run_journal"] = journal
        runtime_ctx["abort_event"] = record.abort_event
        _install_runtime_context(config, runtime_ctx)
        runtime = Runtime(context=cast(Any, runtime_ctx), store=store)
        config.setdefault("configurable", {})["__pregel_runtime"] = runtime

        # Inject RunJournal as a LangChain callback handler.
        # on_llm_end captures token usage; on_chain_start/end captures lifecycle.
        if journal is not None:
            config.setdefault("callbacks", []).append(journal)

        # Inject Langfuse trace-attribute metadata so the langchain CallbackHandler
        # can lift session_id / user_id / trace_name / tags onto the root trace.
        # Shared helper with ``DeerFlowClient.stream`` so both entry points stay
        # in sync; caller-provided metadata wins via setdefault inside the helper.
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=resolve_runtime_user_id(runtime),
            assistant_id=record.assistant_id,
            model_name=record.model_name,
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
            deerflow_trace_id=deerflow_trace_id,
        )

        # Resolve after runtime context installation so context/configurable reflect
        # the agent name that this run will actually execute.
        config.setdefault("run_name", resolve_root_run_name(config, record.assistant_id))
        initial_runnable_config = RunnableConfig(**config)
        initial_metadata = initial_runnable_config.setdefault("metadata", {})
        initial_metadata.pop(RUNTIME_BOUND_TOOL_NAMES_METADATA_KEY, None)
        initial_metadata.pop(RUNTIME_MODEL_ID_METADATA_KEY, None)

        def _continuation_runnable_config() -> RunnableConfig:
            continuation_config = dict(config)
            configurable = dict(continuation_config.get("configurable", {}) or {})
            configurable["checkpoint_ns"] = ""
            configurable.pop("checkpoint_id", None)
            configurable.pop("checkpoint_map", None)
            continuation_config["configurable"] = configurable
            return RunnableConfig(**continuation_config)

        if ctx.app_config is not None and _agent_factory_supports_app_config(agent_factory):
            agent = agent_factory(config=initial_runnable_config, app_config=ctx.app_config)
        else:
            agent = agent_factory(config=initial_runnable_config)

        # The first-party factory records resolved runtime facts on the exact
        # RunnableConfig object supplied by the worker. Agent metadata remains a
        # fallback only for third-party factories that cannot mutate the config.
        runtime_metadata = initial_runnable_config.get("metadata") or {}
        agent_metadata = getattr(agent, "metadata", {}) or {}
        effective_model_name = runtime_metadata.get("model_name") if isinstance(runtime_metadata, dict) else None
        if not effective_model_name and isinstance(agent_metadata, dict):
            effective_model_name = agent_metadata.get("model_name")
        if not effective_model_name:
            effective_model_name = record.model_name
        runtime_model_id = runtime_metadata.get(RUNTIME_MODEL_ID_METADATA_KEY) if isinstance(runtime_metadata, dict) else None
        if not runtime_model_id and isinstance(agent_metadata, dict):
            runtime_model_id = agent_metadata.get(RUNTIME_MODEL_ID_METADATA_KEY)
        if not isinstance(runtime_model_id, str) or not runtime_model_id.strip():
            runtime_model_id = None
        else:
            runtime_model_id = runtime_model_id.strip()
        if effective_model_name and effective_model_name != record.model_name:
            await run_manager.update_model_name(record.run_id, effective_model_name)

        if journal is not None and isinstance(runtime_metadata, dict):
            bound_tool_names = runtime_metadata.get(RUNTIME_BOUND_TOOL_NAMES_METADATA_KEY)
            if isinstance(bound_tool_names, list) and all(isinstance(name, str) and name for name in bound_tool_names):
                journal.record_runtime_tools_bound(
                    tool_names=bound_tool_names,
                    model_name=effective_model_name,
                    runtime_model_id=runtime_model_id,
                )

        # 4. Attach checkpointer and store
        agent_checkpointer = checkpointer
        if agent_checkpointer is not None and ctx.on_run_input_checkpointed is not None:

            async def _notify_input_checkpoint_persisted() -> None:
                try:
                    await ctx.on_run_input_checkpointed(record)
                except Exception as exc:
                    _log_redacted_warning(
                        "Run input checkpoint hook failed for %s (non-fatal)",
                        run_id,
                        error=exc,
                        secret_values=run_secret_values,
                    )

            agent_checkpointer = _AfterFirstSuccessfulCheckpoint(
                agent_checkpointer,
                _notify_input_checkpoint_persisted,
            )
        if agent_checkpointer is not None:
            agent.checkpointer = agent_checkpointer
        if store is not None:
            agent.store = store

        # 5. Set interrupt nodes
        if interrupt_before:
            agent.interrupt_before_nodes = interrupt_before
        if interrupt_after:
            agent.interrupt_after_nodes = interrupt_after

        # 6. Build LangGraph stream_mode list
        #    "events" is NOT a valid astream mode — skip it
        #    "messages-tuple" maps to LangGraph's "messages" mode
        lg_modes: list[str] = []
        for m in requested_modes:
            if m == "messages-tuple":
                lg_modes.append("messages")
            elif m == "events":
                # Skipped — see log above
                continue
            elif m in _VALID_LG_MODES:
                lg_modes.append(m)
        if not lg_modes:
            lg_modes = ["values"]

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for m in lg_modes:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        lg_modes = deduped

        logger.info("Run %s: streaming with modes %s (requested: %s)", run_id, lg_modes, requested_modes)

        # Buffer subagent step events and persist them in batches (#3779) instead
        # of one low-frequency put() per step on the hot stream loop. Flushed in
        # the finally block so buffered steps survive abort/exception paths too.
        subagent_events = _SubagentEventBuffer(
            event_store,
            thread_id,
            run_id,
            secret_values=run_secret_values,
        )

        goal_evaluator_model: Any | None = None
        def _get_goal_evaluator_model() -> Any:
            nonlocal goal_evaluator_model
            if goal_evaluator_model is None:
                goal_evaluator_model = create_goal_evaluator_model(
                    model_name=record.model_name,
                    app_config=ctx.app_config,
                )
            return goal_evaluator_model

        async def _capture_human_input_request(chunk: Any) -> None:
            nonlocal pending_human_input_request
            if pending_human_input_request is not None:
                return
            request_payload = _extract_human_input_request(chunk)
            if request_payload is None:
                return
            pending_human_input_request = request_payload
            if event_store is not None:
                await event_store.put(
                    thread_id=thread_id,
                    run_id=run_id,
                    event_type='runtime.human_input.requested',
                    category='message',
                    content=request_payload,
                    metadata={'request_id': request_payload.get('request_id')},
                )

        async def _stream_once(input_payload: Any, stream_config: RunnableConfig) -> None:
            nonlocal llm_error_fallback_message
            async with _checkpoint_thread_lock(thread_id):
                if len(lg_modes) == 1 and not stream_subgraphs:
                    # Single mode, no subgraphs: astream yields raw chunks
                    single_mode = lg_modes[0]
                    async for chunk in agent.astream(input_payload, config=stream_config, stream_mode=single_mode):
                        if record.abort_event.is_set():
                            logger.info("Run %s abort requested — stopping", run_id)
                            break
                        llm_error_fallback_message = llm_error_fallback_message or _extract_llm_error_fallback_message(chunk, pre_existing_message_ids)
                        await _capture_human_input_request(chunk)
                        safe_chunk = redact_value(chunk, run_secret_values)
                        sse_event = _lg_mode_to_sse_event(single_mode)
                        await bridge.publish(
                            run_id,
                            sse_event,
                            serialize(safe_chunk, mode=single_mode),
                        )
                        if single_mode == "custom":
                            await subagent_events.add(safe_chunk)
                    return
                # Multiple modes or subgraphs: astream yields tuples
                async for item in agent.astream(
                    input_payload,
                    config=stream_config,
                    stream_mode=lg_modes,
                    subgraphs=stream_subgraphs,
                ):
                    if record.abort_event.is_set():
                        logger.info("Run %s abort requested — stopping", run_id)
                        break

                    mode, chunk = _unpack_stream_item(item, lg_modes, stream_subgraphs)
                    if mode is None:
                        continue

                    llm_error_fallback_message = llm_error_fallback_message or _extract_llm_error_fallback_message(chunk, pre_existing_message_ids)
                    await _capture_human_input_request(chunk)
                    safe_chunk = redact_value(chunk, run_secret_values)
                    sse_event = _lg_mode_to_sse_event(mode)
                    await bridge.publish(
                        run_id,
                        sse_event,
                        serialize(safe_chunk, mode=mode),
                    )
                    if mode == "custom":
                        await subagent_events.add(safe_chunk)

        # 7. Stream the requested turn, then optionally continue hidden goal turns.
        await _stream_once(graph_input, initial_runnable_config)
        while not record.abort_event.is_set() and pending_human_input_request is None and not llm_error_fallback_message and (journal is None or not journal.had_llm_error_fallback):
            continuation_input = await _prepare_goal_continuation_input(
                bridge=bridge,
                checkpointer=checkpointer,
                thread_id=thread_id,
                run_id=run_id,
                model_name=record.model_name,
                app_config=ctx.app_config,
                evaluator_model_factory=_get_goal_evaluator_model,
                abort_event=record.abort_event,
                secret_values=run_secret_values,
            )
            if continuation_input is None or record.abort_event.is_set():
                break
            await _stream_once(continuation_input, _continuation_runnable_config())

        # 8. Select the outcome locally. Only the finalization boundary below
        # may expose a terminal status after all durable events are sealed.
        if record.abort_event.is_set():
            await run_manager.set_finalizing(run_id, True)
            action = record.abort_action
            if action == "rollback":
                final_status = RunStatus.error
                final_error = "Rolled back by user"
                try:
                    await _rollback_to_pre_run_checkpoint(
                        checkpointer=checkpointer,
                        thread_id=thread_id,
                        run_id=run_id,
                        pre_run_checkpoint_id=pre_run_checkpoint_id,
                        pre_run_snapshot=pre_run_snapshot,
                        snapshot_capture_failed=snapshot_capture_failed,
                    )
                    logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                except Exception as exc:
                    rollback_error = redact_text(str(exc), run_secret_values)
                    final_error = f"Rollback failed: {rollback_error}"
                    logger.warning(
                        "Failed to rollback checkpoint for run %s | %s",
                        run_id,
                        redact_exception_traceback(exc, run_secret_values),
                    )
            else:
                final_status = RunStatus.interrupted
                final_error = None
        elif pending_human_input_request is not None:
            final_status = RunStatus.interrupted
            final_error = None
        elif llm_error_fallback_message or (journal is not None and journal.had_llm_error_fallback):
            error_msg = llm_error_fallback_message
            if error_msg is None and journal is not None:
                error_msg = journal.llm_error_fallback_message
            final_status = RunStatus.error
            final_error = error_msg or "LLM provider failed after retries"
        else:
            final_status = RunStatus.success
            final_error = None

    except asyncio.CancelledError:
        await run_manager.set_finalizing(run_id, True)
        action = record.abort_action
        if action == "rollback":
            final_status = RunStatus.error
            final_error = "Rolled back by user"
            try:
                await _rollback_to_pre_run_checkpoint(
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                    run_id=run_id,
                    pre_run_checkpoint_id=pre_run_checkpoint_id,
                    pre_run_snapshot=pre_run_snapshot,
                    snapshot_capture_failed=snapshot_capture_failed,
                )
                logger.info("Run %s was cancelled and rolled back", run_id)
            except Exception as exc:
                rollback_error = redact_text(str(exc), run_secret_values)
                final_error = f"Rollback failed: {rollback_error}"
                logger.warning(
                    "Run %s cancellation rollback failed | %s",
                    run_id,
                    redact_exception_traceback(exc, run_secret_values),
                )
        else:
            final_status = RunStatus.interrupted
            final_error = None
            logger.info("Run %s was cancelled", run_id)

    except Exception as exc:
        error_msg = redact_text(str(exc), run_secret_values)
        from deerflow.agents.middlewares.office_round_budget import OfficeRoundTimeout
        final_status = RunStatus.timeout if isinstance(exc, OfficeRoundTimeout) else RunStatus.error
        final_error = error_msg
        logger.error(
            "Run %s failed: %s | %s",
            run_id,
            error_msg,
            redact_exception_traceback(exc, run_secret_values),
        )
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
            },
        )

    finally:
        # Sealed events are not yet a published reply. Unlike cancellation
        # cleanup, publication must allow completion hooks to admit a successor.
        await run_manager.set_completion_pending(run_id, True)
        # Persist any subagent step events still buffered (#3779) — including on
        # abort/exception paths, where the stream loop broke before its own flush.
        if subagent_events is not None:
            await subagent_events.flush()

        if event_store is not None and pre_run_workspace_snapshot is not None:
            try:
                await record_workspace_changes(
                    event_store,
                    thread_id,
                    run_id,
                    pre_run_workspace_snapshot,
                    user_id=workspace_changes_user_id,
                    workspace_path=workspace_changes_workspace_path,
                )
            except Exception as exc:
                _log_redacted_warning(
                    "Failed to record workspace changes for run %s",
                    run_id,
                    error=exc,
                    secret_values=run_secret_values,
                )

        # Seal every event source before committing the run's terminal state.
        if journal is not None:
            await journal.flush()
            completion = journal.get_completion_data()
        else:
            completion = {}

        last_event_seq = 0
        if event_store is not None:
            last_event_seq = await event_store.get_last_event_seq(
                thread_id,
                run_id,
            )

        # This is the unique terminal commit. If it fails, do not publish End,
        # do not run completion hooks, and do not pretend the Run is complete.
        if final_error is not None:
            final_error = redact_text(final_error, run_secret_values)
        completion = redact_value(completion, run_secret_values)
        await run_manager.finalize_run(
            run_id,
            status=final_status,
            last_event_seq=last_event_seq,
            error=final_error,
            **completion,
        )

        # Keep the native interrupt checkpoint intact. A synthetic title checkpoint
        # has no pending interrupt writes and must not replace an answerable ask.
        if (checkpointer is not None and record.status == RunStatus.interrupted
                and pending_human_input_request is None and not record.metadata.get('office_employee_id')):
            try:
                await run_manager.wait_for_prior_finalizing(thread_id, run_id)
                if not await run_manager.has_later_started_run(thread_id, run_id):
                    await _ensure_interrupted_title(checkpointer=checkpointer, thread_id=thread_id, app_config=ctx.app_config, graph_input=graph_input)
            except Exception:
                logger.debug("Failed to generate interrupted title for thread %s (non-fatal)", thread_id)

        # Sync title from checkpoint to threads_meta.display_name
        if checkpointer is not None and thread_store is not None:
            try:
                ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(ckpt_config)
                if ckpt_tuple is not None:
                    ckpt = getattr(ckpt_tuple, "checkpoint", {}) or {}
                    title = ckpt.get("channel_values", {}).get("title")
                    if title:
                        await thread_store.update_display_name(thread_id, title)
            except Exception:
                logger.debug("Failed to sync title for thread %s (non-fatal)", thread_id)

        # Update threads_meta status based on run outcome
        if thread_store is not None:
            try:
                final_status = "idle" if record.status == RunStatus.success else record.status.value
                await thread_store.update_status(thread_id, final_status)
            except Exception:
                logger.debug("Failed to update thread_meta status for %s (non-fatal)", thread_id)

        if ctx.on_run_completed is not None:
            try:
                await ctx.on_run_completed(record)
            except Exception as exc:
                _log_redacted_warning(
                    "Run completion hook failed for %s (non-fatal)",
                    run_id,
                    error=exc,
                    secret_values=run_secret_values,
                )
        if record.metadata.get('office_employee_id') and not record.metadata.get('office_runtime_finished_at'):
            await run_manager.update_metadata(
                run_id, {'office_runtime_finished_at': datetime.now(UTC).isoformat()}, user_id=record.user_id,
            )
        if record.finalizing:
            await run_manager.set_finalizing(run_id, False)
        await run_manager.set_completion_pending(run_id, False)

        await bridge.publish_end(run_id)
        asyncio.create_task(bridge.cleanup(run_id, delay=60))
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _checkpoint_id(checkpoint_tuple: Any) -> str | None:
    config = getattr(checkpoint_tuple, "config", {}) or {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    checkpoint_id = configurable.get("checkpoint_id") if isinstance(configurable, dict) else None
    if isinstance(checkpoint_id, str):
        return checkpoint_id
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    if isinstance(checkpoint, dict) and isinstance(checkpoint.get("id"), str):
        return checkpoint["id"]
    return None


def _goal_instance_matches(left: GoalState | None, right: GoalState | None) -> bool:
    if not left or not right:
        return False
    same_status = left.get("status") == right.get("status") == "active"
    same_objective = left.get("objective") == right.get("objective")
    same_created_at = left.get("created_at") == right.get("created_at")
    return same_status and same_objective and same_created_at


def _read_checkpoint_messages(checkpoint_tuple: Any) -> list[Any]:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = channel_values.get("messages", []) if isinstance(channel_values, dict) else []
    return messages if isinstance(messages, list) else []


def _read_checkpoint_goal(checkpoint_tuple: Any) -> GoalState | None:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    raw_goal = channel_values.get("goal") if isinstance(channel_values, dict) else None
    return copy.deepcopy(raw_goal) if isinstance(raw_goal, dict) else None


def _has_durable_goal_turn_receipt(checkpoint_tuple: Any, messages: list[Any]) -> bool:
    """Return true when a completed visible assistant turn is safely checkpointed.

    ``pending_writes`` is the durability signal: a ``CheckpointTuple`` carries no
    ``tasks`` field (those live on a ``StateSnapshot``), so the presence of any
    queued writes is what tells us the turn is still in flight.
    """
    if _checkpoint_id(checkpoint_tuple) is None:
        return False
    if getattr(checkpoint_tuple, "pending_writes", None):
        return False
    visible_messages = []
    for message in messages:
        if _is_visible_message(message) and message_to_text(message).strip():
            visible_messages.append(message)
    if not visible_messages:
        return False
    return _message_type(visible_messages[-1]) == "ai"


def _stand_down_reason(goal: GoalState, evaluation: GoalEvaluation, no_progress_count: int) -> str | None:
    if evaluation["satisfied"]:
        return None
    if evaluation["blocker"] != "goal_not_met_yet":
        return f"blocked:{evaluation['blocker']}"
    # Default caps mirror should_continue_goal so the two gate functions agree on
    # a goal dict that is missing these fields.
    if int(goal.get("continuation_count", 0)) >= int(goal.get("max_continuations", DEFAULT_MAX_GOAL_CONTINUATIONS)):
        return "max_continuations_reached"
    if no_progress_count >= int(goal.get("max_no_progress_continuations", DEFAULT_MAX_NO_PROGRESS_CONTINUATIONS)):
        return "no_progress_detected"
    return None


async def _persist_goal_evaluation(
    *,
    bridge: StreamBridge,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    goal: GoalState,
    evaluation: GoalEvaluation,
    no_progress_count: int,
    continuation_count: int | None = None,
    stand_down_reason: str | None = None,
    evidence_signature: str = "",
    secret_values: set[str] | frozenset[str] = frozenset(),
) -> GoalState | None:
    try:
        async with goal_thread_lock(thread_id):
            checkpoint_tuple = await _call_checkpointer_method(
                checkpointer,
                "aget_tuple",
                "get_tuple",
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            )
            if checkpoint_tuple is None:
                return None
            current_goal = _read_checkpoint_goal(checkpoint_tuple)
            if current_goal is None or not _goal_instance_matches(goal, current_goal):
                return None
            # Defensive: compute continuation_count from the fresh current_goal
            # inside the lock.  The caller computed it from a possibly-stale goal
            # snapshot; a racing continuation may have already bumped the count.
            if continuation_count is not None:
                current_count = int(current_goal.get("continuation_count", 0))
                continuation_count = max(continuation_count, current_count + 1)
            expected_checkpoint_id = _checkpoint_id(checkpoint_tuple)
            updated_goal = attach_goal_evaluation(
                current_goal,
                evaluation,
                run_id=run_id,
                continuation_count=continuation_count,
                no_progress_count=no_progress_count,
                stand_down_reason=stand_down_reason,
                evidence_signature=evidence_signature,
            )
            values = await write_thread_goal(
                checkpointer,
                thread_id,
                updated_goal,
                as_node="goal_evaluator",
                expected_checkpoint_id=expected_checkpoint_id,
            )
        await bridge.publish(run_id, "values", serialize(values, mode="values"))
        return updated_goal
    except GoalWriteConflict:
        return None
    except Exception as exc:
        _log_redacted_warning(
            "Could not persist goal evaluation for thread %s",
            thread_id,
            error=exc,
            secret_values=secret_values,
        )
        return None


async def _reread_goal_and_checkpoint(checkpointer: Any, thread_id: str) -> tuple[GoalState | None, Any]:
    """Re-read the goal and latest checkpoint together for a concurrency re-check."""
    goal = await read_thread_goal(checkpointer, thread_id)
    checkpoint_tuple = await _call_checkpointer_method(
        checkpointer,
        "aget_tuple",
        "get_tuple",
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
    )
    return goal, checkpoint_tuple


async def _prepare_goal_continuation_input(
    *,
    bridge: StreamBridge,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    model_name: str | None,
    app_config: AppConfig | None,
    evaluator_model_factory: Any | None = None,
    abort_event: asyncio.Event | None = None,
    secret_values: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Evaluate the active goal and return a hidden continuation input if needed.

    NOTE: The re-reads below catch a racing user message or ``/goal clear``
    before we queue a continuation. Goal writes then serialize per thread and
    pass the checkpoint id they read from, so stale evaluator writes stand down
    instead of clobbering a newer goal change.
    """
    if checkpointer is None:
        return None
    if abort_event is not None and abort_event.is_set():
        return None
    effective_secret_values = frozenset(secret_values) | collect_secret_values(app_config)

    try:
        goal = await read_thread_goal(checkpointer, thread_id)
    except Exception as exc:
        _log_redacted_warning(
            "Could not read goal for thread %s after run %s",
            thread_id,
            run_id,
            error=exc,
            secret_values=effective_secret_values,
        )
        return None
    if not goal or goal.get("status") != "active":
        return None

    async def _persist(
        goal: GoalState,
        evaluation: GoalEvaluation,
        no_progress_count: int,
        *,
        stand_down_reason: str | None = None,
        continuation_count: int | None = None,
    ) -> GoalState | None:
        """Record the evaluation against the still-current goal instance."""
        return await _persist_goal_evaluation(
            bridge=bridge,
            checkpointer=checkpointer,
            thread_id=thread_id,
            run_id=run_id,
            goal=goal,
            evaluation=evaluation,
            no_progress_count=no_progress_count,
            continuation_count=continuation_count,
            stand_down_reason=stand_down_reason,
            evidence_signature=evidence_signature,
            secret_values=effective_secret_values,
        )

    try:
        checkpoint_tuple = await _call_checkpointer_method(
            checkpointer,
            "aget_tuple",
            "get_tuple",
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        )
        if checkpoint_tuple is None:
            return None
        checkpoint_id_before = _checkpoint_id(checkpoint_tuple)
        messages = _read_checkpoint_messages(checkpoint_tuple)
        conversation_signature_before = visible_conversation_signature(messages)
        evidence_signature = latest_visible_assistant_signature(messages)

        if not _has_durable_goal_turn_receipt(checkpoint_tuple, messages):
            evaluation = GoalEvaluation(
                satisfied=False,
                blocker="run_failed",
                reason="No durable assistant end-of-turn receipt was available.",
                evidence_summary="",
            )
            no_progress_count = compute_no_progress_count(goal, evaluation, evidence_signature=evidence_signature)
            await _persist(goal, evaluation, no_progress_count, stand_down_reason="no_durable_end_of_turn")
            return None

        if abort_event is not None and abort_event.is_set():
            return None
        evaluator_model = evaluator_model_factory() if evaluator_model_factory is not None else None
        evaluation = await evaluate_goal_completion(
            goal,
            messages,
            model=evaluator_model,
            model_name=model_name,
            app_config=app_config,
        )
        if abort_event is not None and abort_event.is_set():
            return None
    except Exception as exc:
        _log_redacted_warning(
            "Goal evaluator failed for thread %s after run %s",
            thread_id,
            run_id,
            error=exc,
            secret_values=effective_secret_values,
        )
        return None

    no_progress_count = compute_no_progress_count(goal, evaluation, evidence_signature=evidence_signature)

    # Re-check that neither the goal nor the visible conversation changed while the
    # evaluator ran — a user message or /goal clear racing the evaluation must win.
    try:
        current_goal, current_checkpoint_tuple = await _reread_goal_and_checkpoint(checkpointer, thread_id)
    except Exception as exc:
        _log_redacted_warning(
            "Could not re-check goal state for thread %s after evaluation",
            thread_id,
            error=exc,
            secret_values=effective_secret_values,
        )
        return None

    if not _goal_instance_matches(goal, current_goal) or current_checkpoint_tuple is None:
        return None

    checkpoint_changed = _checkpoint_id(current_checkpoint_tuple) != checkpoint_id_before
    messages_changed = visible_conversation_signature(_read_checkpoint_messages(current_checkpoint_tuple)) != conversation_signature_before
    if checkpoint_changed or messages_changed:
        await _persist(current_goal, evaluation, no_progress_count, stand_down_reason="thread_changed_after_evaluation")
        return None

    if evaluation["satisfied"]:
        try:
            async with goal_thread_lock(thread_id):
                latest_checkpoint_tuple = await _call_checkpointer_method(
                    checkpointer,
                    "aget_tuple",
                    "get_tuple",
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                )
                if latest_checkpoint_tuple is None:
                    return None
                latest_goal = _read_checkpoint_goal(latest_checkpoint_tuple)
                if latest_goal is None or not _goal_instance_matches(goal, latest_goal):
                    return None
                values = await write_thread_goal(
                    checkpointer,
                    thread_id,
                    None,
                    as_node="goal_evaluator",
                    expected_checkpoint_id=_checkpoint_id(latest_checkpoint_tuple),
                )
            await bridge.publish(run_id, "values", serialize(values, mode="values"))
        except GoalWriteConflict:
            return None
        except Exception as exc:
            _log_redacted_warning(
                "Could not clear satisfied goal for thread %s",
                thread_id,
                error=exc,
                secret_values=effective_secret_values,
            )
        return None

    stand_down_reason = _stand_down_reason(goal, evaluation, no_progress_count)
    if stand_down_reason is not None or not should_continue_goal(goal, evaluation, no_progress_count=no_progress_count):
        await _persist(goal, evaluation, no_progress_count, stand_down_reason=stand_down_reason)
        return None

    next_count = int(goal.get("continuation_count", 0)) + 1
    updated_goal = await _persist(goal, evaluation, no_progress_count, continuation_count=next_count)
    if updated_goal is None:
        return None

    # Final guard: the persist above bumped the checkpoint id, so only the visible
    # conversation signature is meaningful for detecting a racing user turn here.
    try:
        latest_goal, latest_checkpoint_tuple = await _reread_goal_and_checkpoint(checkpointer, thread_id)
    except Exception as exc:
        _log_redacted_warning(
            "Could not verify queued goal continuation for thread %s",
            thread_id,
            error=exc,
            secret_values=effective_secret_values,
        )
        return None
    if not _goal_instance_matches(updated_goal, latest_goal) or latest_checkpoint_tuple is None:
        return None
    if visible_conversation_signature(_read_checkpoint_messages(latest_checkpoint_tuple)) != conversation_signature_before:
        await _persist(
            latest_goal,
            evaluation,
            no_progress_count,
            continuation_count=next_count,
            stand_down_reason="thread_changed_before_continuation",
        )
        return None

    logger.info(
        "Run %s continuing thread %s for active goal (%d/%d)",
        run_id,
        thread_id,
        updated_goal.get("continuation_count", next_count),
        updated_goal.get("max_continuations", 0),
    )
    return {"messages": [make_goal_continuation_message(updated_goal, evaluation)]}


async def _rollback_to_pre_run_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> None:
    """Restore thread state to the checkpoint snapshot captured before run start."""
    if checkpointer is None:
        logger.info("Run %s rollback requested but no checkpointer is configured", run_id)
        return

    if snapshot_capture_failed:
        logger.warning("Run %s rollback skipped: pre-run checkpoint snapshot capture failed", run_id)
        return

    if pre_run_snapshot is None:
        await _call_checkpointer_method(checkpointer, "adelete_thread", "delete_thread", thread_id)
        logger.info("Run %s rollback reset thread %s to empty state", run_id, thread_id)
        return

    checkpoint_to_restore = None
    metadata_to_restore: dict[str, Any] = {}
    checkpoint_ns = ""
    checkpoint = pre_run_snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        logger.warning("Run %s rollback skipped: invalid pre-run checkpoint snapshot", run_id)
        return
    checkpoint_to_restore = checkpoint
    if checkpoint_to_restore.get("id") is None and pre_run_checkpoint_id is not None:
        checkpoint_to_restore = {**checkpoint_to_restore, "id": pre_run_checkpoint_id}
    if checkpoint_to_restore.get("id") is None:
        logger.warning("Run %s rollback skipped: pre-run checkpoint has no checkpoint id", run_id)
        return
    restore_marker = _new_checkpoint_marker()
    checkpoint_to_restore = {
        **checkpoint_to_restore,
        "id": restore_marker["id"],
        "ts": restore_marker["ts"],
    }
    metadata = pre_run_snapshot.get("metadata", {})
    metadata_to_restore = metadata if isinstance(metadata, dict) else {}
    raw_checkpoint_ns = pre_run_snapshot.get("checkpoint_ns")
    checkpoint_ns = raw_checkpoint_ns if isinstance(raw_checkpoint_ns, str) else ""

    channel_versions = checkpoint_to_restore.get("channel_versions")
    new_versions = dict(channel_versions) if isinstance(channel_versions, dict) else {}

    restore_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    restored_config = await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        restore_config,
        checkpoint_to_restore,
        metadata_to_restore if isinstance(metadata_to_restore, dict) else {},
        new_versions,
    )
    if not isinstance(restored_config, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config: expected dict")
    restored_configurable = restored_config.get("configurable", {})
    if not isinstance(restored_configurable, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config payload")
    restored_checkpoint_id = restored_configurable.get("checkpoint_id")
    if not restored_checkpoint_id:
        raise RuntimeError(f"Run {run_id} rollback restore did not return checkpoint_id")

    pending_writes = pre_run_snapshot.get("pending_writes", [])
    if not pending_writes:
        return

    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for item in pending_writes:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write is not a 3-tuple: {item!r}")
        task_id, channel, value = item
        if not isinstance(channel, str):
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write has non-string channel: task_id={task_id!r}, channel={channel!r}")
        writes_by_task.setdefault(str(task_id), []).append((channel, value))

    for task_id, writes in writes_by_task.items():
        await _call_checkpointer_method(
            checkpointer,
            "aput_writes",
            "put_writes",
            restored_config,
            writes,
            task_id=task_id,
        )


def _new_checkpoint_marker() -> dict[str, str]:
    marker = empty_checkpoint()
    return {"id": marker["id"], "ts": marker["ts"]}


def _bump_channel_version(checkpointer: Any, current_version: Any) -> Any:
    """Return a strictly-different next version for a checkpoint channel.

    DB-backed LangGraph savers (PostgresSaver / v4 SqliteSaver blob layout)
    persist channel blobs keyed by ``channel_versions[<channel>]``, so the
    new value MUST differ from the prior value. We delegate to the
    checkpointer's ``get_next_version`` when available — that is the canonical
    versioning scheme each saver picks (int, monotonic float, or
    UUID-shaped string). When the checkpointer doesn't expose it (or it
    returns ``None``/an unchanged value), fall back to a defensive bump that
    still guarantees inequality.
    """
    get_next_version = getattr(checkpointer, "get_next_version", None)
    if callable(get_next_version):
        try:
            next_version = get_next_version(current_version, None)
        except Exception:
            next_version = None
        if next_version is not None and next_version != current_version:
            return next_version
        # fall through to defensive bump

    if isinstance(current_version, bool):
        # ``bool`` is a subclass of ``int``; treat True/False as 1/0 instead of
        # adding to the boolean itself, which would produce an int anyway but
        # via a path that surprises readers.
        return int(current_version) + 1
    if isinstance(current_version, int):
        return current_version + 1
    if isinstance(current_version, float):
        # Match LangGraph's default float versioning (monotonic increment).
        return current_version + 1.0
    if isinstance(current_version, str):
        try:
            return str(int(current_version) + 1)
        except ValueError:
            return f"{current_version}.1"
    return 1


def _checkpoint_identity(ckpt_tuple: Any | None, checkpoint: dict[str, Any]) -> str | None:
    tuple_config = getattr(ckpt_tuple, "config", {}) or {}
    tuple_configurable = tuple_config.get("configurable", {}) if isinstance(tuple_config, dict) else {}
    if isinstance(tuple_configurable, dict):
        checkpoint_id = tuple_configurable.get("checkpoint_id")
        if isinstance(checkpoint_id, str) and checkpoint_id:
            return checkpoint_id
    checkpoint_id = checkpoint.get("id")
    return checkpoint_id if isinstance(checkpoint_id, str) and checkpoint_id else None


def _checkpoint_namespace(ckpt_tuple: Any | None) -> str:
    tuple_config = getattr(ckpt_tuple, "config", {}) or {}
    tuple_configurable = tuple_config.get("configurable", {}) if isinstance(tuple_config, dict) else {}
    checkpoint_ns = tuple_configurable.get("checkpoint_ns", "") if isinstance(tuple_configurable, dict) else ""
    return checkpoint_ns if isinstance(checkpoint_ns, str) else ""


def _graph_input_messages(graph_input: Any | None) -> list[Any]:
    if not isinstance(graph_input, dict):
        return []
    messages = graph_input.get("messages")
    if isinstance(messages, list):
        return messages
    if isinstance(messages, tuple):
        return list(messages)
    return []


def _title_generation_state(channel_values: dict[str, Any], graph_input: Any | None) -> dict[str, Any]:
    state = dict(channel_values)
    messages = state.get("messages")
    if not messages:
        fallback_messages = _graph_input_messages(graph_input)
        if fallback_messages:
            state["messages"] = fallback_messages
    return state


async def _ensure_interrupted_title(*, checkpointer: Any, thread_id: str, app_config: AppConfig | None, graph_input: Any | None = None) -> str | None:
    """Persist a local fallback title for interrupted first-turn runs.

    Returns the title that is now persisted (existing or newly written), or
    ``None`` when no checkpoint is available or no title text can be derived.
    Idempotent: re-invoking against a checkpoint that already carries a title
    short-circuits without writing a new checkpoint.
    """
    from deerflow.agents.middlewares.title_middleware import TitleMiddleware

    middleware = TitleMiddleware(app_config=app_config) if app_config is not None else TitleMiddleware()
    ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    for _attempt in range(3):
        ckpt_tuple = await _call_checkpointer_method(checkpointer, "aget_tuple", "get_tuple", ckpt_config)
        checkpoint = copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {}) or {}) if ckpt_tuple is not None else empty_checkpoint()
        channel_values = dict(checkpoint.get("channel_values", {}) or {})
        existing_title = channel_values.get("title")
        if existing_title:
            return existing_title

        result = middleware._generate_title_result(_title_generation_state(channel_values, graph_input), allow_partial_exchange=True)
        title = result.get("title") if isinstance(result, dict) else None
        if not title:
            return None

        # ``empty_checkpoint()`` creates a fresh id every time; only real tuples
        # carry an identity stable enough for the stale-snapshot comparison.
        base_identity = _checkpoint_identity(ckpt_tuple, checkpoint) if ckpt_tuple is not None else None
        latest_tuple = await _call_checkpointer_method(checkpointer, "aget_tuple", "get_tuple", ckpt_config)
        latest_checkpoint = copy.deepcopy(getattr(latest_tuple, "checkpoint", {}) or {}) if latest_tuple is not None else empty_checkpoint()
        latest_identity = _checkpoint_identity(latest_tuple, latest_checkpoint) if latest_tuple is not None else None
        if base_identity is None:
            if latest_identity is not None:
                continue
        elif latest_identity != base_identity:
            continue

        checkpoint = latest_checkpoint
        channel_values = dict(checkpoint.get("channel_values", {}) or {})
        existing_title = channel_values.get("title")
        if existing_title:
            return existing_title

        channel_values["title"] = title
        marker = _new_checkpoint_marker()
        checkpoint.update({"id": marker["id"], "ts": marker["ts"], "channel_values": channel_values})

        # Bump ``channel_versions["title"]`` and declare the bump in ``new_versions``
        # so DB-backed savers (SqliteSaver v4 / PostgresSaver) actually persist the
        # new blob — those savers strip inline ``channel_values`` from ``put`` and
        # only write blobs for channels listed in ``new_versions``. Mirrors
        # ``_rollback_to_pre_run_checkpoint`` in the same file.
        channel_versions = dict(checkpoint.get("channel_versions", {}) or {})
        next_title_version = _bump_channel_version(checkpointer, channel_versions.get("title"))
        channel_versions["title"] = next_title_version
        checkpoint["channel_versions"] = channel_versions

        metadata = dict(getattr(latest_tuple, "metadata", {}) or {})
        metadata["source"] = "update"
        prev_step = metadata.get("step")
        metadata["step"] = (prev_step + 1) if isinstance(prev_step, int) else 1
        metadata["writes"] = {"runtime_interrupt_title": {"title": title}}

        checkpoint_ns = _checkpoint_namespace(latest_tuple)
        write_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
        await _call_checkpointer_method(
            checkpointer,
            "aput",
            "put",
            write_config,
            checkpoint,
            metadata,
            {"title": next_title_version},
        )
        return title

    return None


def _lg_mode_to_sse_event(mode: str) -> str:
    """Map LangGraph internal stream_mode name to SSE event name.

    LangGraph's ``astream(stream_mode="messages")`` produces message
    tuples.  The SSE protocol calls this ``messages-tuple`` when the
    client explicitly requests it, but the default SSE event name used
    by LangGraph Platform is simply ``"messages"``.
    """
    # All LG modes map 1:1 to SSE event names — "messages" stays "messages"
    return mode


def _error_fallback_message_from_metadata(metadata: dict[str, Any], content: Any) -> str:
    detail = metadata.get("error_detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    reason = metadata.get("error_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    if isinstance(content, str) and content.strip():
        return content.strip()[:2000]
    return "LLM provider failed after retries"


def _message_id(obj: Any) -> str | None:
    """Best-effort extraction of a stable message id from a message-like object."""
    msg_id = getattr(obj, "id", None)
    if isinstance(msg_id, str) and msg_id:
        return msg_id
    if isinstance(obj, dict):
        raw = obj.get("id")
        if isinstance(raw, str) and raw:
            return raw
    return None


def _extract_human_input_request(value: Any) -> dict[str, Any] | None:
    '''Read one structured human-input request from a LangGraph interrupt chunk.'''
    if not isinstance(value, dict):
        return None
    interrupts = value.get('__interrupt__')
    if not isinstance(interrupts, (list, tuple)):
        return None
    for item in interrupts:
        payload = item.get('value') if isinstance(item, dict) else getattr(item, 'value', None)
        if (
            isinstance(payload, dict)
            and payload.get('version') == 1
            and payload.get('kind') == 'human_input_request'
            and payload.get('source') == 'ask_clarification'
        ):
            return dict(payload)
    return None


def _try_extract_from_message(obj: Any, pre_existing_ids: set[str] | None = None) -> str | None:
    """Try to extract fallback marker from a single message object or dict.

    Messages whose id appears in ``pre_existing_ids`` are skipped — those are
    history checkpointed by a *prior* run on this thread and any fallback
    marker on them was already accounted for when that earlier run finished.
    Without this filter, a single past run that ended with a fallback marker
    would mark every subsequent run on the same thread as ``error``, because
    LangGraph replays the full message history through ``stream_mode="values"``.
    """
    if pre_existing_ids:
        msg_id = _message_id(obj)
        if msg_id is not None and msg_id in pre_existing_ids:
            return None

    additional_kwargs = getattr(obj, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("deerflow_error_fallback"):
        return _error_fallback_message_from_metadata(additional_kwargs, getattr(obj, "content", None))

    if isinstance(obj, dict):
        nested_kwargs = obj.get("additional_kwargs")
        if isinstance(nested_kwargs, dict) and nested_kwargs.get("deerflow_error_fallback"):
            return _error_fallback_message_from_metadata(nested_kwargs, obj.get("content"))
    return None


def _extract_llm_error_fallback_message(value: Any, pre_existing_ids: set[str] | None = None) -> str | None:
    """Find LLM fallback markers in streamed LangGraph chunks.

    Error fallback messages returned by model-call middleware are not guaranteed
    to pass through LLM end callbacks, but they do appear in graph state chunks.

    Messages whose id appears in ``pre_existing_ids`` are ignored — they are
    history from prior runs on the same thread (LangGraph replays the full
    messages channel in ``stream_mode="values"`` chunks), and any error
    fallback in that history was already resolved when its run finished.
    """
    # Fast path: large state chunks produced by stream_mode="values" have a
    # top-level "messages" list. Scanning only that list avoids expensive deep
    # recursion into large state dicts.
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, (list, tuple)):
            for msg in messages:
                result = _try_extract_from_message(msg, pre_existing_ids)
                if result is not None:
                    return result
            # Fallback marker is attached to an AI message in the messages
            # channel; it will never appear elsewhere in a values chunk.
            return None
        # No top-level "messages" — this is likely an "updates" chunk (small
        # dict keyed by node name). Fall through to deep walk, which is cheap
        # for these payloads.

    # Deep walk for updates / messages / tuple / list modes. Payloads are
    # small, so full recursion is acceptable here.
    seen: set[int] = set()

    def walk(obj: Any) -> str | None:
        oid = id(obj)
        if oid in seen:
            return None
        seen.add(oid)

        result = _try_extract_from_message(obj, pre_existing_ids)
        if result is not None:
            return result

        if isinstance(obj, dict):
            for item in obj.values():
                result = walk(item)
                if result is not None:
                    return result
            return None

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                result = walk(item)
                if result is not None:
                    return result
        return None

    return walk(value)


def _collect_pre_existing_message_ids(snapshot: dict[str, Any] | None) -> set[str]:
    """Pull stable message ids out of a pre-run checkpoint snapshot.

    Used by :func:`run_agent` to mask stale ``deerflow_error_fallback`` markers
    on history messages so they don't trip the current run's failure path. A
    missing or malformed snapshot yields an empty set (best-effort — we
    intentionally never raise from this helper).
    """
    if not isinstance(snapshot, dict):
        return set()
    checkpoint = snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return set()
    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, dict):
        return set()
    messages = channel_values.get("messages")
    if not isinstance(messages, (list, tuple)):
        return set()
    ids: set[str] = set()
    for msg in messages:
        msg_id = _message_id(msg)
        if msg_id is not None:
            ids.add(msg_id)
    return ids


def _unpack_stream_item(
    item: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
) -> tuple[str | None, Any]:
    """Unpack a multi-mode or subgraph stream item into (mode, chunk).

    Returns ``(None, None)`` if the item cannot be parsed.
    """
    if stream_subgraphs:
        if isinstance(item, tuple) and len(item) == 3:
            _ns, mode, chunk = item
            return str(mode), chunk
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
            return str(mode), chunk
        return None, None

    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
        return str(mode), chunk

    # Fallback: single-element output from first mode
    return lg_modes[0] if lg_modes else None, item
