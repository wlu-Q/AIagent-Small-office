import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun
from deerflow.runtime.runs.schemas import RunStatus


def item(key, kind='group', employee='a'):
    return QueuedOfficeRun(key, employee, 'direct', key, 1, conversation_kind=kind)


@pytest.mark.asyncio
async def test_pause_holds_group_queue_but_private_and_new_messages_can_run():
    gate = EmployeeRunGate()
    active = await gate.admit(item('active'))
    await gate.admit(item('held'))
    await gate.admit(item('private', 'private'))
    await gate.pause_group()
    released = await gate.release(active.lease_id)
    assert released.next_item.queue_id == 'private'
    assert await gate.is_held('held')
    await gate.finish_group_pause()
    await gate.release(released.next_admission.lease_id)
    fresh = await gate.admit(item('new-chat'))
    assert fresh.started
    await gate.release(fresh.lease_id)
    waiter = asyncio.create_task(gate.wait_until_started('held'))
    await gate.resume_group()
    assert (await asyncio.wait_for(waiter, 1)).started


@pytest.mark.asyncio
async def test_admission_during_stop_is_held_even_for_idle_employee():
    gate = EmployeeRunGate()
    await gate.pause_group()
    assert not (await gate.admit(item('late'))).started
    assert (await gate.admit(item('private', 'private'))).started


@pytest.mark.asyncio
async def test_group_pause_uses_backend_runs_not_client_run_list():
    from deerflow.office.group_execution import GroupExecution

    gate = EmployeeRunGate()
    group = SimpleNamespace(run_id='group', assistant_id='a', status=RunStatus.running,
                            task=None, finalizing=False, user_id='owner', thread_id='a:group', metadata={'office_conversation_kind': 'group'})
    private = SimpleNamespace(run_id='private', assistant_id='a', status=RunStatus.running)
    manager = SimpleNamespace(list_by_thread=AsyncMock(side_effect=lambda thread, **kw: [group] if thread == 'a:group' else [private]),
                              cancel=AsyncMock(), update_metadata=AsyncMock())
    employees = SimpleNamespace(list_employees=AsyncMock(return_value=[{'id': 'a'}]), thread_id=lambda e, c: f'{e}:{c}')
    pipeline = SimpleNamespace(user_id='owner', employee_service=employees, employee_run_gate=gate,
                               run_view=SimpleNamespace(run_manager=manager), project_service=None, run_launcher=SimpleNamespace(checkpointer=None), workflow_service=None)
    control = GroupExecution(pipeline)
    await control.pause()
    manager.cancel.assert_awaited_once_with('group', action='interrupt', user_id='owner')
    assert manager.list_by_thread.call_args.args == ('a:group',)


@pytest.mark.asyncio
async def test_stale_admitted_launch_cannot_cross_pause_boundary():
    from deerflow.office.group_execution import GroupExecution

    gate = EmployeeRunGate()
    await gate.admit(item('admitted'))
    control = GroupExecution(SimpleNamespace(employee_run_gate=gate))
    start = AsyncMock()
    guarded = control.guard(start, 'admitted')
    await gate.pause_group()
    with pytest.raises(ValueError, match='暂停'):
        await guarded(None, None, None)
    start.assert_not_called()


@pytest.mark.asyncio
async def test_request_admitted_after_stop_still_holds_its_old_generation():
    gate = EmployeeRunGate()
    await gate.pause_group()
    await gate.finish_group_pause()
    assert not (await gate.admit(item('old-request'), hold=True)).started
    assert (await gate.admit(item('new-request'))).started
    assert await gate.is_held('old-request')


@pytest.mark.asyncio
async def test_pause_waits_for_already_crossed_start_and_does_not_freeze_private():
    from deerflow.office.group_execution import GroupExecution

    gate = EmployeeRunGate()
    await gate.admit(item('launching'))
    entered, release = asyncio.Event(), asyncio.Event()
    records = []
    manager = SimpleNamespace(list_by_thread=AsyncMock(side_effect=lambda *a, **k: list(records)), cancel=AsyncMock(), update_metadata=AsyncMock())
    p = SimpleNamespace(user_id='owner', employee_run_gate=gate, project_service=None, run_launcher=SimpleNamespace(checkpointer=None), workflow_service=None,
                        employee_service=SimpleNamespace(list_employees=AsyncMock(return_value=[{'id': 'a'}]), thread_id=lambda *a: 'group'),
                        run_view=SimpleNamespace(run_manager=manager))
    control = GroupExecution(p)

    async def start(*args):
        entered.set()
        await release.wait()
        records.append(SimpleNamespace(run_id='late', status=RunStatus.running, task=None, user_id='owner', assistant_id='a', thread_id='group', metadata={'office_conversation_kind': 'group'}))

    launch = asyncio.create_task(control.guard(start, 'launching')(None, None, None))
    await entered.wait()
    pause = asyncio.create_task(control.pause())
    await asyncio.sleep(0)
    assert not pause.done()
    assert (await gate.admit(item('private', 'private', 'b'))).started
    release.set()
    await launch
    state = await pause
    assert state['stopping']
    manager.cancel.assert_awaited_once_with('late', action='interrupt', user_id='owner')


@pytest.mark.asyncio
async def test_resume_never_resumes_a_private_workflow():
    from deerflow.office.group_execution import GroupExecution

    private_wf = SimpleNamespace(workflow_id='private-wf', status=SimpleNamespace(value='paused'), phase_runs=[{'runId': 'private'}])
    service = SimpleNamespace(get=lambda: private_wf, resume=AsyncMock())
    p = SimpleNamespace(user_id='owner', employee_run_gate=EmployeeRunGate(), project_service=None, run_launcher=SimpleNamespace(checkpointer=None), workflow_service=service,
                        employee_service=SimpleNamespace(list_employees=AsyncMock(return_value=[])),
                        run_view=SimpleNamespace(run_manager=None))
    control = GroupExecution(p)
    await control.resume()
    service.resume.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_held_item_cannot_resurrect_on_continue():
    gate = EmployeeRunGate()
    await gate.pause_group()
    await gate.admit(item('held'))
    await gate.delete('held')
    await gate.finish_group_pause()
    await gate.resume_group()
    assert await gate.get_admission('held') is None
    assert not await gate.list_pending()


def test_group_http_boundary_rejects_a_different_user(monkeypatch):
    from fastapi import HTTPException

    from app.gateway.routers import office

    monkeypatch.setattr(office, 'get_effective_user_id', lambda: 'intruder')
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(office_message_pipeline=SimpleNamespace(user_id='owner'))))
    with pytest.raises(HTTPException) as error:
        office.get_office_message_pipeline(request)
    assert error.value.status_code == 403


def test_pause_generation_is_not_part_of_message_idempotency():
    from dataclasses import replace

    from deerflow.office.message_pipeline import MessageDispatchRequest

    request = MessageDispatchRequest('message', 'a', 'group', 'work', (), group_epoch=1)
    assert replace(request, group_epoch=2) == request


@pytest.mark.asyncio
@pytest.mark.parametrize('text,mentions', [('继续执行', ()), ('@项目经理 继续执行', ('pm',)), ('@项目经理\u00a0请继续吧', ('pm',))])
async def test_plain_continue_is_idempotent_and_does_not_start_an_agent(tmp_path, text, mentions):
    from test_office_message_pipeline import EmployeeServiceStub

    from deerflow.config.paths import Paths
    from deerflow.office.group_messages import GroupMessageStore
    from deerflow.office.message_pipeline import OfficeMessagePipeline
    from deerflow.office.messages import OfficeMessageCommand

    manager = SimpleNamespace(list_by_thread=AsyncMock(return_value=[]))
    pipeline = OfficeMessagePipeline(user_id='user-a', employee_service=EmployeeServiceStub(),
                                     group_store=GroupMessageStore(paths=Paths(tmp_path), user_id='user-a'),
                                     employee_run_gate=EmployeeRunGate(), run_launcher=None,
                                     run_view=SimpleNamespace(run_manager=manager), project_service=None)
    await pipeline.group_execution.pause()
    command = OfficeMessageCommand(conversation_kind='group', text=text, mention_employee_ids=mentions, target_employee_id=None)
    starter = AsyncMock()
    first = await pipeline.send('resume', command, start_run=starter, request=None)
    second = await pipeline.send('resume', command, start_run=starter, request=None)
    assert first == second
    assert not first['dispatches']
    assert not pipeline.group_execution.paused
    starter.assert_not_called()
    assert pipeline.group_store.find('resume').content == text


@pytest.mark.asyncio
@pytest.mark.parametrize('case,expected', [('current', ['current']), ('newer-message', []), ('project-changed', []), ('resuming', []), ('no-checkpoint', [])])
async def test_restore_and_continue_use_only_the_current_paused_checkpoint(tmp_path, case, expected):
    from test_office_message_pipeline import EmployeeServiceStub

    from deerflow.config.paths import Paths
    from deerflow.office.group_messages import GroupMessageStore
    from deerflow.office.message_pipeline import OfficeMessagePipeline
    from deerflow.runtime.runs.execution import CHECKPOINT_RUN_ID_METADATA_KEY

    employees = EmployeeServiceStub()
    records = [SimpleNamespace(run_id=key, assistant_id='pm', user_id='user-a',
                              thread_id=employees.thread_id('pm', 'group'), status='interrupted', error=None,
                              metadata={'office_control': 'paused', 'office_conversation_kind': 'group', 'office_project_id': 'project'})
               for key in ('old', 'current')]
    if case == 'resuming':
        records.append(SimpleNamespace(run_id='resuming', assistant_id='pm', user_id='user-a',
                       thread_id=records[-1].thread_id, status='pending',
                       metadata={'office_resume_source_run_id': 'current', 'office_conversation_kind': 'group'}))
    owner = 'newer' if case == 'newer-message' else 'current'
    checkpoint = None if case == 'no-checkpoint' else SimpleNamespace(metadata={CHECKPOINT_RUN_ID_METADATA_KEY: owner}, pending_writes=[])
    launcher = SimpleNamespace(checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=checkpoint)),
                               continue_from_checkpoint=AsyncMock())
    manager = SimpleNamespace(list_by_thread=AsyncMock(side_effect=lambda thread, **kwargs: records if thread == records[0].thread_id else []))
    view = SimpleNamespace(run_manager=manager, resolve_run=AsyncMock(side_effect=lambda employee, run: (employee, next(row for row in records if row.run_id == run))))
    project = SimpleNamespace(get_binding=lambda: SimpleNamespace(project_id='changed' if case == 'project-changed' else 'project'))
    pipeline = OfficeMessagePipeline(user_id='user-a', employee_service=employees,
        group_store=GroupMessageStore(paths=Paths(tmp_path), user_id='user-a'), employee_run_gate=EmployeeRunGate(),
        run_launcher=launcher, run_view=view, project_service=project)
    try:
        await pipeline.restore_waits()
        assert pipeline.group_execution.paused == bool(expected)
        await pipeline.queue_paused_continuations(start_run=AsyncMock(), request=None)
        await pipeline.queue_paused_continuations(start_run=AsyncMock(), request=None)
        assert [item.queue_id for item in await pipeline.employee_run_gate.list_pending()] == [f'continue:{key}' for key in expected]
        await pipeline.employee_run_gate.resume_group()
        await asyncio.gather(*list(pipeline._queued_message_tasks))
        assert [call.args[0].run_id for call in launcher.continue_from_checkpoint.call_args_list] == expected
        assert pipeline.group_execution.error == ''
    finally:
        await pipeline.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('text,mentions,paused,employee', [
    ('@项目经理 继续修改首页', ('pm',), True, 'pm'),
    ('@别的名字 继续执行', ('pm',), True, 'pm'),
    ('@前端员工 继续执行', ('frontend-a',), True, 'frontend-a'),
    ('@项目经理 继续执行', ('pm',), False, 'pm'),
])
async def test_continue_control_matching_does_not_rewrite_normal_messages(tmp_path, text, mentions, paused, employee):
    from test_office_message_pipeline import _pipeline

    from deerflow.office.messages import OfficeMessageCommand

    pipeline, launcher, store = _pipeline(tmp_path)
    pipeline.group_execution.paused = paused
    try:
        result = await pipeline.send('normal', OfficeMessageCommand('group', text, None, mentions),
                                     start_run=AsyncMock(), request=None)
        assert result['triggerEmployeeIds'] == [employee]
        await launcher.launched.wait()
        assert launcher.employee_ids == [employee]
        assert store.find('normal').content == text
        assert pipeline.group_execution.paused == paused
    finally:
        await pipeline.shutdown()


@pytest.mark.asyncio
async def test_pause_inventory_error_does_not_permanently_lock_new_chat():
    from deerflow.office.group_execution import GroupExecution

    gate = EmployeeRunGate()
    p = SimpleNamespace(employee_run_gate=gate, employee_service=SimpleNamespace(list_employees=AsyncMock(side_effect=RuntimeError('offline'))))
    control = GroupExecution(p)
    with pytest.raises(RuntimeError, match='offline'):
        await control.pause()
    assert (await gate.admit(item('new-message'))).started


@pytest.mark.asyncio
async def test_native_stop_finishes_without_frontend_polling():
    from deerflow.office.group_execution import GroupExecution

    gate = EmployeeRunGate()
    admission = await gate.admit(item('running'))
    await gate.admit(item('old-queued'))
    finished = asyncio.Event()
    record = SimpleNamespace(run_id='run', status=RunStatus.running, finalizing=False, user_id='owner', assistant_id='a', thread_id='group', metadata={'office_conversation_kind': 'group'})

    async def worker():
        await finished.wait()
        record.status = RunStatus.interrupted
        await gate.release(admission.lease_id)

    record.task = asyncio.create_task(worker())
    manager = SimpleNamespace(list_by_thread=AsyncMock(return_value=[record]), cancel=AsyncMock(), update_metadata=AsyncMock())
    p = SimpleNamespace(user_id='owner', employee_run_gate=gate, project_service=None, run_launcher=SimpleNamespace(checkpointer=None), workflow_service=None,
                        employee_service=SimpleNamespace(list_employees=AsyncMock(return_value=[{'id': 'a'}]), thread_id=lambda *a: 'group'),
                        run_view=SimpleNamespace(run_manager=manager))
    control = GroupExecution(p, stop_timeout=0)
    assert (await control.pause())['stopping']
    finished.set()
    await record.task
    await asyncio.gather(*list(control.settlers))
    # No GET/state call after the worker completed: backend owns reopening.
    assert (await gate.admit(item('new-message'))).started
    assert await gate.is_held('old-queued')
    await control.shutdown()
