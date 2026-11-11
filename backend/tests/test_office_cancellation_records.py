import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from test_office_recovery_records import TASK, helpers, replay_setup as _replay_setup
from deerflow.agents.middlewares.skill_context import extract_skills
from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from deerflow.office.delegation import claim_delegation
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

recovery_setup = _replay_setup


@pytest.mark.parametrize('dispatch', ['inline', 'langchain_async'])
def test_live_progress_with_real_callback_dispatch(dispatch):
    async def exercise():
        updates = []
        threads = []
        owner_thread = threading.get_ident()
        store = MemoryRunEventStore()

        async def reporter(snapshot):
            updates.append(snapshot)

        class ObservedJournal(RunJournal):
            def on_llm_end(self, *args, **kwargs):
                threads.append(threading.get_ident())
                return super().on_llm_end(*args, **kwargs)

        journal = ObservedJournal('progress-run', 'progress-thread', store, user_id='probe',
                                  flush_threshold=1, progress_reporter=reporter, progress_flush_interval=0)
        message = AIMessage(id='pm-response', content='Start the employee task',
                            usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120})
        response = LLMResult(generations=[[ChatGeneration(message=message)]])
        if dispatch == 'inline':
            journal.on_llm_end(response, run_id=uuid4(), tags=['lead_agent'])
        else:
            manager = AsyncCallbackManagerForLLMRun(run_id=uuid4(), handlers=[journal],
                                                   inheritable_handlers=[], tags=['lead_agent'])
            await manager.on_llm_end(response)
        await asyncio.sleep(.03)
        await journal.flush()
        events = await store.list_events('progress-thread', 'progress-run', user_id='probe')
        snapshot = dict(dispatch=dispatch, owner_thread=owner_thread, callback_threads=threads,
                        updates=updates, completion=journal.get_completion_data(),
                        persisted_event_types=[event['event_type'] for event in events])
        assert snapshot['completion']['total_tokens'] == 120
        assert 'llm.ai.response' in snapshot['persisted_event_types']
        if dispatch == 'langchain_async':
            assert threads[0] != owner_thread
        assert updates and updates[-1]['total_tokens'] == 120

    asyncio.run(exercise())


@pytest.mark.parametrize('cancellation', ['child_terminal', 'parent_cancel', 'completed_during_cancel'])
def test_cancelled_receipt_preserves_available_child_facts(recovery_setup, monkeypatch, cancellation):
    runtime, _, store, _, gate, _ = recovery_setup
    gate.cancel_parent = AsyncMock()
    args = dict(description='Edit one file', prompt='Edit one file', subagent_type='frontend')
    call_id = 'call-cancellation-' + cancellation
    runtime.state['messages'] = [HumanMessage(content='Edit one file'),
                                 AIMessage(content='', id='ai-cancellation', tool_calls=[dict(name='task', id=call_id, args=args)])]
    steps = [AIMessage(content='Editing README', tool_calls=[dict(name='apply_patch', id='edit-call',
                        args={'patch': '*** Begin Patch\n*** Add File: README.md\n+Ready\n*** End Patch'})]).model_dump(),
             ToolMessage(content='Success. Added README.md', tool_call_id='edit-call', name='apply_patch',
                         status='success').model_dump()]
    child = helpers._make_result(helpers.FakeSubagentStatus.CANCELLED if cancellation == 'child_terminal'
                                 else helpers.FakeSubagentStatus.RUNNING, result='README created; tests not yet run',
                                 ai_messages=steps)
    monkeypatch.setattr(TASK, 'SubagentStatus', helpers.FakeSubagentStatus)
    monkeypatch.setattr(TASK, 'SubagentExecutor', lambda **kwargs: SimpleNamespace(execute_async=lambda prompt, task_id: task_id))
    monkeypatch.setattr(TASK, 'get_background_task_result', lambda task_id: child)
    monkeypatch.setattr(TASK, 'update_employee_runtime_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(TASK, 'cleanup_background_task', lambda task_id: None)
    monkeypatch.setattr('deerflow.office.terminal.manager_for', lambda store: SimpleNamespace(finish_run=lambda *args: None))

    def cancel_child(task_id):
        child.status = (helpers.FakeSubagentStatus.COMPLETED if cancellation == 'completed_during_cancel'
                        else helpers.FakeSubagentStatus.CANCELLED)

    monkeypatch.setattr(TASK, 'request_cancel_background_task', cancel_child)

    async def exercise():
        running = asyncio.Event()
        monkeypatch.setattr(TASK, 'get_stream_writer', lambda: lambda event: running.set() if event['type'] == 'task_running' else None)
        task = asyncio.create_task(TASK.task_tool.coroutine(runtime=runtime, tool_call_id=call_id, **args))
        if cancellation != 'child_terminal':
            await asyncio.wait_for(running.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task

    asyncio.run(exercise())
    _, identity, receipt, first, _ = claim_delegation(runtime, user_id='default', employee_id='frontend',
                                                    tool_call_id=call_id, **args)
    assert not first
    assert receipt.status == ('succeeded' if cancellation == 'completed_during_cancel' else 'interrupted')
    assert receipt.result_ref['report'] == child.result
    assert receipt.evidence == steps


def test_usage_arriving_after_initial_cancel_report(monkeypatch):
    monkeypatch.setattr(TASK, 'SubagentStatus', helpers.FakeSubagentStatus)
    async def exercise():
        journal = RunJournal('late-run', 'late-thread', MemoryRunEventStore(), user_id='probe')
        runtime = helpers._make_runtime()
        runtime.config['callbacks'] = [journal]
        first = dict(source_run_id='call-first', caller='subagent:frontend', model_name='test',
                     input_tokens=100, output_tokens=20, total_tokens=120)
        second = dict(source_run_id='call-late', caller='subagent:frontend', model_name='test',
                      input_tokens=200, output_tokens=40, total_tokens=240)
        child = helpers._make_result(helpers.FakeSubagentStatus.RUNNING, token_usage_records=[first])
        TASK._report_subagent_usage(runtime, child)
        before = journal.get_completion_data()
        child.token_usage_records.append(second)
        child.status = helpers.FakeSubagentStatus.CANCELLED
        monkeypatch.setattr(TASK, 'get_background_task_result', lambda task_id: child)
        removed = []
        monkeypatch.setattr(TASK, 'cleanup_background_task', removed.append)
        await asyncio.wait_for(TASK._deferred_cleanup_subagent_task('late-task', 'trace'), timeout=2)
        TASK._report_subagent_usage(runtime, child)
        after = journal.get_completion_data()
        assert before['total_tokens'] == 120 and removed == ['late-task']
        assert after['total_tokens'] == 360

    asyncio.run(exercise())


@pytest.mark.parametrize('mode', ['normal', 'native_cancel'])
def test_executor_cancellation_after_llm_callback_before_stream_chunk(monkeypatch, mode):
    from deerflow.subagents import executor as executor_module

    async def exercise():
        callback_seen = asyncio.Event()
        finish_stream = asyncio.Event()
        collectors = []
        message = AIMessage(content='Completed response', id='last-response',
                            usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120})

        async def stream(state, *, config, **kwargs):
            yield {'messages': [HumanMessage(content='Controlled task')]}
            collector = config['callbacks'][0]
            collectors.append(collector)
            manager = AsyncCallbackManagerForLLMRun(run_id=uuid4(), handlers=[collector], inheritable_handlers=[])
            await manager.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
            callback_seen.set()
            await finish_stream.wait()
            yield {'messages': [HumanMessage(content='Controlled task'), message]}

        executor = executor_module.SubagentExecutor(config=helpers._make_subagent_config(), tools=[],
                                                    thread_id='isolated-thread', user_id='probe')
        monkeypatch.setattr(executor_module, 'build_tracing_callbacks', lambda: [])
        monkeypatch.setattr(executor_module, 'inject_langfuse_metadata', lambda *args, **kwargs: None)
        monkeypatch.setattr(executor, '_build_initial_state', AsyncMock(return_value=({'messages': []}, [], None)))
        monkeypatch.setattr(executor, '_create_agent', lambda *args, **kwargs: SimpleNamespace(astream=stream))
        holder = executor_module.SubagentResult(task_id='native-probe', trace_id='probe', status=executor_module.SubagentStatus.RUNNING)
        child = asyncio.create_task(executor._aexecute('Controlled task', holder))
        await asyncio.wait_for(callback_seen.wait(), timeout=2)
        if mode == 'native_cancel':
            child.cancel()
            with pytest.raises(asyncio.CancelledError):
                await child
        else:
            finish_stream.set()
            await asyncio.wait_for(child, timeout=2)
        actual_records = collectors[0].snapshot_records()
        assert sum(item['total_tokens'] for item in actual_records) == 120
        assert holder.token_usage_records == actual_records

    asyncio.run(exercise())


@pytest.mark.parametrize('start_line,expected_loaded', [(None, True), (999, False)])
def test_skill_read_range_tracks_real_content(monkeypatch, tmp_path, start_line, expected_loaded):
    from deerflow.sandbox import tools as sandbox_tools

    source = tmp_path / 'SKILL.md'
    source.write_text('---\nname: example\ndescription: Build an example UI\n---\nUse semantic HTML.\n', encoding='utf-8')
    path = '/mnt/skills/public/example/SKILL.md'
    monkeypatch.setattr(sandbox_tools, '_is_disabled_skill_path', lambda *args, **kwargs: False)
    monkeypatch.setattr(sandbox_tools, 'read_current_file_content', lambda *args: source.read_text('utf-8'))
    monkeypatch.setattr(sandbox_tools, 'is_local_sandbox', lambda runtime: True)
    runtime = helpers._make_approved_office_runtime(metadata={})
    runtime.tool_call_id = 'skill-read'
    args = dict(description='Read selected Skill', path=path, start_line=start_line)
    call = dict(name='read_file', id='skill-read', args=args)
    middleware = ToolErrorHandlingMiddleware()
    request = SimpleNamespace(tool_call=call)

    def handler(request):
        result = sandbox_tools.read_file_tool.func(runtime=runtime, **args)
        return result if isinstance(result, ToolMessage) else ToolMessage(content=result, name='read_file', tool_call_id='skill-read')

    result = middleware.wrap_tool_call(request, handler)
    loaded = extract_skills([AIMessage(content='', tool_calls=[call]), result], skills_root='/mnt/skills', read_tool_names=['read_file'])
    assert bool(loaded) == expected_loaded
