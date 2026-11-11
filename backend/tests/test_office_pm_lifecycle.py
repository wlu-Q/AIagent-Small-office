"""Stage-free PM lifecycle: native results, scoped ask waits and resume identity."""
import asyncio
from types import SimpleNamespace

import pytest

from deerflow.office.execution_facts import PendingAsk, WorkIdentity
from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun


@pytest.mark.anyio
async def test_pending_ask_uses_current_native_interrupt_not_ai_text():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    from deerflow.office.native_work import pending_ask

    saver = InMemorySaver()
    def question(state):
        return {'answer': interrupt({'version': 1, 'kind': 'human_input_request',
            'source': 'ask_clarification', 'request_id': 'question-one', 'question': '范围？'})}
    graph = StateGraph(dict)
    graph.add_node('question', question)
    graph.add_edge(START, 'question')
    graph.add_edge('question', END)
    agent = graph.compile(checkpointer=saver)
    config = {'configurable': {'thread_id': 'pm-group'}, 'metadata': {'deerflow_run_id': 'run-one'}}
    await agent.ainvoke({}, config)
    record = SimpleNamespace(run_id='run-one', user_id='default', thread_id='pm-group', assistant_id='pm',
        status='interrupted', metadata={}, kwargs={}, error=None, last_ai_message='随便编的内容')
    waiting = await pending_ask(saver, record)
    assert waiting.request_id == 'question-one'
    await agent.ainvoke(Command(resume='answer'), {**config, 'metadata': {'deerflow_run_id': 'run-two'}})
    assert await pending_ask(saver, record) is None


@pytest.mark.anyio
async def test_pm_final_answer_ends_once_without_stage_protocol():
    from test_office_run_projection import EmployeeServiceStub, GroupStoreStub, _pm_record

    from deerflow.office.run_projection import OfficeRunProjection
    from deerflow.runtime.runs.schemas import RunStatus

    employees, messages, gate = EmployeeServiceStub(), GroupStoreStub(), EmployeeRunGate()
    employees.summaries['pm'].update(displayName='PM', avatar='adam')
    lease = await gate.admit(queued('original'))
    record = _pm_record(RunStatus.running)
    record.metadata['office_lease_id'] = lease.lease_id
    projection = OfficeRunProjection(employee_service=employees, user_id='default', employee_run_gate=gate,
                                     group_store=messages)
    await projection.handle_run_started(record)
    record.status, record.last_ai_message = RunStatus.success, '这是前端架构分析。'
    await projection.handle_run_completed(record)
    await projection.handle_run_completed(record)
    assert employees.summaries['pm']['state'] == 'idle'
    assert [m['content'] for m in messages.messages] == ['这是前端架构分析。']


def queued(key, employee='pm'):
    return QueuedOfficeRun(key, employee, 'direct', key, 1, conversation_kind='group')


def ask(source='run-one', request='question-one'):
    return PendingAsk(WorkIdentity('default', 'project', 'pm-group', 'work'), 'pm', source, request)


@pytest.mark.anyio
async def test_ask_holds_existing_and_new_messages_but_allows_matching_resume():
    gate = EmployeeRunGate()
    running = await gate.admit(queued('original'))
    await gate.admit(queued('older'))
    waiting = ask()
    released = await gate.release(running.lease_id, pending_ask=waiting)
    assert released.next_item is None
    assert not (await gate.admit(queued('new'))).started
    assert (await gate.admit(queued('other', 'writer'))).started
    answer = await gate.admit(queued('answer'), resume_ask=waiting)
    assert answer.started
    await gate.consume_ask(waiting)
    released = await gate.release(answer.lease_id)
    assert released.next_item.queue_id == 'older'


@pytest.mark.anyio
async def test_answer_does_not_clear_manual_pause():
    gate = EmployeeRunGate()
    running = await gate.admit(queued('original'))
    waiting = ask()
    await gate.release(running.lease_id, pending_ask=waiting)
    await gate.pause_group()
    answer = await gate.admit(queued('answer'), resume_ask=waiting)
    assert not answer.started
    await gate.finish_group_pause()
    assert await gate.pending_ask('pm') == waiting
    await gate.resume_group()
    assert (await gate.get_admission('answer')).started
    assert await gate.pending_ask('pm') == waiting


@pytest.mark.anyio
async def test_stale_resume_and_completion_cannot_clear_new_wait():
    gate = EmployeeRunGate()
    first = await gate.admit(queued('original'))
    old = ask()
    await gate.release(first.lease_id, pending_ask=old)
    resumed = await gate.admit(queued('answer'), resume_ask=old)
    await gate.consume_ask(old)
    new = ask('run-two', 'question-two')
    await gate.release(resumed.lease_id, pending_ask=new)
    assert await gate.release(first.lease_id, pending_ask=old) is None
    with pytest.raises(ValueError):
        await gate.admit(queued('stale-answer'), resume_ask=old)
    assert not await gate.consume_ask(old)
    assert await gate.pending_ask('pm') == new


@pytest.mark.anyio
async def test_release_admit_race_keeps_unrelated_pm_work_queued():
    gate = EmployeeRunGate()
    running = await gate.admit(queued('original'))
    await asyncio.gather(gate.release(running.lease_id, pending_ask=ask()), gate.admit(queued('new')))
    assert not (await gate.get_admission('new')).started


@pytest.mark.anyio
async def test_current_view_ignores_other_project_and_old_terminal_runs():
    from unittest.mock import AsyncMock

    from deerflow.office.group_execution import GroupExecution
    from deerflow.runtime.runs.manager import RunManager
    from deerflow.runtime.runs.store.memory import MemoryRunStore
    store = MemoryRunStore()
    for index in range(125):
        await store.put(str(index), thread_id='pm-group', assistant_id='pm', user_id='default',
            status='running' if index in {0, 1} else 'success',
            metadata={'office_conversation_kind': 'group', 'office_project_id': 'other' if index == 1 else 'project'})
    manager = RunManager(store=store)
    pipeline = SimpleNamespace(user_id='default', employee_run_gate=EmployeeRunGate(),
        employee_service=SimpleNamespace(list_employees=AsyncMock(return_value=[{'id': 'pm'}]), thread_id=lambda *a: 'pm-group'),
        run_view=SimpleNamespace(run_manager=manager), run_launcher=SimpleNamespace(checkpointer=None),
        project_service=SimpleNamespace(get_binding=lambda: SimpleNamespace(project_id='project')))
    targets = (await GroupExecution(pipeline).state())['runTargets']
    assert targets == [{'employeeId': 'pm', 'runId': '0', 'projectId': 'project', 'workId': None}]
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.gateway.routers import office
    pipeline.group_execution = GroupExecution(pipeline)
    app = FastAPI()
    app.include_router(office.router)
    app.dependency_overrides[office.get_office_message_pipeline] = lambda: pipeline
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        response = await client.get('/api/office/group/execution')
        assert response.status_code == 200
        assert response.json()['runTargets'] == targets
        assert set(response.json()) == {'activeCount', 'paused', 'stopping', 'heldQueueIds', 'error', 'runTargets'}



@pytest.mark.anyio
async def test_native_control_metadata_is_owner_scoped_and_durable(monkeypatch):
    from unittest.mock import AsyncMock

    from deerflow.runtime.runs.manager import RunManager
    from deerflow.runtime.runs.store.memory import MemoryRunStore
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create('thread', user_id='default')
    await manager.update_metadata(record.run_id, {'office_control': 'paused'}, user_id='default')
    hydrated = await RunManager(store=store).get(record.run_id, user_id='default')
    assert hydrated.metadata['office_control'] == 'paused'
    with pytest.raises(FileNotFoundError):
        await manager.update_metadata(record.run_id, {'office_control': 'cancelled'}, user_id='other')
    monkeypatch.setattr(store, 'put', AsyncMock(side_effect=RuntimeError('storage unavailable')))
    with pytest.raises(RuntimeError):
        await manager.update_metadata(record.run_id, {'office_control': 'cancelled'}, user_id='default')
    assert record.metadata['office_control'] == 'paused'


@pytest.mark.anyio
@pytest.mark.parametrize('scenario', ['ask', 'manual-pause', 'cancel-ask', 'resume-checkpoint-failure'])
async def test_native_ask_resume_keeps_pm_busy_and_replaces_current_view(tmp_path, monkeypatch, scenario):
    manual_pause = scenario == "manual-pause"
    from unittest.mock import AsyncMock

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore
    from test_office_workflow_integration import runtime as fixture

    from app.gateway import services
    from app.gateway.routers.office import _start_office_run
    from deerflow.agents.lead_agent import agent
    from deerflow.config.agents_config import AgentConfig
    from deerflow.config.app_config import AppConfig
    from deerflow.office.messages import OfficeMessageCommand
    from deerflow.office.native_work import pending_ask
    from deerflow.office.run_view import OfficeRunView
    from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
    from deerflow.runtime.events.store.memory import MemoryRunEventStore
    from deerflow.runtime.runs.manager import RunManager
    from deerflow.runtime.runs.store.memory import MemoryRunStore
    from deerflow.runtime.runs.worker import RunContext
    from deerflow.tools.builtins.clarification_tool import ask_clarification_tool

    rt = fixture.__wrapped__(tmp_path, monkeypatch)
    cfg = AppConfig.model_validate({'models': [{'name': 'profile-primary', 'model': 'controlled', 'use': 'unused'}],
        'tools': [{'name': 'read_file', 'use': 'deerflow.sandbox.tools:read_file_tool', 'group': 'file:read'}],
        'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'}, 'memory': {'enabled': False},
        'title': {'enabled': False}, 'summarization': {'enabled': False}, 'tool_search': {'enabled': False}})
    store, saver, events = MemoryRunStore(), InMemorySaver(), MemoryRunEventStore()
    manager = RunManager(store=store)
    employees = rt.pipeline.employee_service
    summaries = {e['id']: dict(e) for e in await employees.list_employees()}
    employees.get_employee_summary = lambda eid: summaries[eid]
    employees.update_runtime_projection = lambda eid, patch: summaries[eid].update(patch)
    employees.list_employees = AsyncMock(side_effect=lambda: list(summaries.values()))
    rt.projection.checkpointer = rt.pipeline.run_launcher.checkpointer = saver
    rt.pipeline.run_view = OfficeRunView(employee_service=employees, run_manager=manager, event_store=events,
                                        checkpointer=saver, user_id='user-a')
    threads = MemoryThreadMetaStore(InMemoryStore())
    await threads.create(employees.thread_id('pm', 'group'), assistant_id='pm', user_id='user-a')
    ctx = RunContext(checkpointer=saver, event_store=events, thread_store=threads, app_config=cfg,
        on_run_started=rt.projection.handle_run_started, on_run_input_checkpointed=rt.projection.handle_run_input_checkpointed,
        on_run_completed=rt.projection.handle_run_completed)
    monkeypatch.setattr(services, 'get_run_manager', lambda _: manager)
    monkeypatch.setattr(services, 'get_checkpointer', lambda _: saver)
    monkeypatch.setattr(services, 'get_run_context', lambda _: ctx)
    monkeypatch.setattr(services, 'get_stream_bridge', lambda _: SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock()))
    monkeypatch.setattr(agent, 'is_office_managed_agent', lambda *a, **k: True)
    monkeypatch.setattr(agent, 'load_agent_config', lambda _: AgentConfig(name='pm', skills=[], runtime_contract={'authority': {'can_delegate': True}}))
    monkeypatch.setattr(agent, 'load_agent_soul', lambda _: 'PM')
    monkeypatch.setattr(agent, '_load_enabled_employee_skills', lambda *a, **k: [])
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **kw: [ask_clarification_tool])
    model_entered = asyncio.Event()
    pause_once = [manual_pause]
    class Model(FakeMessagesListChatModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            if pause_once[0]:
                pause_once[0] = False
                model_entered.set()
                await asyncio.Event().wait()
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        def bind_tools(self, tools, **kwargs):
            return self
    model = Model(responses=[AIMessage(content='', id='ask-message', tool_calls=[{'name': 'ask_clarification',
        'id': 'ask-one', 'args': {'question': '需要哪些部门？', 'clarification_type': 'missing_info'}}]),
        AIMessage(content='按研发部门完成分析。')])
    if manual_pause:
        model = Model(responses=[AIMessage(content='暂停后继续完成。')])
    monkeypatch.setattr(agent, 'create_chat_model', lambda **kw: model)
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id='user-a'), auth_source='internal'), headers={}, cookies={})
    try:
        result = await rt.pipeline.send('ask-work', OfficeMessageCommand('group', '分析部门筛选', None, ('pm',)),
                                       start_run=_start_office_run, request=request)
        original = await manager.get(result['dispatches'][0]['runId'], user_id='user-a')
        if manual_pause:
            await asyncio.wait_for(model_entered.wait(), 5)
            await rt.pipeline.group_execution.pause()
        await original.task
        assert original.status == 'interrupted', original.error
        if manual_pause:
            assert await pending_ask(saver, original) is None
            assert not (await rt.pipeline.group_execution.state())['stopping']
            await rt.pipeline.group_execution.resume(start_run=_start_office_run, request=request)
            await asyncio.gather(*list(rt.pipeline._queued_message_tasks))
            records = await manager.list_by_thread(original.thread_id, user_id='user-a')
            resumed = next(r for r in records if r.run_id != original.run_id)
            await resumed.task
            assert resumed.status == 'success', resumed.error
            assert resumed.metadata['office_work_id'] == original.metadata['office_work_id']
            assert resumed.metadata['office_resume_source_run_id'] == original.run_id
            assert rt.group.list()[-1].content == '暂停后继续完成。'
            assert not (await rt.pipeline.group_execution.state())['runTargets']
            return
        waiting = await pending_ask(saver, original)
        assert waiting is not None
        assert summaries['pm']['state'] == 'working'
        assert (await rt.pipeline.group_execution.state())['runTargets'][0]['runId'] == original.run_id

        # Hydrate through a new manager and rebuild projections from native facts.
        await manager.shutdown()
        manager = RunManager(store=store)
        rt.pipeline.run_view.run_manager = manager
        original = await manager.get(original.run_id, user_id='user-a')
        rt.pipeline.employee_run_gate = rt.projection.employee_run_gate = EmployeeRunGate()
        await rt.pipeline.restore_waits()
        assert await rt.pipeline.employee_run_gate.pending_ask('pm') == waiting
        unrelated = await rt.pipeline.employee_run_gate.admit(queued('unrelated'))
        assert not unrelated.started
        if scenario == 'cancel-ask':
            other = await manager.create(employees.thread_id('frontend-a', 'group'), assistant_id='frontend-a',
                user_id='user-a', metadata={'office_conversation_kind': 'group', 'office_employee_id': 'frontend-a',
                                          'office_project_id': rt.project.get_binding().project_id})
            command = OfficeMessageCommand('group', '取消本次工作流', None, ('pm',))
            await rt.pipeline.send('cancel', command, start_run=_start_office_run, request=request)
            await rt.pipeline.send('cancel', command, start_run=_start_office_run, request=request)
            assert other.status == 'pending'
            assert not other.abort_event.is_set()
            assert (await rt.pipeline.employee_run_gate.get_admission('unrelated')).started
            assert summaries['pm']['state'] == 'idle'
            assert all(t['employeeId'] != 'pm' for t in (await rt.pipeline.group_execution.state())['runTargets'])
            with pytest.raises(ValueError, match='no longer pending'):
                await rt.pipeline.resume('pm', original.run_id, source='ask_clarification',
                    request_id=waiting.request_id, text='过时回答', start_run=_start_office_run, request=request)
            rt.pipeline.employee_run_gate = EmployeeRunGate()
            await rt.pipeline.restore_waits()
            assert await rt.pipeline.employee_run_gate.pending_ask('pm') is None
            return
        with pytest.raises(ValueError, match='no longer pending'):
            await rt.pipeline.resume('pm', original.run_id, source='ask_clarification', request_id='wrong',
                text='wrong', start_run=_start_office_run, request=request)
        if scenario == 'resume-checkpoint-failure':
            # Fail the actual persistence boundary before the first Resume input
            # is saved. Keep the Worker, completion hook and retry path real.
            with monkeypatch.context() as failure:
                failure.setattr(saver, 'aput', AsyncMock(side_effect=OSError('injected checkpoint write failure')))
                response = await rt.pipeline.resume('pm', original.run_id, source='ask_clarification',
                    request_id=waiting.request_id, text='研发部门', start_run=_start_office_run, request=request)
                failed = await manager.get(response['runId'], user_id='user-a')
                await failed.task
            assert failed.status == 'error'
            assert 'injected checkpoint write failure' in failed.error
            assert await pending_ask(saver, original) == waiting
            assert await rt.pipeline.employee_run_gate.pending_ask('pm') == waiting
            assert summaries['pm']['state'] == 'working'
            assert not (await rt.pipeline.employee_run_gate.get_admission('unrelated')).started
            assert [t['runId'] for t in (await rt.pipeline.group_execution.state())['runTargets']] == [original.run_id]
            with pytest.raises(ValueError, match='different content'):
                await rt.pipeline.resume('pm', original.run_id, source='ask_clarification',
                    request_id=waiting.request_id, text='changed answer', start_run=_start_office_run, request=request)
        await rt.pipeline.group_execution.pause()
        response = await rt.pipeline.resume('pm', original.run_id, source='ask_clarification',
            request_id=waiting.request_id, text='研发部门', start_run=_start_office_run, request=request)
        assert not response['started']
        assert rt.pipeline.group_execution.paused
        await rt.pipeline.group_execution.resume(start_run=_start_office_run, request=request)
        await asyncio.gather(*list(rt.pipeline._queued_message_tasks))
        resumed = await rt.pipeline.run_view.find_resume_run('pm', 'group', original.run_id, waiting.request_id)
        assert resumed is not None
        await resumed.task
        assert resumed.status == 'success', resumed.error
        if scenario == 'resume-checkpoint-failure':
            assert resumed.run_id != failed.run_id
        assert rt.pipeline.run_view.serialize_run('pm', resumed)['workId'] == waiting.work.work_id
        assert rt.pipeline.run_view.serialize_run('pm', resumed)['resumeSourceRunId'] == original.run_id
        assert not (await rt.pipeline.group_execution.state())['runTargets']
        assert summaries['pm']['state'] == 'idle'
        assert rt.group.list()[-1].content == '按研发部门完成分析。'
        assert (await rt.pipeline.employee_run_gate.get_admission('unrelated')).started
    finally:
        await rt.pipeline.shutdown()
        await manager.shutdown()
