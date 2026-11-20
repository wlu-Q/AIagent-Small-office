import json
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import office
from deerflow.office.execution_facts import DelegationIdentity, ExecutionReceiptStore, WorkIdentity
from deerflow.office.work_logs import EmployeeWorkLogs
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.store.memory import MemoryRunStore
from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload
from test_run_repository import _cleanup, _make_repo


@pytest.mark.asyncio
@pytest.mark.parametrize('sql', [False, True])
async def test_daily_log_merges_resumes_and_scopes_rows_without_loading_details(tmp_path, sql):
    employees, _, _ = build_employee_service(tmp_path)
    await employees.create_employee(frontend_employee_payload('worker'))
    runs = await _make_repo(tmp_path) if sql else MemoryRunStore()
    events = MemoryRunEventStore()
    thread = employees.thread_id('worker', 'private')
    logs = EmployeeWorkLogs(employees, runs, events, 'worker')
    first = datetime(2026, 11, 17, 23, 50).astimezone(UTC).isoformat()
    last = datetime(2026, 11, 18, 0, 10).astimezone(UTC).isoformat()

    async def run(run_id, state, when, employee='worker', user='default', **metadata):
        await runs.put(run_id, thread_id=thread, assistant_id=employee, user_id=user, status=state, created_at=when,
                       metadata={'office_conversation_kind': 'private', 'office_task_summary': run_id,
                                 'office_runtime_finished_at': when, **metadata}, events_finalized=state != 'running')

    try:
        await run('original', 'interrupted', first)
        await run('answer', 'success', last, office_resume_source_run_id='original', office_work_id='run:original')
        await runs.update_run_completion('answer', status='success', last_ai_message='delivered')
        await run('earlier', 'error', first)
        await run('paused', 'interrupted', last)
        await run('in-flight', 'running', last)
        await run('foreign', 'success', last, user='other-user')
        await run('wrong-employee', 'success', last, employee='other-employee')
        await events.put(thread_id=thread, run_id='original', event_type='run.start', category='trace', user_id='default', created_at=first)
        for caller in ('lead_agent', 'other-agent'):
            await events.put(thread_id=thread, run_id='answer', event_type='llm.tool.result', category='message',
                             content={'name': caller, 'content': 'raw tool output'}, metadata={'caller': caller}, user_id='default')
        await events.put(thread_id=thread, run_id='answer', event_type='llm.ai.response', category='message',
                         content={'tool_calls': [{'name': 'read_file'}], 'content': [{'type': 'reasoning', 'text': 'raw thought'}]},
                         metadata={'caller': 'lead_agent'}, user_id='default')
        page = await logs.days(None, 1)
        assert page == {'days': [{'date': '2026-11-18', 'count': 1}], 'nextBefore': '2026-11-18'}
        assert (await logs.days(date.fromisoformat(page['nextBefore']), 1))['days'] == [{'date': '2026-11-17', 'count': 1}]
        detail, = (await logs.day(date(2026, 11, 18)))['tasks']
        assert (detail['id'], detail['task'], detail['result'], detail['startedAt'], detail['endedAt']) == (
            'original', 'original', 'delivered', first, last)
        assert detail['steps'] == [{'text': 'lead_agent', 'error': False}]
        assert not (employees.paths.user_dir('default') / ExecutionReceiptStore.FILENAME).exists()
    finally:
        if sql:
            await _cleanup()


@pytest.mark.asyncio
async def test_gateway_reads_finished_delegations_by_employee_not_parent(tmp_path, monkeypatch):
    employees, _, _ = build_employee_service(tmp_path)
    for employee in ('worker', 'neighbor'):
        await employees.create_employee(frontend_employee_payload(employee))
    store = ExecutionReceiptStore(paths=employees.paths, user_id='default')
    work = WorkIdentity('default', 'project', 'pm-thread', 'group-request')
    events = MemoryRunEventStore()
    for number, status in enumerate(['succeeded', 'timeout', 'failed', 'interrupted', 'running', 'unknown', 'succeeded']):
        employee = 'neighbor' if number == 6 else 'worker'
        key = DelegationIdentity(work, 'ai', f'call-{number}')
        store.claim(key, employee_id=employee, request_hash='hash', parent_run_id='pm-run', input_ref={'task': f'task-{number}'})
        receipt = store.observe(key, parent_run_id='pm-run', native_task_id=f'native-{number}', status=status,
                                result_ref={'report': f'result-{number}'}, evidence=[])
        await events.put(thread_id='pm-thread', run_id='pm-run', event_type='subagent.step', category='trace',
                         content={'kind': 'tool', 'tool_name': f'operation-{number}', 'text': 'raw content'},
                         metadata={'employee_id': employee, 'task_id': f'native-{number}'}, user_id='default')
    today = datetime.fromtimestamp(receipt.updated_at / 1000).date().isoformat()
    app = FastAPI()
    app.include_router(office.router)
    app.state.office_employee_service = employees
    app.state.run_store = MemoryRunStore()
    app.state.run_event_store = events
    monkeypatch.setattr(office, 'get_effective_user_id', lambda: 'default')
    with TestClient(app) as client:
        url = '/api/office/employees/worker/work-logs'
        assert client.get(url).json() == {'days': [{'date': today, 'count': 4}], 'nextBefore': None}
        tasks = client.get(f'{url}/{today}').json()['tasks']
        assert {item['task'] for item in tasks} == {f'task-{number}' for number in range(4)}
        for item in tasks:
            number = item['task'].split('-')[1]
            assert item['result'] == f'result-{number}'
            assert item['steps'] == [{'text': f'operation-{number}', 'error': False}]
            assert item['startedAt'] is None and item['endedAt'] is None and item['recordedAt']
        assert 'raw content' not in json.dumps(tasks)
        assert client.get(url + '/not-a-date').status_code == 422
        assert client.get('/api/office/employees/nobody/work-logs').status_code == 404
        monkeypatch.setattr(office, 'get_effective_user_id', lambda: 'other-user')
        assert client.get(url).status_code == 403
