"""Full HTTP entry -> native Agent/employee tools -> persisted facts; isolated files."""
import os
import json
import sqlite3
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def gateway(tmp_path, request):
    backend = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    home = tmp_path / 'gateway'
    with (tmp_path / 'gateway.log').open('w', encoding='utf-8') as log:
        env = {**os.environ, 'PYTHONPATH': str(backend / 'packages/harness'), 'PYTHONUTF8': '1',
               'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
        env['OFFICE_TEST_TOOL_CONTRACT'] = '1' if getattr(request, 'param', None) == 'tools' else '0'
        process = subprocess.Popen([sys.executable, 'tests/office_gateway_fixture.py', str(home), str(port)],
                                   cwd=backend, env=env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=20, trust_env=False) as client:
                deadline = time.monotonic() + 50
                while time.monotonic() < deadline:
                    try:
                        if client.get('/health').is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    assert process.poll() is None, (tmp_path / 'gateway.log').read_text(encoding='utf-8')[-6000:]
                    time.sleep(.2)
                else:
                    pytest.fail('Gateway startup timed out')
                yield client, home
        finally:
            if os.name == 'nt' and process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=False)
            elif process.poll() is None:
                process.terminate()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=20)


def wait_run(client, run_id, status='success'):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        record = client.get(f'/api/office/employees/pm/runs/{run_id}').json()
        if record['eventsFinalized'] and not record['finalizing']:
            assert record['status'] == status, record
            return record
        time.sleep(.1)
    pytest.fail(f'Run {run_id} did not finish: {record}')


def send(client, text, mentions=('pm',)):
    before = {r['runId'] for r in client.get('/api/office/employees/pm/runs?conversationKind=group').json()['runs']}
    response = client.post('/api/office/messages', json=dict(messageId=str(uuid.uuid4()), conversationKind='group',
                                                          text=text, mentionEmployeeIds=list(mentions)))
    assert response.is_success, response.text
    for _ in range(100):
        runs = client.get('/api/office/employees/pm/runs?conversationKind=group').json()['runs']
        if new := next((r for r in runs if r['runId'] not in before), None):
            return new['runId']
        time.sleep(.1)
    pytest.fail(f'No run admitted: {response.text}')


def in_place_result_root(home, run_id):
    with sqlite3.connect(home / 'users/default/office-execution-receipts.v1.sqlite3') as db:
        receipt = db.execute("SELECT result_ref_json FROM delegation_receipts WHERE parent_run_id=? AND status='succeeded'", (run_id,)).fetchone()
        result = json.loads(receipt[0])
        assert 'integration_status' not in result
        assert result['workspace_id'] is None and 'result_commit' not in result
        assert db.execute('SELECT COUNT(*) FROM execution_workspaces').fetchone()[0] == 0
    return home / 'project'


def test_native_goal_compositions_and_real_outputs(gateway):
    client, home = gateway
    project = home / 'project'
    for name in ('intro.md', 'filter.txt'):
        (project / name).write_text('original\n', encoding='utf-8')
    expected_intro = 'original\n'
    expected_filter = 'original\n'
    for goal in ('DIRECT', 'REVIEW', 'DOC', 'BUILD', 'REWORK'):
        run = wait_run(client, send(client, f'GOAL_{goal} Complete this goal.'))
        assert f'GOAL_{goal}_DONE' in run['lastAiMessage']
        assert run['lastEventSeq'] > 0
        evidence = client.get(f"/api/office/employees/pm/runs/{run['runId']}/events").json()
        assert evidence
        if goal == 'DOC':
            assert in_place_result_root(home, run['runId']) == project
            expected_intro = '# Product introduction\nPM coordinates employees.\n'
        if goal in {'BUILD', 'REWORK'}:
            assert in_place_result_root(home, run['runId']) == project
            expected_filter = 'department=engineering\n'
        assert (project / 'intro.md').read_text(encoding='utf-8') == expected_intro
        assert (project / 'filter.txt').read_text(encoding='utf-8') == expected_filter
        execution = client.get('/api/office/group/execution').json()
        assert execution['runTargets'] == []
    # Legacy history was never created even though execution order differed.
    assert client.get('/api/office/workflows/current').json()['workflow'] is None
    pending = wait_run(client, send(client, 'GOAL_ASK Ask the department before delegating.'), status='interrupted')
    assert client.get('/api/office/group/execution').json()['runTargets'][0]['runId'] == pending['runId']
    assert next(e for e in client.get('/api/office/employees').json()['employees'] if e['id'] == 'pm')['state'] == 'working'
    answer = client.post(f"/api/office/employees/pm/runs/{pending['runId']}/resume", json={
        'source': 'ask_clarification', 'requestId': 'clarification:goal-ASK-0',
        'text': 'engineering; replace the earlier sales requirement',
    })
    assert answer.is_success, answer.text
    resumed = wait_run(client, answer.json()['runId'])
    assert resumed['resumeSourceRunId'] == pending['runId']
    assert resumed['workId'] == pending['workId']
    assert 'GOAL_ASK_DONE' in resumed['lastAiMessage']
    assert in_place_result_root(home, resumed['runId']) == project
    assert (project / 'filter.txt').read_text(encoding='utf-8') == 'department=engineering\n'
    # Refresh sees the same terminal identity and actual events; nothing re-dispatches.
    assert client.get(f"/api/office/employees/pm/runs/{resumed['runId']}").json() == resumed
    assert client.get('/api/office/group/execution').json()['runTargets'] == []

    with sqlite3.connect(home / 'users/default/office-execution-receipts.v1.sqlite3') as db:
        receipts = db.execute('SELECT input_ref_json,status FROM delegation_receipts WHERE parent_run_id=?',
                              (resumed['runId'],)).fetchall()
    assert len(receipts) == 1 and receipts[0][1] == 'succeeded'
    assert 'replace the earlier sales requirement' in json.dumps(json.loads(receipts[0][0]))
    cancelled = wait_run(client, send(client, 'GOAL_ASK A question that will be cancelled.'), status='interrupted')
    assert client.post(f"/api/office/employees/pm/runs/{cancelled['runId']}/cancel").is_success
    fresh = wait_run(client, send(client, 'GOAL_DIRECT A new goal after cancelling the old question.'))
    assert 'GOAL_DIRECT_DONE' in fresh['lastAiMessage']
    assert fresh['workId'] != cancelled['workId']
    assert client.get('/api/office/group/execution').json()['runTargets'] == []


def test_two_ask_answers_reach_employee_background(gateway):
    client, home = gateway
    (home / 'project/filter.txt').write_text('original\n', encoding='utf-8')
    pending = wait_run(client, send(client, 'GOAL_MULTI Add department filter.'), 'interrupted')
    sources = []
    for index, text in enumerate(('Only active departments.', 'Use blue.')):
        sources.append(pending['runId'])
        response = client.post(f"/api/office/employees/pm/runs/{pending['runId']}/resume", json={
            'source': 'ask_clarification', 'requestId': f'clarification:goal-MULTI-{index}', 'text': text,
        })
        assert response.is_success, response.text
        pending = wait_run(client, response.json()['runId'], 'interrupted' if index == 0 else 'success')
    with sqlite3.connect(home / 'users/default/office-execution-receipts.v1.sqlite3') as db:
        raw = db.execute('SELECT input_ref_json FROM delegation_receipts WHERE parent_run_id=?', (pending['runId'],)).fetchone()[0]
    background = json.loads(raw)['background']
    assert background['user_answers'] == [
        {'source_run_id': sources[0], 'request_id': 'clarification:goal-MULTI-0', 'text': 'Only active departments.'},
        {'source_run_id': sources[1], 'request_id': 'clarification:goal-MULTI-1', 'text': 'Use blue.'},
    ]


@pytest.mark.parametrize('selected', [False, True])
def test_pm_todos_follow_saved_selection_without_blocking_final_answer(gateway, selected):
    client, _ = gateway
    if selected:
        url = '/api/office/employees/pm/tools'
        current = client.get(url).json()
        saved = client.put(url, json={'tools': [*current['tools'], 'write_todos'], 'expectedRevision': current['revision']})
        assert saved.is_success, saved.text
    record = wait_run(client, send(client, 'GOAL_TODO Track work, then report the blocker.'))
    assert record['lastAiMessage'] == ('GOAL_TODO_DONE' if selected else 'TODO_TOOL_FAILED')
    assert record['lastEventSeq'] > 0
    events = client.get(f"/api/office/employees/pm/runs/{record['runId']}/events").json()
    assert 'write_todos' in json.dumps(events)
    assert client.get('/api/office/group/execution').json()['runTargets'] == []


def test_pm_can_capture_only_a_verified_user_message_as_project_knowledge(gateway):
    client, home = gateway
    message_id = 'knowledge-source-message'
    response = client.post('/api/office/messages', json={
        'messageId': message_id,
        'conversationKind': 'group',
        'text': 'GOAL_KNOWLEDGE Only active departments are in scope.',
        'mentionEmployeeIds': ['pm'],
    })
    assert response.is_success, response.text
    run_id = next(iter(response.json()['dispatches']))['runId'] if response.json()['dispatches'] else None
    assert run_id
    record = wait_run(client, run_id)
    assert 'GOAL_KNOWLEDGE_DONE' in record['lastAiMessage']
    with sqlite3.connect(home / 'users/default/office-project-knowledge.v1.sqlite3') as db:
        rows = db.execute('SELECT payload_json FROM project_knowledge WHERE project_id=?', (record['projectId'],)).fetchall()
    assert len(rows) == 1, json.dumps(record, ensure_ascii=False)
    payload = json.loads(rows[0][0])
    assert payload['source_type'] == 'user_statement'
    assert payload['source_message_id'] == message_id
    assert payload['statement'] == 'GOAL_KNOWLEDGE Only active departments are in scope.'


def test_pm_cannot_capture_a_fabricated_user_message(gateway):
    client, home = gateway
    response = client.post('/api/office/messages', json={
        'messageId': 'knowledge-invalid-source-message',
        'conversationKind': 'group',
        'text': 'GOAL_KNOWLEDGE_BAD Do not invent a source.',
        'mentionEmployeeIds': ['pm'],
    })
    assert response.is_success, response.text
    run_id = response.json()['dispatches'][0]['runId']
    record = wait_run(client, run_id)
    assert 'source user message was not found' in record['lastAiMessage']
    with sqlite3.connect(home / 'users/default/office-project-knowledge.v1.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM project_knowledge').fetchone()[0] == 0


@pytest.mark.parametrize('gateway', ['tools'], indirect=True)
def test_live_gateway_binds_employee_vision_memory_and_applies_removal(gateway):
    def bound_tools(record):
        # Inspect the model's actual bound names, not tool names mentioned in
        # workspace/permission guidance appended after the original report.
        raw = record['lastAiMessage'].split('CONTRACT_BOUND ', 1)[1]
        return set(json.JSONDecoder().raw_decode(raw)[0])
    client, _ = gateway
    url = '/api/office/employees/writer/tools'
    initial = client.get(url).json()
    assert {'view_image', 'memory_search', 'memory_add'} <= set(initial['availableTools'])
    first = wait_run(client, send(client, 'CONTRACT_DELEGATE Check delegated employee bindings.'))
    for name in ('view_image', 'memory_search', 'memory_add', 'report_to_pm'):
        assert name in bound_tools(first), first['lastAiMessage']
    assert 'ask_clarification' not in bound_tools(first)
    removed = client.put(url, json={'tools': [t for t in initial['tools'] if t not in {'view_image', 'memory_search'}],
                                   'expectedRevision': initial['revision']})
    assert removed.status_code == 200, removed.text
    second = wait_run(client, send(client, 'CONTRACT_DELEGATE Check removed tools on next delegation.'))
    assert 'CONTRACT_BOUND' in second['lastAiMessage']
    assert 'view_image' not in bound_tools(second)
    assert 'memory_search' not in bound_tools(second)
    assert 'memory_add' in bound_tools(second)
    # Direct employee run uses the same manifest, via the real HTTP entry.
    response = client.post('/api/office/messages', json={'messageId': str(uuid.uuid4()), 'conversationKind': 'group',
        'text': 'CONTRACT_BOUND Check my tools.', 'mentionEmployeeIds': ['writer']})
    assert response.is_success, response.text
    run_id = response.json()['dispatches'][0]['runId']
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        direct = client.get(f'/api/office/employees/writer/runs/{run_id}').json()
        if direct['eventsFinalized'] and not direct['finalizing']:
            break
        time.sleep(.1)
    assert direct['status'] == 'success' and direct['eventsFinalized'], direct
    assert 'CONTRACT_BOUND' in direct['lastAiMessage']
    assert 'view_image' not in bound_tools(direct) and 'memory_search' not in bound_tools(direct)
    assert 'memory_add' in bound_tools(direct)
