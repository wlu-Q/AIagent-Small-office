"""Tests for RunJournal callback handler.

Uses MemoryRunEventStore as the backend for direct event inspection.
"""

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY


@pytest.fixture
def journal_setup():
    store = MemoryRunEventStore()
    j = RunJournal("r1", "t1", store, user_id="test-user-autouse", flush_threshold=100)
    return j, store


def _make_llm_response(content="Hello", usage=None, tool_calls=None, additional_kwargs=None):
    """Create a mock LLM response with a message.

    model_dump() returns checkpoint-aligned format matching real AIMessage.
    """
    msg = MagicMock()
    msg.type = "ai"
    msg.content = content
    msg.id = f"msg-{id(msg)}"
    msg.tool_calls = tool_calls or []
    msg.invalid_tool_calls = []
    msg.response_metadata = {"model_name": "test-model"}
    msg.usage_metadata = usage
    msg.additional_kwargs = additional_kwargs or {}
    msg.name = None
    # model_dump returns checkpoint-aligned format
    msg.model_dump.return_value = {
        "content": content,
        "additional_kwargs": additional_kwargs or {},
        "response_metadata": {"model_name": "test-model"},
        "type": "ai",
        "name": None,
        "id": msg.id,
        "tool_calls": tool_calls or [],
        "invalid_tool_calls": [],
        "usage_metadata": usage,
    }

    gen = MagicMock()
    gen.message = msg

    response = MagicMock()
    response.generations = [[gen]]
    return response


class TestLlmCallbacks:
    @pytest.mark.anyio
    async def test_employee_diagnostics_bridge_does_not_double_count_or_emit_chat(self, journal_setup):
        from deerflow.subagents.token_collector import SubagentTokenCollector

        collector = SubagentTokenCollector("subagent:frontend")
        rid = uuid4()
        collector.on_chat_model_start({}, [[HumanMessage(content="private task")]], run_id=rid, invocation_params={"tools": []})
        collector.on_llm_end(_make_llm_response(usage={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}), run_id=rid)
        j, store = journal_setup
        records = collector.snapshot_records()
        j.record_external_llm_usage_records(records)
        j.record_external_llm_usage_records(records)
        await j.flush()
        events = await store.list_events("t1", "r1")
        assert len(events) == 1
        assert events[0]["event_type"] == "llm.external.usage"
        assert events[0]["category"] == "trace"
        assert events[0]["metadata"]["input_context"]["message_count"] == 1
        assert events[0]["metadata"]["caller"] == "subagent:frontend"
        assert "private task" not in str(events)
        assert j.get_completion_data()["total_tokens"] == 110

    @pytest.mark.anyio
    async def test_diagnostic_failure_does_not_block_normal_events(self, journal_setup, monkeypatch):
        from deerflow.runtime import context_metrics
        def fail(*args, **kwargs):
            raise ValueError('private provider content')
        monkeypatch.setattr(context_metrics, 'measure_context', fail)
        j, store = journal_setup
        rid = uuid4()
        j.on_chat_model_start({}, [[HumanMessage(content='request')]], run_id=rid, tags=['lead_agent'])
        j.on_llm_end(_make_llm_response(), run_id=rid, tags=['lead_agent'])
        await j.flush()
        events = await store.list_events('t1', 'r1')
        assert any(e['event_type'] == 'llm.human.input' for e in events)
        event = next(e for e in events if e['event_type'] == 'llm.ai.response')
        assert event['metadata']['input_context'] == {'unavailable': True, 'reason': 'ValueError'}
        assert 'private provider content' not in str(events)

    @pytest.mark.anyio
    async def test_parallel_model_inputs_keep_their_own_metrics_and_cache_usage(self, journal_setup):
        from langchain_core.messages import SystemMessage
        j, store = journal_setup
        first, second = uuid4(), uuid4()
        j.on_chat_model_start({}, [[SystemMessage(content='confidential'), HumanMessage(content='first')]],
                              run_id=first, tags=['lead_agent'], invocation_params={'tools': []})
        j.on_chat_model_start({}, [[HumanMessage(content='second request')]],
                              run_id=second, tags=['subagent:worker'])
        j.on_llm_end(_make_llm_response('second answer'), run_id=second, tags=['subagent:worker'])
        j.on_llm_end(_make_llm_response('first answer', usage={
            'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12,
            'input_token_details': {'cache_read': 8}}), run_id=first, tags=['lead_agent'])
        await j.flush()
        events = [e for e in await store.list_events('t1', 'r1') if e['event_type'] == 'llm.ai.response']
        by_caller = {e['metadata']['caller']: e['metadata'] for e in events}
        lead = by_caller['lead_agent']
        assert lead['llm_call_index'] == 1
        assert lead['input_context']['message_chars'] == len('confidentialfirst')
        assert lead['usage']['input_token_details']['cache_read'] == 8
        assert by_caller['subagent:worker']['input_context']['message_chars'] == len('second request')
        assert 'confidential' not in str(lead)
        assert not j._input_metrics and not j._call_indices

    @pytest.mark.anyio
    async def test_terminal_middleware_notice_uses_final_graph_message_without_double_counting(self, journal_setup):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult
        from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
        j, store = journal_setup
        original = AIMessage(content='', id='current-ai', tool_calls=[{'name': 'task', 'id': 'rejected', 'args': {}}],
                             usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
        j.on_llm_end(LLMResult(generations=[[ChatGeneration(message=original)]]), run_id=uuid4(), tags=['lead_agent'])
        before = j.get_completion_data()
        runtime = MagicMock(context={'run_id': 'r1'})
        changed = SubagentLimitMiddleware(max_total=1)._truncate_task_calls(
            {'messages': [original], 'delegations': [{'id': 'previous', 'run_id': 'r1'}]}, runtime)['messages'][0]
        j.on_chain_end({'messages': [changed]}, run_id=uuid4())
        await j.flush()
        after = j.get_completion_data()
        assert 'SUBAGENT LIMIT REACHED' in after['last_ai_message']
        assert after['message_count'] == before['message_count']
        assert after['total_tokens'] == before['total_tokens'] == 15
        assert after['llm_call_count'] == before['llm_call_count']
        from deerflow.runtime.runs.worker import _event_completion_seed
        recovered = _event_completion_seed(await store.list_events('t1', 'r1'))
        assert recovered['last_ai_message'] == after['last_ai_message']
        assert recovered['total_tokens'] == after['total_tokens']
        assert recovered['message_count'] == after['message_count']
        assert recovered['llm_call_count'] == after['llm_call_count']

    @pytest.mark.anyio
    @pytest.mark.parametrize('source', ['history', 'subagent', 'hidden'])
    async def test_final_state_cannot_promote_other_sources_to_lead_answer(self, journal_setup, source):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult
        j, _ = journal_setup
        lead = AIMessage(content='Lead answer', id='lead')
        j.on_llm_end(LLMResult(generations=[[ChatGeneration(message=lead)]]), run_id=uuid4(), tags=['lead_agent'])
        final = AIMessage(content='Unrelated', id='lead' if source == 'hidden' else source,
                          additional_kwargs={'hide_from_ui': source == 'hidden'})
        if source == 'subagent':
            j.on_llm_end(LLMResult(generations=[[ChatGeneration(message=final)]]), run_id=uuid4(), tags=['subagent:writer'])
        j.on_chain_end({'messages': [final]}, run_id=uuid4())
        assert j.get_completion_data()['last_ai_message'] == 'Lead answer'

    @pytest.mark.anyio
    async def test_journal_redacts_run_secret_from_events_and_completion_data(self):
        secret = 'fake-journal-api-key-value'
        store = MemoryRunEventStore()
        journal = RunJournal(
            'r-secret',
            't-secret',
            store, user_id="test-user-autouse",
            flush_threshold=100,
            secret_values={secret},
        )

        journal._put(
            event_type='security.probe',
            category='test',
            content={'message': f'provider returned {secret}'},
            metadata={'authorization': f'Bearer {secret}'},
        )
        journal.on_chain_error(
            RuntimeError(f'provider rejected {secret}'),
            run_id=uuid4(),
        )
        journal.set_first_human_message(f'user accidentally echoed {secret}')
        journal._last_ai_msg = f'model accidentally echoed {secret}'
        await journal.flush()

        events = await store.list_events('t-secret', 'r-secret')
        assert secret not in repr(events)
        assert secret not in repr(journal.get_completion_data())
        assert '[redacted]' in repr(events)

    @pytest.mark.anyio
    async def test_on_chat_model_start_persists_original_user_input_without_mutating_model_message(self, journal_setup):
        j, store = journal_setup
        wrapped_content = "--- BEGIN USER INPUT ---\nShow revenue\n--- END USER INPUT ---"
        model_message = HumanMessage(
            content=wrapped_content,
            id="human-1",
            additional_kwargs={ORIGINAL_USER_CONTENT_KEY: "Show revenue", "channel": "web"},
        )

        j.on_chat_model_start({}, [[model_message]], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg == "Show revenue"
        events = await store.list_events("t1", "r1")
        human_event = next(event for event in events if event["event_type"] == "llm.human.input")
        assert human_event["content"]["content"] == "Show revenue"
        assert human_event["content"]["id"] == "human-1"
        assert human_event["content"]["additional_kwargs"] == {"channel": "web"}
        assert model_message.content == wrapped_content
        assert model_message.additional_kwargs[ORIGINAL_USER_CONTENT_KEY] == "Show revenue"

    @pytest.mark.anyio
    async def test_resume_journal_does_not_persist_checkpoint_human_message_as_new_input(self):
        store = MemoryRunEventStore()
        journal = RunJournal(
            "r-resume",
            "t-resume",
            store,
            user_id="test-user-autouse",
            flush_threshold=100,
            is_resume=True,
        )
        checkpoint_message = HumanMessage(
            content="@PM 我想做一个新的管理系统。",
            id="checkpoint-human-1",
        )

        journal.on_chat_model_start(
            {},
            [[checkpoint_message]],
            run_id=uuid4(),
            tags=["lead_agent"],
        )
        journal.record_human_input_response(
            {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": "clarification:call-1",
                "response_kind": "text",
                "value": "做一个面向小团队的日程管理系统。",
            }
        )
        await journal.flush()

        events = await store.list_events("t-resume", "r-resume")
        assert not any(event["event_type"] == "llm.human.input" for event in events)
        response_event = next(
            event
            for event in events
            if event["event_type"] == "runtime.human_input.responded"
        )
        assert response_event["content"]["value"] == "做一个面向小团队的日程管理系统。"

    @pytest.mark.anyio
    async def test_on_llm_end_produces_trace_event(self, journal_setup):
        j, store = journal_setup
        run_id = uuid4()
        j.on_llm_start({}, [], run_id=run_id, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("Hi"), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        await j.flush()
        events = await store.list_events("t1", "r1")
        trace_events = [e for e in events if e["event_type"] == "llm.ai.response"]
        assert len(trace_events) == 1
        assert trace_events[0]["category"] == "message"

    @pytest.mark.anyio
    async def test_on_llm_end_lead_agent_produces_ai_message(self, journal_setup):
        j, store = journal_setup
        run_id = uuid4()
        j.on_llm_start({}, [], run_id=run_id, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("Answer"), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        await j.flush()
        messages = await store.list_messages("t1")
        assert len(messages) == 1
        assert messages[0]["event_type"] == "llm.ai.response"
        # Content is checkpoint-aligned model_dump format
        assert messages[0]["content"]["type"] == "ai"
        assert messages[0]["content"]["content"] == "Answer"

    @pytest.mark.anyio
    async def test_on_llm_end_with_tool_calls_produces_ai_tool_call(self, journal_setup):
        """LLM response with pending tool_calls emits llm.ai.response with tool_calls in content."""
        j, store = journal_setup
        run_id = uuid4()
        j.on_llm_end(
            _make_llm_response("Let me search", tool_calls=[{"id": "call_1", "name": "search", "args": {}}]),
            run_id=run_id,
            parent_run_id=None,
            tags=["lead_agent"],
        )
        await j.flush()
        messages = await store.list_messages("t1")
        assert len(messages) == 1
        assert messages[0]["event_type"] == "llm.ai.response"
        assert len(messages[0]["content"]["tool_calls"]) == 1

    @pytest.mark.anyio
    async def test_on_llm_end_subagent_no_ai_message(self, journal_setup):
        j, store = journal_setup
        run_id = uuid4()
        j.on_llm_start({}, [], run_id=run_id, tags=["subagent:research"])
        j.on_llm_end(_make_llm_response("Sub answer"), run_id=run_id, parent_run_id=None, tags=["subagent:research"])
        await j.flush()
        messages = await store.list_messages("t1")
        # subagent responses still emit llm.ai.response with category="message"
        assert len(messages) == 1

    @pytest.mark.anyio
    async def test_token_accumulation(self, journal_setup):
        j, store = journal_setup
        usage1 = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        usage2 = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
        j.on_llm_end(_make_llm_response("A", usage=usage1), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("B", usage=usage2), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        assert j._total_input_tokens == 30
        assert j._total_output_tokens == 15
        assert j._total_tokens == 45
        assert j._llm_call_count == 2

    @pytest.mark.anyio
    async def test_total_tokens_computed_from_input_output(self, journal_setup):
        """If total_tokens is 0, it should be computed from input + output."""
        j, store = journal_setup
        j.on_llm_end(
            _make_llm_response("Hi", usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 0}),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        assert j._total_tokens == 150

    @pytest.mark.anyio
    async def test_caller_token_classification(self, journal_setup):
        j, store = journal_setup
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("A", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("B", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["subagent:research"])
        j.on_llm_end(_make_llm_response("C", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["middleware:summarization"])
        # token tracking not broken by caller type
        assert j._total_tokens == 45
        assert j._llm_call_count == 3

    @pytest.mark.anyio
    async def test_usage_metadata_none_no_crash(self, journal_setup):
        j, store = journal_setup
        j.on_llm_end(_make_llm_response("No usage", usage=None), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        await j.flush()

    @pytest.mark.anyio
    async def test_latency_tracking(self, journal_setup):
        j, store = journal_setup
        run_id = uuid4()
        j.on_llm_start({}, [], run_id=run_id, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("Fast"), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        await j.flush()
        events = await store.list_events("t1", "r1")
        llm_resp = [e for e in events if e["event_type"] == "llm.ai.response"][0]
        assert "latency_ms" in llm_resp["metadata"]
        assert llm_resp["metadata"]["latency_ms"] is not None


class TestLifecycleCallbacks:
    @pytest.mark.anyio
    async def test_chain_start_end_produce_trace_events(self, journal_setup):
        j, store = journal_setup
        j.on_chain_start({}, {}, run_id=uuid4(), parent_run_id=None)
        j.on_chain_end({}, run_id=uuid4())
        await asyncio.sleep(0.05)
        await j.flush()
        events = await store.list_events("t1", "r1")
        types = {e["event_type"] for e in events}
        assert "run.start" in types
        assert "run.end" in types

    @pytest.mark.anyio
    async def test_nested_chain_no_run_lifecycle_events(self, journal_setup):
        """Nested chains (parent_run_id set) should NOT produce root run lifecycle events."""
        j, store = journal_setup
        parent_id = uuid4()
        j.on_chain_start({}, {}, run_id=uuid4(), parent_run_id=parent_id)
        j.on_chain_end({}, run_id=uuid4(), parent_run_id=parent_id)
        await j.flush()
        events = await store.list_events("t1", "r1")
        assert not any(e["event_type"] == "run.start" for e in events)
        assert not any(e["event_type"] == "run.end" for e in events)


class TestToolCallbacks:
    @pytest.mark.anyio
    async def test_tool_end_with_tool_message(self, journal_setup):
        """on_tool_end with a ToolMessage stores it as llm.tool.result."""
        from langchain_core.messages import ToolMessage

        j, store = journal_setup
        tool_msg = ToolMessage(content="results", tool_call_id="call_1", name="web_search")
        j.on_tool_end(tool_msg, run_id=uuid4())
        await j.flush()
        messages = await store.list_messages("t1")
        assert len(messages) == 1
        assert messages[0]["event_type"] == "llm.tool.result"
        assert messages[0]["content"]["type"] == "tool"

    @pytest.mark.anyio
    async def test_tool_end_with_command_unwraps_tool_message(self, journal_setup):
        """on_tool_end with Command(update={'messages':[ToolMessage]}) unwraps inner message."""
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        inner = ToolMessage(content="file list", tool_call_id="call_2", name="present_files")
        cmd = Command(update={"messages": [inner]})
        j.on_tool_end(cmd, run_id=uuid4())
        await j.flush()
        messages = await store.list_messages("t1")
        assert len(messages) == 1
        assert messages[0]["event_type"] == "llm.tool.result"
        assert messages[0]["content"]["content"] == "file list"

    @pytest.mark.anyio
    async def test_on_tool_error_no_crash(self, journal_setup):
        """on_tool_error should not crash (no event emitted by default)."""
        j, store = journal_setup
        j.on_tool_error(TimeoutError("timeout"), run_id=uuid4(), name="web_fetch")
        await j.flush()
        # Base implementation does not emit tool_error — just verify no crash
        events = await store.list_events("t1", "r1")
        assert isinstance(events, list)


class TestFinalToolMessageReconciliation:
    @pytest.mark.anyio
    async def test_root_chain_end_reconciles_missing_ask_clarification_tool_message(self, journal_setup):
        from langchain_core.messages import ToolMessage

        j, store = journal_setup
        j.on_llm_end(
            _make_llm_response("", tool_calls=[{"id": "call_clarify", "name": "ask_clarification", "args": {"question": "Which format?"}}]),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        tool_msg = ToolMessage(
            content="Which format?",
            tool_call_id="call_clarify",
            name="ask_clarification",
            artifact={"human_input": {"kind": "human_input_request", "request_id": "clarification:call_clarify"}},
        )

        j.on_chain_end({"messages": [tool_msg]}, run_id=uuid4())
        await j.flush()

        messages = await store.list_messages("t1")
        tool_results = [m for m in messages if m["event_type"] == "llm.tool.result"]
        assert len(tool_results) == 1
        assert tool_results[0]["content"]["name"] == "ask_clarification"
        assert tool_results[0]["content"]["artifact"]["human_input"]["request_id"] == "clarification:call_clarify"

    @pytest.mark.anyio
    async def test_root_chain_end_does_not_duplicate_tool_message_captured_by_on_tool_end(self, journal_setup):
        from langchain_core.messages import ToolMessage

        j, store = journal_setup
        j.on_llm_end(
            _make_llm_response("", tool_calls=[{"id": "call_clarify", "name": "ask_clarification", "args": {"question": "Which format?"}}]),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        tool_msg = ToolMessage(content="Which format?", tool_call_id="call_clarify", name="ask_clarification")

        j.on_tool_end(tool_msg, run_id=uuid4())
        j.on_chain_end({"messages": [tool_msg]}, run_id=uuid4())
        await j.flush()

        messages = await store.list_messages("t1")
        tool_results = [m for m in messages if m["event_type"] == "llm.tool.result"]
        assert len(tool_results) == 1

    @pytest.mark.anyio
    async def test_root_chain_end_ignores_retained_old_tool_message_from_previous_run(self, journal_setup):
        from langchain_core.messages import ToolMessage

        j, store = journal_setup
        j.on_llm_end(
            _make_llm_response("", tool_calls=[{"id": "call_current", "name": "ask_clarification", "args": {"question": "Current?"}}]),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        retained_old_tool_msg = ToolMessage(content="Old question", tool_call_id="call_old", name="ask_clarification")

        j.on_chain_end({"messages": [retained_old_tool_msg]}, run_id=uuid4())
        await j.flush()

        messages = await store.list_messages("t1")
        assert not any(m["event_type"] == "llm.tool.result" for m in messages)

    @pytest.mark.anyio
    async def test_root_chain_end_ignores_non_allowlisted_tool_message(self, journal_setup):
        from langchain_core.messages import ToolMessage

        j, store = journal_setup
        j.on_llm_end(
            _make_llm_response("", tool_calls=[{"id": "call_search", "name": "web_search", "args": {"query": "deerflow"}}]),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        tool_msg = ToolMessage(content="Search result", tool_call_id="call_search", name="web_search")

        j.on_chain_end({"messages": [tool_msg]}, run_id=uuid4())
        await j.flush()

        messages = await store.list_messages("t1")
        assert not any(m["event_type"] == "llm.tool.result" for m in messages)

    @pytest.mark.anyio
    async def test_root_chain_end_ignores_hidden_ask_clarification_tool_message(self, journal_setup):
        from langchain_core.messages import ToolMessage

        j, store = journal_setup
        j.on_llm_end(
            _make_llm_response("", tool_calls=[{"id": "call_clarify", "name": "ask_clarification", "args": {"question": "Hidden?"}}]),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        tool_msg = ToolMessage(
            content="Hidden?",
            tool_call_id="call_clarify",
            name="ask_clarification",
            additional_kwargs={"hide_from_ui": True},
        )

        j.on_chain_end({"messages": [tool_msg]}, run_id=uuid4())
        await j.flush()

        messages = await store.list_messages("t1")
        assert not any(m["event_type"] == "llm.tool.result" for m in messages)

    @pytest.mark.anyio
    async def test_trusted_human_response_is_recorded_before_the_next_model_response(
        self, journal_setup
    ):
        j, store = journal_setup
        response = {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-current",
            "response_kind": "option",
            "option_id": "option-1",
            "value": "SQLite",
        }
        j.record_human_input_response(response)
        j.on_llm_end(
            _make_llm_response("最终选择 SQLite"),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        await j.flush()

        events = await store.list_events("t1", "r1")
        responses = [
            event
            for event in events
            if event["event_type"] == "runtime.human_input.responded"
        ]
        assert len(responses) == 1
        assert responses[0]["content"] == response
        assert [event["event_type"] for event in events] == [
            "runtime.human_input.responded",
            "llm.ai.response",
        ]
        assert not any(event["event_type"] == "llm.tool.result" for event in events)

    @pytest.mark.anyio
    async def test_expected_graph_interrupt_is_not_recorded_as_run_error(
        self, journal_setup
    ):
        from langgraph.errors import GraphInterrupt

        j, store = journal_setup

        j.on_chain_error(GraphInterrupt(()), run_id=uuid4())
        await j.flush()

        events = await store.list_events("t1", "r1")
        assert not any(event["event_type"] == "run.error" for event in events)


class TestCustomEvents:
    @pytest.mark.anyio
    async def test_on_custom_event_not_implemented(self, journal_setup):
        """RunJournal does not implement on_custom_event — no crash expected."""
        j, store = journal_setup
        # BaseCallbackHandler.on_custom_event is a no-op by default
        j.on_custom_event("task_running", {"task_id": "t1"}, run_id=uuid4())
        await j.flush()
        events = await store.list_events("t1", "r1")
        assert isinstance(events, list)


class TestBufferFlush:
    @pytest.mark.anyio
    async def test_flush_threshold(self, journal_setup):
        j, store = journal_setup
        j._flush_threshold = 2
        # Each on_llm_end emits 1 event
        j.on_llm_end(_make_llm_response("A"), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        assert len(j._buffer) == 1
        j.on_llm_end(_make_llm_response("B"), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        # At threshold the buffer should have been flushed asynchronously
        await asyncio.sleep(0.1)
        events = await store.list_events("t1", "r1")
        assert len(events) >= 2

    @pytest.mark.anyio
    async def test_events_retained_when_no_loop(self, journal_setup):
        """Events buffered in a sync (no-loop) context should survive
        until the async flush() in the finally block."""
        j, store = journal_setup
        j._flush_threshold = 1

        original = asyncio.get_running_loop

        def no_loop():
            raise RuntimeError("no running event loop")

        asyncio.get_running_loop = no_loop
        try:
            j._put(event_type="llm.ai.response", category="message", content="test")
        finally:
            asyncio.get_running_loop = original

        assert len(j._buffer) == 1
        await j.flush()
        events = await store.list_events("t1", "r1")
        assert any(e["event_type"] == "llm.ai.response" for e in events)

    @pytest.mark.anyio
    async def test_durable_flush_drains_events_buffered_while_write_is_in_flight(self):
        class BlockingStore(MemoryRunEventStore):
            def __init__(self):
                super().__init__()
                self.first_write_started = asyncio.Event()
                self.release_first_write = asyncio.Event()
                self.second_write_finished = asyncio.Event()
                self.write_count = 0

            async def put_batch(self, events, *, user_id):
                self.write_count += 1
                write_number = self.write_count
                if write_number == 1:
                    self.first_write_started.set()
                    await self.release_first_write.wait()
                result = await super().put_batch(events, user_id=user_id)
                if write_number == 2:
                    self.second_write_finished.set()
                return result

        store = BlockingStore()
        journal = RunJournal('r1', 't1', store, user_id="test-user-autouse", flush_threshold=1)

        journal._put(event_type='run.start', category='trace', content='first')
        await asyncio.wait_for(store.first_write_started.wait(), timeout=1)

        journal._put(event_type='llm.human.input', category='message', content='second')
        assert len(journal._buffer) == 1

        store.release_first_write.set()
        await asyncio.wait_for(store.second_write_finished.wait(), timeout=1)

        events = await store.list_events('t1', 'r1')
        assert [event['content'] for event in events] == ['first', 'second']
        assert journal._buffer == []

    @pytest.mark.anyio
    async def test_threshold_flush_returns_from_callback_thread_to_owner_loop(self):
        store = MemoryRunEventStore()
        journal = RunJournal('r1', 't1', store, user_id="test-user-autouse", flush_threshold=1)

        await asyncio.to_thread(
            journal._put,
            event_type='llm.tool.result',
            category='message',
            content='persisted-before-run-end',
        )

        deadline = asyncio.get_running_loop().time() + 1
        events = []
        while asyncio.get_running_loop().time() < deadline:
            events = await store.list_events('t1', 'r1')
            if events:
                break
            await asyncio.sleep(0.01)

        assert [event['content'] for event in events] == [
            'persisted-before-run-end'
        ]
        assert journal._buffer == []

    @pytest.mark.anyio
    async def test_failed_background_flush_requeues_for_final_retry(self):
        class FailOnceStore(MemoryRunEventStore):
            def __init__(self):
                super().__init__()
                self.write_count = 0

            async def put_batch(self, events, *, user_id):
                self.write_count += 1
                if self.write_count == 1:
                    raise RuntimeError('temporary write failure')
                return await super().put_batch(events, user_id=user_id)

        store = FailOnceStore()
        journal = RunJournal('r1', 't1', store, user_id="test-user-autouse", flush_threshold=1)

        journal._put(
            event_type='llm.tool.result',
            category='message',
            content='retry-without-loss',
        )

        deadline = asyncio.get_running_loop().time() + 1
        while asyncio.get_running_loop().time() < deadline:
            if not journal._pending_flush_tasks:
                break
            await asyncio.sleep(0.01)

        assert store.write_count == 1
        assert [event['content'] for event in journal._buffer] == [
            'retry-without-loss'
        ]

        await journal.flush()

        events = await store.list_events('t1', 'r1')
        assert store.write_count == 2
        assert [event['content'] for event in events] == ['retry-without-loss']
        assert journal._buffer == []


@pytest.mark.anyio
async def test_office_public_prose_flushes_before_next_tool_finishes_and_survives_inflight_write():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    started, release = asyncio.Event(), asyncio.Event()
    class SlowStore(MemoryRunEventStore):
        async def put_batch(self, events, *, user_id):
            started.set()
            await release.wait()
            return await super().put_batch(events, user_id=user_id)
    store = SlowStore()
    journal = RunJournal('r1', 't1', store, user_id='test-user-autouse', flush_threshold=100, flush_public_messages=True)
    def emit(identity, content):
        message = AIMessage(id=identity, content=content, tool_calls=[{'name': 'read_file', 'args': {'path': 'README.md'}, 'id': identity + '-tool'}],
                            usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
        journal.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=uuid4(), tags=['lead_agent'])
    await asyncio.to_thread(emit, 'one', '我先检查项目。')
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.to_thread(emit, 'two', [{'type': 'reasoning', 'text': 'PRIVATE'}, {'type': 'text', 'text': '开始修改。'}])
    release.set()
    for _ in range(100):
        events = await store.list_events('t1', 'r1')
        if len(events) == 2 and not journal._pending_flush_tasks:
            break
        await asyncio.sleep(.01)
    assert [event['content']['id'] for event in events] == ['one', 'two']
    assert journal._buffer == []
    assert journal.get_completion_data()['last_ai_message'] == '开始修改。'
    assert journal.get_completion_data()['total_tokens'] == 30


@pytest.mark.anyio
async def test_public_flush_does_not_promote_hidden_or_non_office_events():
    for enabled in (True, False):
        store = MemoryRunEventStore()
        journal = RunJournal('r1', 't1', store, user_id='test-user-autouse', flush_threshold=100, flush_public_messages=enabled)
        journal._put(event_type='llm.ai.response', category='message', content={'content': 'secret', 'additional_kwargs': {'hidden': True}}, metadata={'caller': 'lead_agent'})
        journal._put(event_type='llm.ai.response', category='message', content={'content': 'internal'}, metadata={'caller': 'middleware:summary'})
        if not enabled:
            journal._put(event_type='llm.ai.response', category='message', content={'content': 'ordinary'}, metadata={'caller': 'lead_agent'})
        await asyncio.sleep(.02)
        assert await store.list_events('t1', 'r1') == []
        await journal.flush()


class TestIdentifyCaller:
    def test_lead_agent_tag(self, journal_setup):
        j, _ = journal_setup
        assert j._identify_caller(["lead_agent"]) == "lead_agent"

    def test_subagent_tag(self, journal_setup):
        j, _ = journal_setup
        assert j._identify_caller(["subagent:research"]) == "subagent:research"

    def test_middleware_tag(self, journal_setup):
        j, _ = journal_setup
        assert j._identify_caller(["middleware:summarization"]) == "middleware:summarization"

    def test_no_tags_returns_lead_agent(self, journal_setup):
        j, _ = journal_setup
        assert j._identify_caller([]) == "lead_agent"
        assert j._identify_caller(None) == "lead_agent"


class TestChainErrorCallback:
    @pytest.mark.anyio
    async def test_on_chain_error_writes_run_error(self, journal_setup):
        j, store = journal_setup
        j.on_chain_error(ValueError("boom"), run_id=uuid4())
        await asyncio.sleep(0.05)
        await j.flush()
        events = await store.list_events("t1", "r1")
        error_events = [e for e in events if e["event_type"] == "run.error"]
        assert len(error_events) == 1
        assert "boom" in error_events[0]["content"]
        assert error_events[0]["metadata"]["error_type"] == "ValueError"


class TestTokenTrackingDisabled:
    @pytest.mark.anyio
    async def test_track_token_usage_false(self):
        store = MemoryRunEventStore()
        j = RunJournal("r1", "t1", store, user_id="test-user-autouse", track_token_usage=False, flush_threshold=100)
        j.on_llm_end(
            _make_llm_response("X", usage={"input_tokens": 50, "output_tokens": 50, "total_tokens": 100}),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        data = j.get_completion_data()
        assert data["total_tokens"] == 0
        assert data["llm_call_count"] == 0


class TestConvenienceFields:
    @pytest.mark.anyio
    async def test_first_human_message_via_set(self, journal_setup):
        j, _ = journal_setup
        j.set_first_human_message("What is AI?")
        data = j.get_completion_data()
        assert data["first_human_message"] == "What is AI?"

    @pytest.mark.anyio
    async def test_completion_data_counts_human_ai_and_tool_messages(self, journal_setup):
        from langchain_core.messages import HumanMessage, ToolMessage

        j, _ = journal_setup
        j.on_chat_model_start({}, [[HumanMessage(content="Question")]], run_id=uuid4(), tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("Answer"), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        j.on_tool_end(ToolMessage(content="Tool result", tool_call_id="call_1", name="search"), run_id=uuid4())

        data = j.get_completion_data()

        assert data["message_count"] == 3
        assert data["first_human_message"] == "Question"
        assert data["last_ai_message"] == "Answer"

    @pytest.mark.anyio
    async def test_tool_call_only_ai_does_not_clear_last_ai_message(self, journal_setup):
        j, _ = journal_setup
        j.on_llm_end(_make_llm_response("Useful answer"), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        j.on_llm_end(
            _make_llm_response("", tool_calls=[{"id": "call_1", "name": "search", "args": {}}]),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )

        data = j.get_completion_data()

        assert data["message_count"] == 2
        assert data["last_ai_message"] == "Useful answer"

    @pytest.mark.anyio
    async def test_last_ai_message_extracts_mixed_content_without_extra_newlines(self, journal_setup):
        j, _ = journal_setup
        j.on_llm_end(
            _make_llm_response(
                [
                    {"type": "text", "text": "First "},
                    {"type": "text", "content": "second"},
                    " third",
                    {"type": "image", "url": "ignored"},
                ]
            ),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )

        data = j.get_completion_data()

        assert data["message_count"] == 1
        assert data["last_ai_message"] == "First second third"

    @pytest.mark.anyio
    async def test_last_ai_message_extracts_mapping_content(self, journal_setup):
        j, _ = journal_setup
        j.on_llm_end(_make_llm_response({"content": "Nested answer"}), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])

        data = j.get_completion_data()

        assert data["message_count"] == 1
        assert data["last_ai_message"] == "Nested answer"

    @pytest.mark.anyio
    async def test_duplicate_llm_run_id_does_not_double_count_message_summary(self, journal_setup):
        j, _ = journal_setup
        run_id = uuid4()

        j.on_llm_end(_make_llm_response("Answer", usage=None), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        j.on_llm_end(
            _make_llm_response("Answer", usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
            run_id=run_id,
            parent_run_id=None,
            tags=["lead_agent"],
        )

        data = j.get_completion_data()

        assert data["message_count"] == 1
        assert data["last_ai_message"] == "Answer"
        assert data["total_tokens"] == 15

    @pytest.mark.anyio
    async def test_subagent_ai_does_not_overwrite_lead_last_ai_message(self, journal_setup):
        j, _ = journal_setup
        j.on_llm_end(_make_llm_response("Lead answer"), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("Subagent detail"), run_id=uuid4(), parent_run_id=None, tags=["subagent:research"])

        data = j.get_completion_data()

        assert data["message_count"] == 2
        assert data["last_ai_message"] == "Lead answer"

    @pytest.mark.anyio
    async def test_get_completion_data(self, journal_setup):
        j, _ = journal_setup
        j._total_tokens = 100
        j._msg_count = 5
        data = j.get_completion_data()
        assert data["total_tokens"] == 100
        assert data["message_count"] == 5


class TestMiddlewareEvents:
    @pytest.mark.anyio
    async def test_record_middleware_uses_middleware_category(self, journal_setup):
        j, store = journal_setup
        j.record_middleware(
            "title",
            name="TitleMiddleware",
            hook="after_model",
            action="generate_title",
            changes={"title": "Test Title", "thread_id": "t1"},
        )
        await j.flush()
        events = await store.list_events("t1", "r1")
        mw_events = [e for e in events if e["event_type"] == "middleware:title"]
        assert len(mw_events) == 1
        assert mw_events[0]["category"] == "middleware"
        assert mw_events[0]["content"]["name"] == "TitleMiddleware"
        assert mw_events[0]["content"]["hook"] == "after_model"
        assert mw_events[0]["content"]["action"] == "generate_title"
        assert mw_events[0]["content"]["changes"]["title"] == "Test Title"

    @pytest.mark.anyio
    async def test_middleware_tag_variants(self, journal_setup):
        """Different middleware tags produce distinct event_types."""
        j, store = journal_setup
        j.record_middleware("title", name="TitleMiddleware", hook="after_model", action="generate_title", changes={})
        j.record_middleware("guardrail", name="GuardrailMiddleware", hook="before_tool", action="deny", changes={})
        await j.flush()
        events = await store.list_events("t1", "r1")
        event_types = {e["event_type"] for e in events}
        assert "middleware:title" in event_types
        assert "middleware:guardrail" in event_types


class TestCallerBucketing:
    """Tests for caller-bucketed token accumulation (lead_agent / subagent / middleware)."""

    def test_lead_agent_bucketing(self, journal_setup):
        j, _ = journal_setup
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("A", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        assert j._lead_agent_tokens == 15
        assert j._subagent_tokens == 0
        assert j._middleware_tokens == 0

    def test_subagent_bucketing(self, journal_setup):
        j, _ = journal_setup
        usage = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
        j.on_llm_end(_make_llm_response("B", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["subagent:research"])
        assert j._subagent_tokens == 30
        assert j._lead_agent_tokens == 0
        assert j._middleware_tokens == 0

    def test_middleware_bucketing(self, journal_setup):
        j, _ = journal_setup
        usage = {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
        j.on_llm_end(_make_llm_response("C", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["middleware:summarize"])
        assert j._middleware_tokens == 7
        assert j._lead_agent_tokens == 0
        assert j._subagent_tokens == 0

    def test_mixed_callers_sum_independently(self, journal_setup):
        j, _ = journal_setup
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("A", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("B", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["subagent:bash"])
        j.on_llm_end(_make_llm_response("C", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["middleware:title"])
        assert j._lead_agent_tokens == 15
        assert j._subagent_tokens == 15
        assert j._middleware_tokens == 15
        assert j._total_tokens == 45

    def test_get_completion_data_includes_buckets(self, journal_setup):
        j, _ = journal_setup
        j._lead_agent_tokens = 100
        j._subagent_tokens = 200
        j._middleware_tokens = 50
        data = j.get_completion_data()
        assert data["lead_agent_tokens"] == 100
        assert data["subagent_tokens"] == 200
        assert data["middleware_tokens"] == 50

    def test_dedup_same_run_id(self, journal_setup):
        """Same langchain run_id in on_llm_end must not double-count."""
        j, _ = journal_setup
        run_id = uuid4()
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("A", usage=usage), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        j.on_llm_end(_make_llm_response("A", usage=usage), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        assert j._total_tokens == 15
        assert j._lead_agent_tokens == 15
        assert j._llm_call_count == 1

    def test_first_no_usage_second_with_usage(self, journal_setup):
        """First callback with no usage must not block second callback with usage for same run_id."""
        j, _ = journal_setup
        run_id = uuid4()
        j.on_llm_end(_make_llm_response("A", usage=None), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        assert str(run_id) not in j._counted_llm_run_ids
        # Second callback for the same run_id with actual usage must still count
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("A", usage=usage), run_id=run_id, parent_run_id=None, tags=["lead_agent"])
        assert j._total_tokens == 15
        assert j._lead_agent_tokens == 15

    def test_track_token_usage_false_skips_buckets(self):
        """When token tracking is disabled, caller buckets stay at 0."""
        store = MemoryRunEventStore()
        j = RunJournal("r1", "t1", store, user_id="test-user-autouse", track_token_usage=False, flush_threshold=100)
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("X", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["subagent:research"])
        assert j._subagent_tokens == 0
        assert j._lead_agent_tokens == 0

    def test_default_no_tags_buckets_as_lead_agent(self, journal_setup):
        """LLM calls without explicit tags default to lead_agent bucket."""
        j, _ = journal_setup
        usage = {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}
        j.on_llm_end(_make_llm_response("Hi", usage=usage), run_id=uuid4(), parent_run_id=None)
        assert j._lead_agent_tokens == 10
        assert j._subagent_tokens == 0
        assert j._middleware_tokens == 0

    def test_unknown_tag_buckets_as_lead_agent(self, journal_setup):
        """Calls with unrecognized tags (not lead_agent/subagent:/middleware:) go to lead_agent."""
        j, _ = journal_setup
        usage = {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}
        j.on_llm_end(_make_llm_response("Hi", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["some_random_tag"])
        assert j._lead_agent_tokens == 10


class TestExternalUsageRecords:
    """Tests for record_external_llm_usage_records."""

    def test_records_added_to_subagent_bucket(self, journal_setup):
        j, _ = journal_setup
        records = [
            {
                "source_run_id": "ext-1",
                "caller": "subagent:general-purpose",
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            }
        ]
        j.record_external_llm_usage_records(records)
        assert j._subagent_tokens == 150
        assert j._total_tokens == 150
        assert j._total_input_tokens == 100
        assert j._total_output_tokens == 50

    def test_records_added_to_middleware_bucket(self, journal_setup):
        j, _ = journal_setup
        records = [
            {
                "source_run_id": "ext-2",
                "caller": "middleware:summarize",
                "input_tokens": 30,
                "output_tokens": 10,
                "total_tokens": 40,
            }
        ]
        j.record_external_llm_usage_records(records)
        assert j._middleware_tokens == 40
        assert j._lead_agent_tokens == 0
        assert j._subagent_tokens == 0

    def test_records_added_to_lead_agent_bucket(self, journal_setup):
        j, _ = journal_setup
        records = [
            {
                "source_run_id": "ext-3",
                "caller": "lead_agent",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            }
        ]
        j.record_external_llm_usage_records(records)
        assert j._lead_agent_tokens == 15

    def test_dedup_same_source_run_id(self, journal_setup):
        """Same source_run_id must not be double-counted."""
        j, _ = journal_setup
        records = [
            {
                "source_run_id": "dup-1",
                "caller": "subagent:research",
                "input_tokens": 50,
                "output_tokens": 25,
                "total_tokens": 75,
            }
        ]
        j.record_external_llm_usage_records(records)
        j.record_external_llm_usage_records(records)
        assert j._subagent_tokens == 75
        assert j._total_tokens == 75

    def test_total_tokens_missing_computed_from_input_output(self, journal_setup):
        j, _ = journal_setup
        records = [
            {
                "source_run_id": "ext-4",
                "caller": "subagent:bash",
                "input_tokens": 200,
                "output_tokens": 100,
                "total_tokens": 0,
            }
        ]
        j.record_external_llm_usage_records(records)
        assert j._subagent_tokens == 300
        assert j._total_tokens == 300

    def test_total_tokens_zero_no_count(self, journal_setup):
        """Records with zero total and zero input+output must not be counted."""
        j, _ = journal_setup
        records = [
            {
                "source_run_id": "ext-5",
                "caller": "subagent:research",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
        ]
        j.record_external_llm_usage_records(records)
        assert j._total_tokens == 0
        assert j._subagent_tokens == 0

    def test_empty_source_run_id_skipped(self, journal_setup):
        j, _ = journal_setup
        records = [
            {
                "source_run_id": "",
                "caller": "subagent:research",
                "input_tokens": 50,
                "output_tokens": 25,
                "total_tokens": 75,
            }
        ]
        j.record_external_llm_usage_records(records)
        assert j._total_tokens == 0

    def test_multiple_records_in_single_call(self, journal_setup):
        j, _ = journal_setup
        records = [
            {"source_run_id": "r1", "caller": "subagent:gp", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            {"source_run_id": "r2", "caller": "subagent:bash", "input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        ]
        j.record_external_llm_usage_records(records)
        assert j._subagent_tokens == 45
        assert j._total_tokens == 45

    def test_external_records_coexist_with_inline_callbacks(self, journal_setup):
        """External records and inline on_llm_end must not interfere."""
        j, _ = journal_setup
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("A", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        j.record_external_llm_usage_records([{"source_run_id": "ext-6", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150}])
        assert j._lead_agent_tokens == 15
        assert j._subagent_tokens == 150
        assert j._total_tokens == 165

    def test_track_token_usage_false_skips_external_records(self):
        """When token tracking is disabled, external records must not accumulate."""
        store = MemoryRunEventStore()
        j = RunJournal("r1", "t1", store, user_id="test-user-autouse", track_token_usage=False, flush_threshold=100)
        j.record_external_llm_usage_records([{"source_run_id": "ext-7", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150}])
        assert j._total_tokens == 0
        assert j._subagent_tokens == 0


class TestProgressSnapshots:
    @pytest.mark.anyio
    async def test_recovery_seed_preserves_totals_and_suppresses_singleton_events(self):
        store = MemoryRunEventStore()
        journal = RunJournal(
            'r1',
            't1',
            store, user_id="test-user-autouse",
            flush_threshold=100,
            initial_completion_data={
                'total_input_tokens': 100,
                'total_output_tokens': 50,
                'total_tokens': 150,
                'llm_call_count': 2,
                'lead_agent_tokens': 120,
                'subagent_tokens': 30,
                'middleware_tokens': 0,
                'token_usage_by_model': {
                    'mimo-v2.5': {
                        'input_tokens': 100,
                        'output_tokens': 50,
                        'total_tokens': 150,
                    }
                },
                'message_count': 4,
                'last_ai_message': 'existing answer',
                'first_human_message': 'original request',
            },
            existing_event_types={
                'runtime.tools.bound',
                'run.start',
                'llm.human.input',
            },
        )
        llm_run_id = uuid4()

        journal.on_chain_start(
            {'name': 'lead_agent'},
            {},
            run_id=uuid4(),
            parent_run_id=None,
        )
        journal.record_runtime_tools_bound(
            tool_names=['read_file'],
            model_name='office-selected-model',
            runtime_model_id='mimo-v2.5',
        )
        journal.on_chat_model_start(
            {},
            [[HumanMessage(content='original request', id='human-1')]],
            run_id=llm_run_id,
            tags=['lead_agent'],
        )
        journal.on_llm_end(
            _make_llm_response(
                'resumed answer',
                usage={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
            ),
            run_id=llm_run_id,
            parent_run_id=None,
            tags=['lead_agent'],
        )
        await journal.flush()

        events = await store.list_events('t1', 'r1')
        assert [event['event_type'] for event in events] == ['llm.ai.response']
        assert events[0]['metadata']['llm_call_index'] == 3
        assert journal.get_completion_data() == {
            'total_input_tokens': 110,
            'total_output_tokens': 55,
            'total_tokens': 165,
            'llm_call_count': 3,
            'lead_agent_tokens': 135,
            'subagent_tokens': 30,
            'middleware_tokens': 0,
            'token_usage_by_model': {
                'mimo-v2.5': {
                    'input_tokens': 100,
                    'output_tokens': 50,
                    'total_tokens': 150,
                },
                'test-model': {
                    'input_tokens': 10,
                    'output_tokens': 5,
                    'total_tokens': 15,
                },
            },
            'message_count': 5,
            'last_ai_message': 'resumed answer',
            'first_human_message': 'original request',
        }

    @pytest.mark.anyio
    async def test_recovery_writes_missing_human_event_without_double_counting_seeded_message(self):
        store = MemoryRunEventStore()
        journal = RunJournal(
            'r1',
            't1',
            store, user_id="test-user-autouse",
            flush_threshold=100,
            initial_completion_data={
                'message_count': 1,
                'first_human_message': 'original request',
            },
        )

        journal.on_chat_model_start(
            {},
            [[HumanMessage(content='original request', id='human-1')]],
            run_id=uuid4(),
            tags=['lead_agent'],
        )
        await journal.flush()

        events = await store.list_events('t1', 'r1')
        assert [event['event_type'] for event in events] == ['llm.human.input']
        assert journal.get_completion_data()['message_count'] == 1

    @pytest.mark.anyio
    async def test_on_llm_end_reports_progress_snapshot(self):
        snapshots: list[dict] = []

        async def reporter(snapshot: dict) -> None:
            snapshots.append(snapshot)

        store = MemoryRunEventStore()
        j = RunJournal(
            "r1",
            "t1",
            store, user_id="test-user-autouse",
            flush_threshold=100,
            progress_reporter=reporter,
            progress_flush_interval=0,
        )
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        j.on_llm_end(_make_llm_response("Answer", usage=usage), run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        await j.flush()

        assert snapshots
        assert snapshots[-1]["total_tokens"] == 15
        assert snapshots[-1]["llm_call_count"] == 1
        assert snapshots[-1]["message_count"] == 1
        assert snapshots[-1]["last_ai_message"] == "Answer"

    @pytest.mark.anyio
    async def test_throttled_progress_flush_emits_trailing_snapshot(self):
        snapshots: list[dict] = []
        trailing_seen = asyncio.Event()

        async def reporter(snapshot: dict) -> None:
            snapshots.append(snapshot)
            if snapshot["total_tokens"] == 45:
                trailing_seen.set()

        store = MemoryRunEventStore()
        j = RunJournal(
            "r1",
            "t1",
            store, user_id="test-user-autouse",
            flush_threshold=100,
            progress_reporter=reporter,
            progress_flush_interval=0.01,
        )
        j.on_llm_end(
            _make_llm_response("First", usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        j.on_llm_end(
            _make_llm_response("Second", usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        await asyncio.wait_for(trailing_seen.wait(), timeout=1.0)
        await j.flush()

        assert snapshots
        assert snapshots[-1]["total_tokens"] == 45
        assert snapshots[-1]["llm_call_count"] == 2
        assert snapshots[-1]["last_ai_message"] == "Second"

    @pytest.mark.anyio
    async def test_flush_cancels_delayed_progress_without_final_progress_write(self):
        snapshots: list[dict] = []

        async def reporter(snapshot: dict) -> None:
            snapshots.append(snapshot)

        store = MemoryRunEventStore()
        j = RunJournal(
            "r1",
            "t1",
            store, user_id="test-user-autouse",
            flush_threshold=100,
            progress_reporter=reporter,
            progress_flush_interval=10.0,
        )
        j.on_llm_end(
            _make_llm_response("First", usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )
        await asyncio.sleep(0)
        assert snapshots[-1]["total_tokens"] == 15
        j.on_llm_end(
            _make_llm_response("Second", usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}),
            run_id=uuid4(),
            parent_run_id=None,
            tags=["lead_agent"],
        )

        await asyncio.wait_for(j.flush(), timeout=0.2)

        assert snapshots[-1]["total_tokens"] == 15
        assert snapshots[-1]["llm_call_count"] == 1
        assert snapshots[-1]["last_ai_message"] == "First"


class TestChatModelStartHumanMessage:
    """Tests for on_chat_model_start extracting the first human message."""

    @staticmethod
    def _human_input_response(source: str = "ask_clarification") -> dict:
        return {
            "version": 1,
            "kind": "human_input_response",
            "source": source,
            "request_id": "clarification:call-abc",
            "response_kind": "option",
            "option_id": "option-2",
            "value": "staging",
        }

    @pytest.mark.anyio
    async def test_extracts_first_human_message(self, journal_setup):
        """on_chat_model_start captures the first HumanMessage from prompts."""
        from langchain_core.messages import AIMessage, HumanMessage

        j, store = journal_setup
        messages_batch = [
            [HumanMessage(content="What is AI?"), AIMessage(content="Hi there")],
        ]
        j.on_chat_model_start({}, messages_batch, run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg == "What is AI?"
        events = await store.list_events("t1", "r1")
        human_events = [e for e in events if e["event_type"] == "llm.human.input"]
        assert len(human_events) == 1
        assert human_events[0]["content"]["content"] == "What is AI?"

    @pytest.mark.anyio
    async def test_skips_hidden_human_messages(self, journal_setup):
        """HumanMessages hidden from the UI are internal context, not user input."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        messages_batch = [
            [
                HumanMessage(content="What is the weather today?"),
                HumanMessage(
                    content="Your todo list from earlier...",
                    name="todo_reminder",
                    additional_kwargs={"hide_from_ui": True},
                ),
            ],
        ]
        j.on_chat_model_start({}, messages_batch, run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg == "What is the weather today?"
        assert j.get_completion_data()["message_count"] == 1
        events = await store.list_events("t1", "r1")
        human_events = [e for e in events if e["event_type"] == "llm.human.input"]
        assert len(human_events) == 1
        assert human_events[0]["content"]["content"] == "What is the weather today?"

    @pytest.mark.anyio
    async def test_only_hidden_human_messages_are_not_captured(self, journal_setup):
        """A prompt containing only internal HumanMessages has no user input."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        hidden_message = HumanMessage(
            content="Internal context",
            additional_kwargs={"hide_from_ui": True},
        )
        j.on_chat_model_start({}, [[hidden_message]], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg is None
        assert j.get_completion_data()["message_count"] == 0
        events = await store.list_events("t1", "r1")
        assert not any(e["event_type"] == "llm.human.input" for e in events)

    @pytest.mark.anyio
    async def test_hidden_human_input_response_is_captured(self, journal_setup):
        """Hidden HumanInputCard replies are user-authored and must survive compaction."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        hidden_response = HumanMessage(
            content='For your clarification "Which environment?", my answer is: staging',
            additional_kwargs={
                "hide_from_ui": True,
                "human_input_response": self._human_input_response(),
            },
        )
        j.on_chat_model_start({}, [[hidden_response]], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg == 'For your clarification "Which environment?", my answer is: staging'
        assert j.get_completion_data()["message_count"] == 1
        events = await store.list_events("t1", "r1")
        human_events = [e for e in events if e["event_type"] == "llm.human.input"]
        assert len(human_events) == 1
        assert human_events[0]["content"]["additional_kwargs"]["hide_from_ui"] is True
        assert human_events[0]["content"]["additional_kwargs"]["human_input_response"]["request_id"] == "clarification:call-abc"

    @pytest.mark.anyio
    async def test_hidden_human_input_response_wins_over_older_visible_prompt(self, journal_setup):
        """The latest hidden card reply is the run input, not an older visible prompt."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        older_prompt = HumanMessage(content="Write a quicksort PDF")
        hidden_response = HumanMessage(
            content='For your clarification "Which format?", my answer is: tutorial',
            additional_kwargs={
                "hide_from_ui": True,
                "human_input_response": self._human_input_response(),
            },
        )
        j.on_chat_model_start({}, [[older_prompt, hidden_response]], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg == 'For your clarification "Which format?", my answer is: tutorial'
        events = await store.list_events("t1", "r1")
        human_events = [e for e in events if e["event_type"] == "llm.human.input"]
        assert len(human_events) == 1
        assert human_events[0]["content"]["content"] == 'For your clarification "Which format?", my answer is: tutorial'

    @pytest.mark.anyio
    async def test_hidden_human_input_response_ignores_non_allowlisted_source(self, journal_setup):
        """Only explicit HumanInputCard sources are persisted while hidden."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        hidden_response = HumanMessage(
            content="Internal approval response",
            additional_kwargs={
                "hide_from_ui": True,
                "human_input_response": self._human_input_response(source="future_approval"),
            },
        )
        j.on_chat_model_start({}, [[hidden_response]], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg is None
        assert j.get_completion_data()["message_count"] == 0
        events = await store.list_events("t1", "r1")
        assert not any(e["event_type"] == "llm.human.input" for e in events)

    @pytest.mark.anyio
    async def test_legacy_summary_message_is_not_captured_as_user_input(self, journal_setup):
        """Legacy synthetic summaries are internal context even if hide_from_ui is absent."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        legacy_summary = HumanMessage(content="Older compressed conversation state", name="summary")
        j.on_chat_model_start({}, [[legacy_summary]], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg is None
        assert j.get_completion_data()["message_count"] == 0
        events = await store.list_events("t1", "r1")
        assert not any(e["event_type"] == "llm.human.input" for e in events)

    @pytest.mark.anyio
    async def test_visible_human_message_after_hidden_only_prompt_is_captured(self, journal_setup):
        """Skipping an internal-only prompt does not block later user input."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        hidden_message = HumanMessage(
            content="Internal context",
            additional_kwargs={"hide_from_ui": True},
        )
        j.on_chat_model_start({}, [[hidden_message]], run_id=uuid4(), tags=["lead_agent"])
        j.on_chat_model_start(
            {},
            [[HumanMessage(content="Real question")]],
            run_id=uuid4(),
            tags=["lead_agent"],
        )
        await j.flush()

        assert j._first_human_msg == "Real question"
        assert j.get_completion_data()["message_count"] == 1
        events = await store.list_events("t1", "r1")
        human_events = [e for e in events if e["event_type"] == "llm.human.input"]
        assert len(human_events) == 1
        assert human_events[0]["content"]["content"] == "Real question"

    @pytest.mark.anyio
    async def test_summarization_prompt_does_not_capture_first_human_message(self, journal_setup):
        """Internal summarization prompts must not replace the run's real user input."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        summarization_prompt = HumanMessage(
            content="<role>\nContext Extraction Assistant\n</role>\n\n<primary_objective>\nExtract context...",
        )
        j.on_chat_model_start(
            {},
            [[summarization_prompt]],
            run_id=uuid4(),
            tags=["middleware:summarize"],
        )
        j.on_chat_model_start(
            {},
            [[HumanMessage(content="Real user follow-up")]],
            run_id=uuid4(),
            tags=["lead_agent"],
        )
        await j.flush()

        assert j._first_human_msg == "Real user follow-up"
        assert j.get_completion_data()["message_count"] == 1
        events = await store.list_events("t1", "r1")
        human_events = [e for e in events if e["event_type"] == "llm.human.input"]
        assert len(human_events) == 1
        assert human_events[0]["content"]["content"] == "Real user follow-up"
        assert human_events[0]["metadata"]["caller"] == "lead_agent"

    @pytest.mark.anyio
    @pytest.mark.parametrize("tags", [["middleware:summarize"], ["subagent:research"]])
    async def test_non_lead_human_prompts_are_not_captured_as_user_input(self, journal_setup, tags):
        """Only lead-agent LLM starts create UI-facing human input events."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        j.on_chat_model_start(
            {},
            [[HumanMessage(content="Internal prompt")]],
            run_id=uuid4(),
            tags=tags,
        )
        await j.flush()

        assert j._first_human_msg is None
        assert j.get_completion_data()["message_count"] == 0
        events = await store.list_events("t1", "r1")
        assert not any(e["event_type"] == "llm.human.input" for e in events)

    @pytest.mark.anyio
    async def test_only_first_human_message_captured(self, journal_setup):
        """Subsequent on_chat_model_start calls do not overwrite the first message."""
        from langchain_core.messages import HumanMessage

        j, store = journal_setup
        j.on_chat_model_start({}, [[HumanMessage(content="First question")]], run_id=uuid4(), tags=["lead_agent"])
        j.on_chat_model_start({}, [[HumanMessage(content="Second question")]], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()

        assert j._first_human_msg == "First question"
        events = await store.list_events("t1", "r1")
        human_events = [e for e in events if e["event_type"] == "llm.human.input"]
        assert len(human_events) == 1

    @pytest.mark.anyio
    async def test_empty_messages_no_crash(self, journal_setup):
        """on_chat_model_start with empty messages does not crash."""
        j, store = journal_setup
        j.on_chat_model_start({}, [], run_id=uuid4(), tags=["lead_agent"])
        await j.flush()
        assert j._first_human_msg is None
