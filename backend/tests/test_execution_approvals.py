import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.config.paths import Paths
from deerflow.office.execution_facts import ExecutionReceiptStore
from deerflow.office.execution_approvals import ExecutionApprovals, access_mode


def pending(manager, count=1):
    deadline = time.monotonic() + 3
    while len(manager.pending()) != count:
        assert time.monotonic() < deadline
        time.sleep(.01)
    return manager.pending()


def wait(manager, identifier='command', cancelled=lambda: False):
    return manager.wait(execution_id=identifier, context={'run_id': 'r', 'office_native_task_id': identifier},
        employee_id=identifier, command='Write-Output hello', cwd='C:/task', request_hash=identifier, cancelled=cancelled)


def test_approval_matches_exact_hash_does_not_follow_mode_and_times_wait(tmp_path):
    store = ExecutionReceiptStore(paths=Paths(tmp_path), user_id='alice')
    manager = ExecutionApprovals(store)
    assert access_mode(store) == 'approval'
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(wait, manager, 'a'); second = pool.submit(wait, manager, 'b')
        values = pending(manager, 2)
        (store.paths.user_dir('alice') / 'office-bootstrap.yaml').write_text('terminal_access_mode: full_access')
        time.sleep(.08)
        assert not first.done() and not second.done()
        assert manager.waited_seconds('r', 'a') >= .07
        value = next(v for v in values if v['execution_id'] == 'a')
        with pytest.raises(ValueError, match='changed'): manager.decide(value['approval_id'], 'wrong', 'approve')
        manager.decide(value['approval_id'], 'a', 'approve')
        assert first.result(2) is None
        with pytest.raises(ValueError, match='no longer'): manager.decide(value['approval_id'], 'a', 'approve')
        value = next(v for v in values if v['execution_id'] == 'b')
        manager.decide(value['approval_id'], 'b', 'reject')
        with pytest.raises(PermissionError, match='rejected'): second.result(2)
        duration = manager.waited_seconds('r', 'a')
        time.sleep(.05)
        assert manager.waited_seconds('r', 'a') == duration
        with pytest.raises(PermissionError): wait(manager, 'b')


@pytest.mark.parametrize('action', ['cancel', 'restart', 'expire'])
def test_cancel_restart_expire_never_replays(tmp_path, action):
    store = ExecutionReceiptStore(paths=Paths(tmp_path), user_id='alice')
    manager = ExecutionApprovals(store)
    abort = threading.Event()
    with ThreadPoolExecutor() as pool:
        task = pool.submit(wait, manager, 'a', abort.is_set)
        value = pending(manager)[0]
        if action == 'cancel': abort.set()
        elif action == 'restart':
            restored = ExecutionApprovals(store)
            assert restored.pending() == []
            with pytest.raises(ValueError): restored.decide(value['approval_id'], 'a', 'approve')
        else:
            value['expires_at'] = 0
            with store._connect() as db: manager._save(db, value)
        with pytest.raises(PermissionError): task.result(2)
        assert not manager.pending()
        with pytest.raises(PermissionError): wait(manager, 'a')


def test_native_child_timeout_excludes_actual_approval_wait(tmp_path, monkeypatch):
    import asyncio
    from deerflow.office import terminal
    from deerflow.subagents.executor import SubagentExecutor, SubagentStatus, get_background_task_result
    from deerflow.subagents.config import SubagentConfig
    paths = Paths(tmp_path)
    monkeypatch.setattr('deerflow.config.paths.get_paths', lambda: paths)
    manager = terminal.manager_for(ExecutionReceiptStore(paths=paths, user_id='alice')).approvals
    executor = SubagentExecutor(config=SubagentConfig(name='test', description='test', system_prompt='test', timeout_seconds=.3),
        tools=[], user_id='alice', run_id='r', office_context={'office_work': {'work_id': 'w'}})
    async def child(task, holder):
        await asyncio.to_thread(wait, manager, holder.task_id, holder.cancel_event.is_set)
        holder.try_set_terminal(SubagentStatus.COMPLETED, result='approved')
        return holder
    monkeypatch.setattr(executor, '_aexecute', child)
    task_id = executor.execute_async('test')
    try:
        value = pending(manager)[0]
        time.sleep(.6)  # twice the native budget; still the original occupied task
        assert get_background_task_result(task_id).status == SubagentStatus.RUNNING
        manager.decide(value['approval_id'], value['request_hash'], 'approve')
        deadline = time.monotonic() + 2
        while not get_background_task_result(task_id).status.is_terminal:
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert get_background_task_result(task_id).status == SubagentStatus.COMPLETED
    finally:
        terminal.close_terminals()


@pytest.mark.asyncio
async def test_group_tools_is_one_choice_and_pm_uses_manifest_without_role_blacklist(tmp_path, monkeypatch):
    from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload, employee_app_config
    from deerflow.office.packages import resolve_employee_subagent_package
    from deerflow.config import agents_config
    svc, _, _ = build_employee_service(tmp_path)
    monkeypatch.setattr(agents_config, 'get_paths', lambda: svc.paths)
    await svc.create_employee(frontend_employee_payload())
    before = svc.get_employee_tools('frontend-a')
    after = await svc.save_employee_tools('frontend-a', {'tools': before['tools'] + ['powershell_terminal'], 'expectedRevision': before['revision']})
    assert 'powershell_terminal' in after['tools']
    assert 'powershell_run' not in after['tools']
    assert {'powershell_run', 'powershell_read', 'powershell_list', 'powershell_stop'} <= set(after['availableTools'])
    package = resolve_employee_subagent_package('frontend-a', 'default', employee_app_config())
    assert {'powershell_run', 'powershell_read', 'powershell_list', 'powershell_stop', 'publish_task_result'} <= set(package.config.tools)
    await svc.ensure_builtin_pm(default_model_profile_id='profile-primary')
    pm = svc.get_employee_tools('pm')
    assert 'powershell_run' not in pm['availableTools']
    granted = await svc.save_employee_tools('pm', {'tools': pm['tools'] + ['powershell_terminal'], 'expectedRevision': pm['revision']})
    assert 'powershell_run' in granted['availableTools']


@pytest.mark.asyncio
async def test_approval_api_user_boundary_and_decision(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload
    from app.gateway.routers.office import router, get_office_employee_service, _execution_approvals
    from deerflow.office.terminal import close_terminals
    svc, _, _ = build_employee_service(tmp_path)
    await svc.create_employee(frontend_employee_payload())
    manager = _execution_approvals(svc)
    app = FastAPI(); app.include_router(router)
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        assistant_id='pm', metadata={'office_conversation_kind': 'group', 'office_project_id': 'project-a'})))
    app.dependency_overrides[get_office_employee_service] = lambda: svc
    with TestClient(app) as client, ThreadPoolExecutor() as pool:
        task = pool.submit(wait, manager, 'frontend-a')
        pending(manager)
        values = client.get('/api/office/execution-approvals').json()['items']
        assert len(values) == 1
        value = values[0]
        assert value['conversation_kind'] == 'group'
        assert value['conversation_employee_id'] == 'pm'
        assert value['project_id'] == 'project-a'
        app.state.run_manager.get.assert_awaited_with('r', user_id=svc.user_id)
        route = '/api/office/execution-approvals/' + value['approval_id'] + '/decision'
        assert client.post(route, json={'request_hash': 'forged', 'decision': 'approve'}).status_code == 409
        assert client.post(route, json={'request_hash': 'frontend-a', 'decision': 'approve'}).status_code == 200
        task.result(2)
    close_terminals()
