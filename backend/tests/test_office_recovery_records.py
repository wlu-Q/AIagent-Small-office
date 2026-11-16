import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.agents.middleware.types import ModelRequest

import test_task_tool_core_logic as helpers
from deerflow.agents.middlewares.durable_context_middleware import DurableContextMiddleware
from deerflow.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
from deerflow.agents.middlewares.tool_result_meta import normalize_tool_message
from deerflow.agents.thread_state import merge_delegations
from deerflow.config import paths
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.office.delegation import claim_delegation
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.subagents.step_events import build_subagent_step


TASK = helpers.task_tool_module


@pytest.fixture
def replay_setup(tmp_path, monkeypatch):
    monkeypatch.setenv('DEER_FLOW_HOME', str(tmp_path))
    monkeypatch.setattr(paths, '_paths', paths.Paths(tmp_path))
    app = AppConfig(models=[ModelConfig(name='employee-model', use='langchain_openai:ChatOpenAI', model='test')],
                    sandbox=SandboxConfig(use='test'))
    app.memory = app.memory.model_copy(update={'enabled': False})
    monkeypatch.setattr(TASK, 'get_app_config', lambda: app)
    monkeypatch.setattr(TASK, 'get_available_subagent_names', lambda **kwargs: [])
    monkeypatch.setattr(TASK, 'resolve_employee_subagent_package', lambda *args: helpers._office_employee_package('frontend'))
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **kwargs: [])
    monkeypatch.setattr(TASK, 'get_stream_writer', lambda: lambda payload: None)
    gate = SimpleNamespace(admit=AsyncMock(return_value=SimpleNamespace(started=True, lease_id='lease')),
                           release=AsyncMock(return_value=True))
    monkeypatch.setattr(TASK, '_office_employee_gate', lambda: gate)
    executor = MagicMock(side_effect=AssertionError('Receipt replay must not start a model'))
    monkeypatch.setattr(TASK, 'SubagentExecutor', executor)
    runtime = helpers._make_approved_office_runtime(metadata={})
    runtime.context.update(run_id='original-run', app_config=app)
    args = dict(description='Review existing work', prompt='Review existing work', subagent_type='frontend')
    runtime.state['messages'] = [HumanMessage(content='Build a small site', additional_kwargs={'run_id': 'original-run'}),
                                 AIMessage(content='', id='ai-original', tool_calls=[dict(name='task', id='call-original', args=args)])]
    store, identity, _, first, _ = claim_delegation(runtime, user_id='default', employee_id='frontend',
                                                   tool_call_id='call-original', **args)
    assert first
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='running',
                  result_ref={'report': '', 'workspace_id': 'existing-review-tree'}, evidence=[])
    return runtime, args, store, identity, gate, executor


def replay(setup):
    runtime, args, store, identity, gate, executor = setup
    result = asyncio.run(TASK.task_tool.coroutine(runtime=runtime, tool_call_id='call-original', **args))
    executor.assert_not_called()
    return result.update['messages'][0]


@pytest.mark.parametrize('receipt_status,expected', [
    ('succeeded', 'completed'), ('interrupted', 'cancelled'), ('failed', 'failed'),
    ('timeout', 'timed_out'), ('unknown', 'unknown'),
])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_missing_task_reply_uses_real_receipt_only_in_model_view(replay_setup, receipt_status, expected, asynchronous):
    runtime, _, store, identity, gate, executor = replay_setup
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status=receipt_status,
                  result_ref={'report': 'Files written; preview not confirmed', 'workspace_id': 'existing-review-tree'}, evidence=[])
    runtime.state['delegations'] = DurableContextMiddleware().before_model(runtime.state, runtime)['delegations']
    original_messages = list(runtime.state['messages'])
    runtime.context['run_id'] = 'followup-run'
    runtime.context['office_work']['work_id'] = 'followup-work'
    request = ModelRequest(model=object(), tools=[], messages=original_messages, state=runtime.state, runtime=runtime)
    middleware = DanglingToolCallMiddleware()
    if asynchronous:
        async def capture(value):
            return value
        patched = asyncio.run(middleware.awrap_model_call(request, capture))
    else:
        patched = middleware.wrap_model_call(request, lambda value: value)
    result = next(message for message in patched.messages if isinstance(message, ToolMessage))
    assert result.tool_call_id == 'call-original'
    assert result.additional_kwargs['subagent_status'] == expected
    assert 'Files written; preview not confirmed' in result.content
    assert 'existing-review-tree' in result.content
    assert 'result was not recorded' not in result.content
    assert runtime.state['messages'] == original_messages
    assert request.messages == original_messages
    executor.assert_not_called()
    gate.admit.assert_not_called()


@pytest.mark.parametrize('different', ['user_id', 'project_id', 'thread_id', 'ai_message_id', 'run_id', 'running', 'non_office'])
def test_missing_task_reply_never_borrows_unrelated_or_running_receipt(replay_setup, different):
    runtime, _, store, identity, _, _ = replay_setup
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='running' if different == 'running' else 'succeeded',
                  result_ref={'report': 'Private result'}, evidence=[])
    runtime.state['delegations'] = DurableContextMiddleware().before_model(runtime.state, runtime)['delegations']
    if different in {'user_id', 'project_id', 'thread_id'}:
        runtime.context['office_work'][different] = 'another-owner'
    elif different == 'ai_message_id':
        runtime.state['messages'][-1] = runtime.state['messages'][-1].model_copy(update={'id': 'another-ai'})
    elif different == 'run_id':
        runtime.state['delegations'][0]['run_id'] = 'another-run'
    elif different == 'non_office':
        runtime.context.pop('office_work')
    request = ModelRequest(model=object(), tools=[], messages=runtime.state['messages'], state=runtime.state, runtime=runtime)
    patched = DanglingToolCallMiddleware().wrap_model_call(request, lambda value: value)
    result = next(message for message in patched.messages if isinstance(message, ToolMessage))
    assert 'Private result' not in result.content
    assert 'completion is unknown' in result.content


def test_real_tool_reply_is_not_replaced_by_receipt(replay_setup):
    runtime, _, store, identity, _, _ = replay_setup
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='succeeded',
                  result_ref={'report': 'Stored report'}, evidence=[])
    runtime.state['delegations'] = DurableContextMiddleware().before_model(runtime.state, runtime)['delegations']
    actual = ToolMessage(content='Original tool reply', tool_call_id='call-original', name='task')
    runtime.state['messages'].append(actual)
    request = ModelRequest(model=object(), tools=[], messages=runtime.state['messages'], state=runtime.state, runtime=runtime)
    patched = DanglingToolCallMiddleware().wrap_model_call(request, lambda value: value)
    assert patched is request
    assert patched.messages[-1] is actual


def test_cancelled_receipt_keeps_successful_file_tool_facts_without_command_executions(replay_setup):
    runtime, _, store, identity, _, _ = replay_setup
    steps = []
    for index in range(8):
        steps.extend([
            AIMessage(content='', tool_calls=[dict(name='read_file', id=f'read-{index}', args={'path': f'file-{index}'})]).model_dump(),
            ToolMessage(content='FILE_BODY_NOT_FOR_REPLAY', name='read_file', tool_call_id=f'read-{index}', status='success').model_dump(),
        ])
    steps.extend([
        AIMessage(content='', tool_calls=[dict(name='apply_patch', id='edit-file', args={'patch': 'PATCH_BODY_NOT_FOR_REPLAY'})]).model_dump(),
        ToolMessage(content=json.dumps({'status': 'success', 'applied': [{'action': 'created', 'path': 'SortToggle.tsx'}]}),
                    name='apply_patch', tool_call_id='edit-file', status='success').model_dump(),
    ])
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='interrupted',
                  result_ref={'report': 'Starting the edit', 'execution_evidence': {'total_executions': 0}}, evidence=steps)
    runtime.state['delegations'] = DurableContextMiddleware().before_model(runtime.state, runtime)['delegations']
    request = ModelRequest(model=object(), tools=[], messages=runtime.state['messages'], state=runtime.state, runtime=runtime)
    recovered = DanglingToolCallMiddleware().wrap_model_call(request, lambda value: value).messages[-1]
    for message in (recovered, replay(replay_setup)):
        facts = message.additional_kwargs['office_execution_workspace']
        assert len(facts['tool_observations']) == 6
        assert facts['omitted_tool_observations'] == 3
        assert facts['tool_observations'][-1] == dict(tool='apply_patch', status='success', paths={}, tool_call_id='edit-file',
                                                    applied=[{'action': 'created', 'path': 'SortToggle.tsx'}], omitted_applied=0)
        assert 'BODY_NOT_FOR_REPLAY' not in message.content
        assert message.additional_kwargs['subagent_status'] == 'cancelled'


def test_recovered_report_is_bounded_and_uses_persisted_workspace_facts(replay_setup, monkeypatch):
    runtime, _, store, identity, _, _ = replay_setup
    report = 'Earlier progress ' + 'x' * 20000 + ' Latest partial result'
    evidence = {'commands': [{'execution_id': 'command-one', 'status': 'failed', 'exit_code': 2}]}
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='interrupted',
                  result_ref={'report': report, 'workspace_id': 'existing-review-tree', 'result_commit': 'saved-head',
                              'root_path': '/missing-old-directory', 'execution_evidence': evidence}, evidence=[])
    runtime.state['delegations'] = DurableContextMiddleware().before_model(runtime.state, runtime)['delegations']
    monkeypatch.setattr('deerflow.office.execution_workspace._git', MagicMock(side_effect=AssertionError('No new Git lookup')))
    request = ModelRequest(model=object(), tools=[], messages=runtime.state['messages'], state=runtime.state, runtime=runtime)
    patched = DanglingToolCallMiddleware().wrap_model_call(request, lambda value: value)
    result = patched.messages[-1]
    assert len(result.additional_kwargs['subagent_result_brief']) <= 2000
    assert 'Earlier progress' in result.content and 'Latest partial result' in result.content
    assert len(result.content) < 3000
    assert result.additional_kwargs['office_execution_workspace']['execution_evidence'] == evidence
    assert 'result_commit' not in result.additional_kwargs['office_execution_workspace']
    assert 'saved-head' not in result.content
    assert store.get(identity).result_ref['result_commit'] == 'saved-head'
    assert store.get(identity).result_ref['report'] == report


def test_unknown_receipt_retains_unknown_task_status(replay_setup):
    replay_setup[2].mark_unresolved_unknown()
    message = replay(replay_setup)
    assert message.additional_kwargs['office_execution_workspace']['execution_status'] == 'unknown'
    assert message.additional_kwargs['subagent_status'] == 'unknown'


def test_replayed_receipt_is_reconciled_by_durable_context(replay_setup):
    runtime, _, store, identity, _, _ = replay_setup
    store.mark_unresolved_unknown()
    message = replay(replay_setup)
    runtime.state['messages'].append(message)
    middleware = DurableContextMiddleware()
    first = middleware.before_model(runtime.state, runtime)
    runtime.state['delegations'] = first['delegations']
    second = middleware.before_model(runtime.state, runtime) or {}
    final = merge_delegations(runtime.state['delegations'], second.get('delegations'))
    assert final[0]['status'] == 'unknown'


def test_pending_ledger_without_replayed_tool_message_is_corrected(replay_setup):
    runtime = replay_setup[0]
    replay_setup[2].mark_unresolved_unknown()
    result = DurableContextMiddleware().before_model(runtime.state, runtime)
    assert merge_delegations([], result['delegations'])[0]['status'] == 'unknown'


def test_completed_receipt_can_be_read_when_employee_is_busy(replay_setup):
    runtime, _, store, identity, gate, executor = replay_setup
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='succeeded',
                  result_ref={'report': 'Existing completed report'}, evidence=[])
    gate.admit.return_value = SimpleNamespace(started=False, lease_id=None)
    message = replay(replay_setup)
    assert message.additional_kwargs['subagent_status'] == 'completed'


def test_completed_receipt_replays_when_employee_is_idle(replay_setup):
    _, _, store, identity, _, _ = replay_setup
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='succeeded',
                  result_ref={'report': 'Existing completed report'}, evidence=[])
    message = replay(replay_setup)
    assert message.additional_kwargs['subagent_status'] == 'completed'
    assert 'Existing completed report' in message.content


def test_receipt_replay_does_not_load_changed_employee_config(replay_setup, monkeypatch):
    _, _, store, identity, gate, _ = replay_setup
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='succeeded',
                  result_ref={'report': 'Saved result'}, evidence=[])
    monkeypatch.setattr(TASK, 'resolve_employee_subagent_package', MagicMock(side_effect=AssertionError('Not a new execution')))
    monkeypatch.setattr(TASK, 'get_app_config', MagicMock(side_effect=AssertionError('No model needed for replay')))
    assert replay(replay_setup).additional_kwargs['subagent_status'] == 'completed'
    gate.admit.assert_not_called()


def test_running_receipt_replay_does_not_start_another_execution(replay_setup):
    assert replay(replay_setup).additional_kwargs['subagent_status'] == 'in_progress'
    replay_setup[4].admit.assert_not_called()


def test_replay_rejects_changed_arguments(replay_setup):
    replay_setup[1]['prompt'] = 'Different request'
    message = replay(replay_setup)
    assert message.additional_kwargs['subagent_status'] == 'failed'
    assert 'do not match' in message.content
    replay_setup[4].admit.assert_not_called()


def test_receipt_corrects_stale_terminal_summary(replay_setup):
    runtime, _, store, identity, _, _ = replay_setup
    store.mark_unresolved_unknown()
    runtime.state['delegations'] = [{'id': 'call-original', 'run_id': 'original-run', 'status': 'failed',
        'description': 'Review existing work', 'subagent_type': 'frontend', 'result_brief': 'Nothing was created'}]
    result = DurableContextMiddleware().before_model(runtime.state, runtime)
    entry = result['delegations'][-1]
    assert entry['status'] == 'unknown'
    assert entry['result_brief'] == ''
    assert entry['workspace_id'] == 'existing-review-tree'


def test_stale_tool_message_cannot_undo_receipt_on_next_round(replay_setup):
    runtime, _, store, identity, _, _ = replay_setup
    store.observe(identity, parent_run_id='original-run', native_task_id='call-original', status='succeeded',
                  result_ref={'report': 'Actual completed work'}, evidence=[])
    runtime.state['messages'].append(ToolMessage(content='No work done', name='task', tool_call_id='call-original',
        additional_kwargs={'subagent_status': 'failed', 'subagent_result_brief': 'No work done'}))
    middleware = DurableContextMiddleware()
    first = middleware.before_model(runtime.state, runtime)
    runtime.state['delegations'] = first['delegations']
    assert runtime.state['delegations'][-1]['result_brief'] == 'Actual completed work'
    for iteration in range(3):
        assert not (middleware.before_model(runtime.state, runtime) or {}).get('delegations')


def test_external_usage_recovery_is_per_call_not_per_model_maximum():
    from deerflow.runtime.runs.worker import _event_completion_seed, _merge_recovery_completion_seed

    events = [dict(event_type='llm.ai.response', seq=1,
                   content={'id': 'pm-message', 'content': 'Planning', 'response_metadata': {'model_name': 'shared'}},
                   metadata={'llm_call_index': 1, 'usage': {'input_tokens': 150, 'output_tokens': 0, 'total_tokens': 150}})]
    external = dict(event_type='llm.external.usage', seq=2, metadata=dict(source_run_id='child-call',
                    caller='subagent:frontend', model_name='shared', input_tokens=1000, output_tokens=0, total_tokens=1000))
    events.extend([external, {**external, 'seq': 3}])
    restored = _merge_recovery_completion_seed({'total_tokens': 1100}, _event_completion_seed(events))
    assert restored['total_tokens'] == 1150
    assert restored['lead_agent_tokens'] == 150
    assert restored['subagent_tokens'] == 1000
    assert restored['token_usage_by_model']['shared']['total_tokens'] == 1150
    journal = RunJournal('run', 'thread', MemoryRunEventStore(), user_id='probe', initial_completion_data=restored)
    journal.record_external_llm_usage_records([external['metadata']])
    assert journal.get_completion_data()['total_tokens'] == 1150


def test_subagent_usage_is_counted_once_across_event_types():
    from deerflow.runtime.runs.worker import _event_completion_seed

    events = [
        dict(event_type='llm.ai.response', seq=1,
             content={'id': 'pm-message', 'content': 'Planning', 'response_metadata': {'model_name': 'shared'}},
             metadata={'llm_call_index': 1, 'caller': 'lead_agent',
                       'usage': {'input_tokens': 150, 'output_tokens': 0, 'total_tokens': 150}}),
        dict(event_type='llm.ai.response', seq=2,
             content={'id': 'child-message', 'content': 'Working', 'response_metadata': {'model_name': 'shared'}},
             metadata={'llm_call_index': 2, 'caller': 'subagent:frontend',
                       'usage': {'input_tokens': 1000, 'output_tokens': 0, 'total_tokens': 1000}}),
        dict(event_type='llm.external.usage', seq=3,
             metadata=dict(source_run_id='child-call', caller='subagent:frontend', model_name='shared',
                           input_tokens=1000, output_tokens=0, total_tokens=1000)),
    ]
    seed = _event_completion_seed(events)
    assert seed['lead_agent_tokens'] == 150
    assert seed['subagent_tokens'] == 1000
    assert seed['total_tokens'] == 1150
    assert seed['llm_call_count'] == 2
    assert seed['token_usage_by_model']['shared']['total_tokens'] == 1150


def test_journal_does_not_count_subagent_response_alongside_external_usage():
    from uuid import uuid4
    from langchain_core.outputs import ChatGeneration, LLMResult

    journal = RunJournal('run-dup', 'thread-dup', MemoryRunEventStore(), user_id='probe')
    message = AIMessage(content='child reply',
                        usage_metadata=dict(input_tokens=1000, output_tokens=0, total_tokens=1000))
    journal.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]),
                       run_id=uuid4(), tags=['subagent:frontend'])
    journal.record_external_llm_usage_records([dict(source_run_id='child-call', caller='subagent:frontend',
        model_name='shared', input_tokens=1000, output_tokens=0, total_tokens=1000, cache_read_tokens=0)])
    data = journal.get_completion_data()
    assert data['subagent_tokens'] == 1000
    assert data['total_tokens'] == 1000
    assert data['llm_call_count'] == 1


@pytest.mark.anyio
async def test_concurrent_usage_and_terminal_supplement_do_not_reopen_run():
    from deerflow.runtime.runs.manager import RunManager, RunStatus
    from deerflow.runtime.runs.store.memory import MemoryRunStore

    store = MemoryRunEventStore()
    manager = RunManager(store=MemoryRunStore())
    run = await manager.create('thread-usage')
    await manager.set_status(run.run_id, RunStatus.running)
    journal = RunJournal(run.run_id, run.thread_id, store, user_id=run.user_id, progress_flush_interval=0,
                         progress_reporter=lambda snapshot: manager.update_run_progress(run.run_id, **snapshot))
    usage = dict(source_run_id='child-call', caller='subagent:frontend', model_name='shared',
                 input_tokens=100, output_tokens=20, total_tokens=120)
    await asyncio.gather(*(asyncio.to_thread(journal.record_external_llm_usage_records, [usage]) for iteration in range(8)))
    await journal.flush()
    assert journal.get_completion_data()['total_tokens'] == 120
    boundary = await store.get_last_event_seq(run.thread_id, run.run_id, user_id=run.user_id)
    await manager.finalize_run(run.run_id, status=RunStatus.interrupted, last_event_seq=boundary,
                               **{**journal.get_completion_data(), 'last_ai_message': 'Final reply'})
    finished_at = run.updated_at
    journal.record_external_llm_usage_records([{**usage, 'source_run_id': 'late-call'}])
    await asyncio.sleep(.02)
    await journal.flush()
    assert run.total_tokens == 240
    assert run.status == RunStatus.interrupted
    assert run.last_ai_message == 'Final reply'
    assert run.updated_at == finished_at
    assert run.events_finalized and run.last_event_seq == boundary
    events = await store.list_events(run.thread_id, run.run_id, user_id=run.user_id)
    assert [event['event_type'] for event in events] == ['llm.external.usage', 'llm.external.usage']


@pytest.mark.anyio
async def test_office_tool_only_model_call_persists_usage_before_progress():
    from uuid import uuid4
    from langchain_core.outputs import ChatGeneration, LLMResult

    store = MemoryRunEventStore()
    written = asyncio.Event()
    checks = []

    async def report(snapshot):
        events = await store.list_events('thread', 'run', user_id='probe')
        checks.append((snapshot['total_tokens'], len(events)))
        written.set()

    journal = RunJournal('run', 'thread', store, user_id='probe', flush_public_messages=True,
                         progress_reporter=report, progress_flush_interval=0)
    message = AIMessage(content='', tool_calls=[dict(name='ls', id='call', args={})],
                        usage_metadata=dict(input_tokens=90, output_tokens=10, total_tokens=100))
    journal.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=uuid4())
    await asyncio.wait_for(written.wait(), timeout=2)
    assert checks[-1] == (100, 1)
    await journal.flush()


def test_tool_status_preserves_content_and_process_semantics():
    from dataclasses import asdict
    from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY, ToolResultMeta

    for content in ['Error: example log text', '{"status":"failed","exit_code":2}']:
        message = ToolMessage(content=content, name='read_file', tool_call_id='call', status='success',
            additional_kwargs={TOOL_META_KEY: asdict(ToolResultMeta(status='success', error_type=None,
                recoverable_by_model=True, recommended_next_action='continue', source='tool_return'))})
        assert normalize_tool_message(message).status == 'success'
    message = normalize_tool_message(ToolMessage(content='Error: File not found', name='read_file', tool_call_id='call'))
    assert message.status == 'error'
    assert build_subagent_step(message.model_dump(), task_id='task', message_index=0)['status'] == 'error'


@pytest.mark.anyio
async def test_concurrent_child_usage_is_durable_before_progress():
    entered = asyncio.Event()
    release = asyncio.Event()
    progress_written = asyncio.Event()

    class SlowStore(MemoryRunEventStore):
        async def put_batch(self, events, *, user_id):
            entered.set()
            await release.wait()
            return await super().put_batch(events, user_id=user_id)

    store = SlowStore()
    checks = []

    async def report(snapshot):
        events = await store.list_events('thread', 'run', user_id='probe')
        checks.append((snapshot['total_tokens'], sum(event['metadata']['total_tokens'] for event in events)))
        progress_written.set()

    journal = RunJournal('run', 'thread', store, user_id='probe', progress_reporter=report, progress_flush_interval=0)
    first = dict(source_run_id='first', caller='subagent:frontend', total_tokens=100, input_tokens=90, output_tokens=10)
    journal.record_external_llm_usage_records([first])
    await entered.wait()
    journal.record_external_llm_usage_records([{**first, 'source_run_id': 'second'}])
    release.set()
    await asyncio.wait_for(progress_written.wait(), timeout=2)
    assert checks[-1] == (200, 200)
    await journal.flush()
