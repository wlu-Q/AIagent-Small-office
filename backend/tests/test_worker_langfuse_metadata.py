"""Integration test: worker.run_agent injects Langfuse trace metadata.

Verifies that the agent factory's resulting graph receives a
``RunnableConfig`` whose ``metadata`` carries the Langfuse reserved keys
(``langfuse_session_id`` / ``langfuse_user_id`` / ``langfuse_trace_name``).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import deerflow.runtime.runs.worker as worker_module
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus
from deerflow.runtime.runs.worker import RunContext, run_agent
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY, request_trace_context


def _owner_user_for(record):
    return SimpleNamespace(id=record.user_id or "default")


class _FakeAgent:
    """Minimal LangGraph-like graph that captures the runnable config."""

    def __init__(self) -> None:
        self.captured_config: dict | None = None
        self.metadata: dict = {}
        # Worker may assign these attributes; need them to exist.
        self.checkpointer = None
        self.store = None
        self.interrupt_before_nodes: list[str] = []
        self.interrupt_after_nodes: list[str] = []

    async def astream(self, graph_input, *, config, stream_mode, **kwargs):
        self.captured_config = config
        # Empty async generator — no chunks produced.
        return
        yield  # pragma: no cover (makes this an async generator)


class _FakeRunManager:
    def __init__(self, record: RunRecord) -> None:
        self.record = record

    async def set_completion_pending(self, _run_id, pending) -> None:
        self.record.completion_pending = pending

    async def wait_for_prior_finalizing(self, *_args, **_kwargs) -> None:
        return None

    async def has_later_run(self, *_args, **_kwargs) -> bool:
        return False

    async def has_later_started_run(self, *_args, **_kwargs) -> bool:
        return False

    async def set_status(self, *_args, **_kwargs) -> None:
        return None

    async def update_model_name(self, _run_id, model_name) -> None:
        self.record.model_name = model_name

    async def finalize_run(self, _run_id, *, status, last_event_seq, error=None, **_kwargs) -> None:
        self.record.status = status
        self.record.error = error
        self.record.events_finalized = True
        self.record.last_event_seq = last_event_seq


class _FakeBridge:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def publish(self, _run_id, event, payload) -> None:
        self.events.append((event, payload))

    async def publish_end(self, _run_id) -> None:
        self.events.append(("end", None))

    async def cleanup(self, _run_id, *, delay: int = 0) -> None:
        return None


@pytest.fixture(autouse=True)
def _clear_tracing_env(monkeypatch):
    from deerflow.config.tracing_config import reset_tracing_config

    for name in ("LANGFUSE_TRACING", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    reset_tracing_config()
    yield
    reset_tracing_config()


@pytest.mark.asyncio
async def test_run_agent_injects_langfuse_metadata(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    from deerflow.config.tracing_config import reset_tracing_config

    reset_tracing_config()

    fake_agent = _FakeAgent()

    def agent_factory(config):
        return fake_agent

    record = RunRecord(
        run_id="run-1",
        thread_id="thread-xyz",
        assistant_id="lead-agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        model_name="gpt-4o",
        user_id="langfuse-owner",
    )
    record.abort_event = asyncio.Event()
    ctx = RunContext(checkpointer=None)

    with request_trace_context("gateway-trace-1"):
        await run_agent(
            _FakeBridge(),
            _FakeRunManager(record),
            record,
            owner_user=_owner_user_for(record),
            ctx=ctx,
            agent_factory=agent_factory,
            graph_input={"messages": []},
            config={"configurable": {"thread_id": "thread-xyz"}},
        )

    assert fake_agent.captured_config is not None, "astream was not invoked"
    metadata = fake_agent.captured_config.get("metadata") or {}
    assert metadata.get("langfuse_session_id") == "thread-xyz"
    # The persisted Run owner is authoritative even when the caller has a
    # different ambient user context.
    user_id = metadata.get("langfuse_user_id")
    assert user_id == "langfuse-owner", f"expected langfuse-owner, got {user_id}"
    assert metadata.get("langfuse_trace_name") == "lead-agent"
    assert metadata.get(DEERFLOW_TRACE_METADATA_KEY) == "gateway-trace-1"
    assert fake_agent.captured_config.get("context", {}).get(DEERFLOW_TRACE_METADATA_KEY) == "gateway-trace-1"
    tags = metadata.get("langfuse_tags") or []
    assert "model:gpt-4o" in tags


@pytest.mark.asyncio
async def test_run_agent_uses_context_user_id_over_contextvar(monkeypatch):
    """A run carrying ``context.user_id`` traces to that user, not the contextvar.

    Internal-token callers invoke a run on behalf of an end user, so the
    ``_current_user`` ContextVar is never that end user. The caller instead
    carries the real owner in the run request's ``config['context']['user_id']``,
    which ``resolve_runtime_user_id(runtime)`` must prefer over the contextvar —
    even though conftest's autouse fixture injects ``test-user-autouse`` into it.
    """
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    from deerflow.config.tracing_config import reset_tracing_config

    reset_tracing_config()

    fake_agent = _FakeAgent()

    def agent_factory(config):
        return fake_agent

    record = RunRecord(
        run_id="run-ctx-user",
        thread_id="thread-ctx",
        assistant_id="lead-agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        user_id="test-user-autouse",
    )
    record.abort_event = asyncio.Event()
    ctx = RunContext(checkpointer=None)

    await run_agent(
        _FakeBridge(),
        _FakeRunManager(record),
        record,
        owner_user=_owner_user_for(record),
        ctx=ctx,
        agent_factory=agent_factory,
        graph_input={"messages": []},
        config={
            "configurable": {"thread_id": "thread-ctx"},
            "context": {"user_id": "real-end-user"},
        },
    )

    metadata = fake_agent.captured_config.get("metadata") or {}
    # context.user_id wins over the contextvar's ``test-user-autouse``.
    assert metadata.get("langfuse_user_id") == "real-end-user"


@pytest.mark.asyncio
async def test_run_agent_preserves_caller_metadata_overrides(monkeypatch):
    """Caller-provided langfuse_* keys must NOT be overridden by the default injection."""
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    from deerflow.config.tracing_config import reset_tracing_config

    reset_tracing_config()

    fake_agent = _FakeAgent()

    def agent_factory(config):
        return fake_agent

    record = RunRecord(
        run_id="run-2",
        thread_id="thread-default",
        assistant_id="lead-agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        user_id="test-user-autouse",
    )
    record.abort_event = asyncio.Event()
    ctx = RunContext(checkpointer=None)

    await run_agent(
        _FakeBridge(),
        _FakeRunManager(record),
        record,
        owner_user=_owner_user_for(record),
        ctx=ctx,
        agent_factory=agent_factory,
        graph_input={"messages": []},
        config={
            "configurable": {"thread_id": "thread-default"},
            "metadata": {
                DEERFLOW_TRACE_METADATA_KEY: "explicit-deerflow-trace",
                "langfuse_session_id": "custom-session-id",
                "langfuse_user_id": "explicit-user",
            },
        },
    )

    metadata = fake_agent.captured_config.get("metadata") or {}
    # Caller-supplied keys win.
    assert metadata["langfuse_session_id"] == "custom-session-id"
    assert metadata["langfuse_user_id"] == "explicit-user"
    assert metadata[DEERFLOW_TRACE_METADATA_KEY] == "explicit-deerflow-trace"
    assert fake_agent.captured_config.get("context", {}).get(DEERFLOW_TRACE_METADATA_KEY) == "explicit-deerflow-trace"
    # Worker still fills in keys that the caller didn't set.
    assert metadata["langfuse_trace_name"] == "lead-agent"


@pytest.mark.asyncio
async def test_run_agent_persists_actual_runtime_bound_tools(monkeypatch):
    fake_agent = _FakeAgent()
    event_store = MemoryRunEventStore()

    def agent_factory(config):
        config.setdefault('metadata', {}).update(
            {
                'model_name': 'resolved-model',
                'runtime_model_id': 'mimo-v2.5',
                'runtime_bound_tool_names': [
                    'read_file',
                    'tool_search',
                    'memory_search',
                ],
            }
        )
        return fake_agent

    record = RunRecord(
        run_id='run-tools-bound',
        thread_id='thread-tools-bound',
        assistant_id='lead-agent',
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        model_name='requested-model',
        user_id="test-user-autouse",
    )
    record.abort_event = asyncio.Event()

    await run_agent(
        _FakeBridge(),
        _FakeRunManager(record),
        record,
        owner_user=_owner_user_for(record),
        ctx=RunContext(checkpointer=None, event_store=event_store),
        agent_factory=agent_factory,
        graph_input={'messages': []},
        config={
            'configurable': {'thread_id': 'thread-tools-bound'},
            'metadata': {
                'runtime_bound_tool_names': ['caller-forged-tool'],
                'runtime_model_id': 'caller-forged-model',
            },
        },
    )

    events = await event_store.list_events('thread-tools-bound', 'run-tools-bound')
    bound_events = [event for event in events if event['event_type'] == 'runtime.tools.bound']

    assert record.model_name == 'resolved-model'
    assert len(bound_events) == 1
    assert bound_events[0]['category'] == 'runtime'
    assert bound_events[0]['content'] == {
        'tool_names': ['read_file', 'tool_search', 'memory_search'],
        'tool_count': 3,
        'model_name': 'resolved-model',
        'runtime_model_id': 'mimo-v2.5',
    }


@pytest.mark.asyncio
async def test_recovered_run_accumulates_metrics_without_rewriting_root_events():
    event_store = MemoryRunEventStore()
    thread_id = 'thread-recovery'
    run_id = 'run-recovery'

    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='runtime.tools.bound',
        category='runtime',
        content={'tool_names': ['read_file']},
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='run.start',
        category='trace',
        content={'chain': 'lead_agent'},
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='llm.human.input',
        category='message',
        content={'type': 'human', 'content': 'original request'},
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='llm.ai.response',
        category='message',
        content={'type': 'ai', 'content': 'existing answer'},
        metadata={'llm_call_index': 2},
    )

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id='lead-agent',
        status=RunStatus.running,
        on_disconnect=DisconnectMode.cancel,
        model_name='resolved-model',
        total_input_tokens=100,
        total_output_tokens=50,
        total_tokens=150,
        llm_call_count=2,
        lead_agent_tokens=120,
        subagent_tokens=30,
        token_usage_by_model={
            'mimo-v2.5': {
                'input_tokens': 100,
                'output_tokens': 50,
                'total_tokens': 150,
            }
        },
        message_count=4,
        last_ai_message='existing answer',
        first_human_message='original request',
        user_id="test-user-autouse",
    )
    record.abort_event = asyncio.Event()

    class RecoveryRunManager(_FakeRunManager):
        async def update_run_progress(self, _run_id, **snapshot) -> None:
            for key, value in snapshot.items():
                setattr(self.record, key, value)

        async def finalize_run(
            self,
            _run_id,
            *,
            status,
            last_event_seq,
            error=None,
            **completion,
        ) -> None:
            for key, value in completion.items():
                setattr(self.record, key, value)
            await super().finalize_run(
                _run_id,
                status=status,
                last_event_seq=last_event_seq,
                error=error,
            )

    class RecoveryAgent(_FakeAgent):
        async def astream(self, graph_input, *, config, stream_mode, **kwargs):
            journal = next(
                callback
                for callback in config['callbacks']
                if hasattr(callback, 'get_completion_data')
            )
            chain_run_id = uuid4()
            llm_run_id = uuid4()
            journal.on_chain_start(
                {'name': 'lead_agent'},
                {},
                run_id=chain_run_id,
                parent_run_id=None,
                tags=['lead_agent'],
            )
            journal.on_chat_model_start(
                {},
                [[HumanMessage(content='original request', id='human-1')]],
                run_id=llm_run_id,
                tags=['lead_agent'],
            )
            response = SimpleNamespace(
                generations=[
                    [
                        SimpleNamespace(
                            message=AIMessage(
                                content='resumed answer',
                                response_metadata={'model_name': 'test-model'},
                                usage_metadata={
                                    'input_tokens': 10,
                                    'output_tokens': 5,
                                    'total_tokens': 15,
                                },
                            )
                        )
                    ]
                ]
            )
            journal.on_llm_end(
                response,
                run_id=llm_run_id,
                parent_run_id=None,
                tags=['lead_agent'],
            )
            return
            yield

    agent = RecoveryAgent()

    def agent_factory(config):
        config.setdefault('metadata', {}).update(
            {
                'model_name': 'resolved-model',
                'runtime_model_id': 'mimo-v2.5',
                'runtime_bound_tool_names': ['read_file'],
            }
        )
        return agent

    await run_agent(
        _FakeBridge(),
        RecoveryRunManager(record),
        record,
        owner_user=_owner_user_for(record),
        ctx=RunContext(checkpointer=None, event_store=event_store),
        agent_factory=agent_factory,
        graph_input=None,
        config={'configurable': {'thread_id': thread_id}},
        recovery=True,
    )

    events = await event_store.list_events(thread_id, run_id)
    event_types = [event['event_type'] for event in events]
    ai_events = [
        event for event in events if event['event_type'] == 'llm.ai.response'
    ]

    assert event_types.count('runtime.tools.bound') == 1
    assert event_types.count('run.start') == 1
    assert event_types.count('llm.human.input') == 1
    assert len(ai_events) == 2
    assert ai_events[-1]['metadata']['llm_call_index'] == 3
    assert record.status is RunStatus.success
    assert record.total_input_tokens == 110
    assert record.total_output_tokens == 55
    assert record.total_tokens == 165
    assert record.llm_call_count == 3
    assert record.lead_agent_tokens == 135
    assert record.subagent_tokens == 30
    assert record.message_count == 5
    assert record.first_human_message == 'original request'
    assert record.last_ai_message == 'resumed answer'


@pytest.mark.asyncio
async def test_recovered_run_rebuilds_stale_metrics_from_persisted_events(monkeypatch):
    monkeypatch.setattr(worker_module, '_RECOVERY_EVENT_PAGE_SIZE', 2)
    event_store = MemoryRunEventStore()
    thread_id = 'thread-stale-recovery'
    run_id = 'run-stale-recovery'

    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='runtime.tools.bound',
        category='runtime',
        content={'tool_names': ['read_file', 'apply_patch']},
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='run.start',
        category='trace',
        content={'chain': 'lead_agent'},
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='llm.human.input',
        category='message',
        content={'type': 'human', 'content': 'original request'},
        metadata={'caller': 'lead_agent'},
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='llm.ai.response',
        category='message',
        content={
            'type': 'ai',
            'content': '',
            'response_metadata': {'model_name': 'mimo-v2.5'},
        },
        metadata={
            'caller': 'lead_agent',
            'llm_call_index': 1,
            'usage': {
                'input_tokens': 20,
                'output_tokens': 5,
                'total_tokens': 25,
            },
        },
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='llm.tool.result',
        category='message',
        content={
            'type': 'tool',
            'content': 'file contents',
            'tool_call_id': 'tool-1',
        },
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='llm.ai.response',
        category='message',
        content={
            'type': 'ai',
            'content': 'subagent result',
            'response_metadata': {'model_name': 'mimo-v2.5'},
            'usage_metadata': {
                'input_tokens': 8,
                'output_tokens': 2,
                'total_tokens': 10,
            },
        },
        metadata={
            'caller': 'subagent:researcher',
            'llm_call_index': 2,
        },
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type='llm.ai.response',
        category='message',
        content={
            'type': 'ai',
            'content': 'guard decision',
            'response_metadata': {'model_name': 'guard-model'},
        },
        metadata={
            'caller': 'middleware:guardrail',
            'llm_call_index': 3,
            'usage': {
                'input_tokens': 4,
                'output_tokens': 1,
                'total_tokens': 5,
            },
        },
    )

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id='lead-agent',
        status=RunStatus.running,
        on_disconnect=DisconnectMode.cancel,
        model_name='resolved-model',
        user_id="test-user-autouse",
    )
    record.abort_event = asyncio.Event()

    class RecoveryRunManager(_FakeRunManager):
        async def update_run_progress(self, _run_id, **snapshot) -> None:
            for key, value in snapshot.items():
                setattr(self.record, key, value)

        async def finalize_run(
            self,
            _run_id,
            *,
            status,
            last_event_seq,
            error=None,
            **completion,
        ) -> None:
            for key, value in completion.items():
                setattr(self.record, key, value)
            await super().finalize_run(
                _run_id,
                status=status,
                last_event_seq=last_event_seq,
                error=error,
            )

    class RecoveryAgent(_FakeAgent):
        async def astream(self, graph_input, *, config, stream_mode, **kwargs):
            journal = next(
                callback
                for callback in config['callbacks']
                if hasattr(callback, 'get_completion_data')
            )
            llm_run_id = uuid4()
            journal.on_chat_model_start(
                {},
                [[HumanMessage(content='original request', id='human-1')]],
                run_id=llm_run_id,
                tags=['lead_agent'],
            )
            journal.on_llm_end(
                SimpleNamespace(
                    generations=[
                        [
                            SimpleNamespace(
                                message=AIMessage(
                                    content='resumed answer',
                                    response_metadata={'model_name': 'test-model'},
                                    usage_metadata={
                                        'input_tokens': 10,
                                        'output_tokens': 5,
                                        'total_tokens': 15,
                                    },
                                )
                            )
                        ]
                    ]
                ),
                run_id=llm_run_id,
                tags=['lead_agent'],
            )
            return
            yield

    def agent_factory(config):
        config.setdefault('metadata', {}).update(
            {
                'model_name': 'resolved-model',
                'runtime_model_id': 'mimo-v2.5',
                'runtime_bound_tool_names': ['read_file', 'apply_patch'],
            }
        )
        return RecoveryAgent()

    await run_agent(
        _FakeBridge(),
        RecoveryRunManager(record),
        record,
        owner_user=_owner_user_for(record),
        ctx=RunContext(checkpointer=None, event_store=event_store),
        agent_factory=agent_factory,
        graph_input=None,
        config={'configurable': {'thread_id': thread_id}},
        recovery=True,
    )

    events = await event_store.list_events(thread_id, run_id)
    event_types = [event['event_type'] for event in events]
    ai_events = [
        event for event in events if event['event_type'] == 'llm.ai.response'
    ]

    assert event_types.count('runtime.tools.bound') == 1
    assert event_types.count('run.start') == 1
    assert event_types.count('llm.human.input') == 1
    assert ai_events[-1]['metadata']['llm_call_index'] == 4
    assert record.status is RunStatus.success
    assert record.total_input_tokens == 42
    assert record.total_output_tokens == 13
    assert record.total_tokens == 55
    assert record.llm_call_count == 4
    assert record.lead_agent_tokens == 40
    assert record.subagent_tokens == 10
    assert record.middleware_tokens == 5
    assert record.token_usage_by_model == {
        'mimo-v2.5': {
            'input_tokens': 28,
            'output_tokens': 7,
            'total_tokens': 35,
        },
        'guard-model': {
            'input_tokens': 4,
            'output_tokens': 1,
            'total_tokens': 5,
        },
        'test-model': {
            'input_tokens': 10,
            'output_tokens': 5,
            'total_tokens': 15,
        },
    }
    assert record.message_count == 6
    assert record.first_human_message == 'original request'
    assert record.last_ai_message == 'resumed answer'


@pytest.mark.asyncio
async def test_run_agent_skips_metadata_when_langfuse_disabled(monkeypatch):
    fake_agent = _FakeAgent()

    def agent_factory(config):
        return fake_agent

    record = RunRecord(
        run_id="run-3",
        thread_id="thread-noop",
        assistant_id="lead-agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        user_id="test-user-autouse",
    )
    record.abort_event = asyncio.Event()
    ctx = RunContext(checkpointer=None)

    await run_agent(
        _FakeBridge(),
        _FakeRunManager(record),
        record,
        owner_user=_owner_user_for(record),
        ctx=ctx,
        agent_factory=agent_factory,
        graph_input={"messages": []},
        config={"configurable": {"thread_id": "thread-noop"}},
    )

    metadata = fake_agent.captured_config.get("metadata") or {}
    assert "langfuse_session_id" not in metadata
    assert "langfuse_user_id" not in metadata
    assert "langfuse_trace_name" not in metadata
