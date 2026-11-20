import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload


@pytest.mark.asyncio
async def test_employee_memory_is_read_only_and_scoped_to_existing_employee(tmp_path, monkeypatch):
    from app.gateway.routers import office

    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload('employee-one'))
    await service.create_employee(frontend_employee_payload('employee-two'))
    manager = Mock()
    manager.get_memory.side_effect = lambda **scope: {
        'facts': [{'id': 'fact-1', 'content': scope['agent_name'], 'source': 'thread-1'}],
    }
    monkeypatch.setattr(office, 'get_memory_manager', lambda: manager)
    monkeypatch.setattr(office, 'get_effective_user_id', lambda: 'default')
    app = FastAPI()
    app.state.office_employee_service = service
    app.include_router(office.router)
    with TestClient(app) as client:
        for employee_id in ['employee-one', 'employee-two']:
            response = client.get(f'/api/office/employees/{employee_id}/memory')
            assert response.status_code == 200
            assert response.json()['facts'][0]['content'] == employee_id
            manager.get_memory.assert_called_with(agent_name=employee_id, user_id='default')
        manager.get_memory.reset_mock()
        assert client.get('/api/office/employees/missing/memory').status_code == 404
        assert client.get('/api/office/employees/bad_id/memory').status_code == 400
        manager.get_memory.assert_not_called()
        for method in ['post', 'put', 'delete']:
            assert getattr(client, method)('/api/office/employees/employee-one/memory').status_code == 405
        monkeypatch.setattr(office, 'get_effective_user_id', lambda: 'other-user')
        assert client.get('/api/office/employees/employee-one/memory').status_code == 403
        manager.get_memory.assert_not_called()


def test_employee_without_memories_returns_empty_document(monkeypatch):
    from app.gateway.routers import office

    monkeypatch.setattr(office, 'get_memory_manager', lambda: SimpleNamespace(get_memory=lambda **scope: {'facts': []}))
    service = SimpleNamespace(user_id='default', get_employee_summary=lambda employee_id: {'id': employee_id})
    response = office.get_employee_memory('employee-one', service)
    assert response.facts == []
    assert response.lastUpdated == ''


@pytest.mark.asyncio
async def test_employee_memory_reads_deermem_file_without_writes(tmp_path, monkeypatch):
    from app.gateway.routers import office
    from deerflow.agents.memory.backends.deermem.deer_mem import DeerMem

    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload('employee-one'))
    await service.create_employee(frontend_employee_payload('employee-two'))
    storage = tmp_path / 'memory-storage'
    memory_file = storage / 'users' / 'default' / 'agents' / 'employee-one' / 'memory.json'
    memory_file.parent.mkdir(parents=True)
    document = {'facts': [{'id': 'saved-fact', 'content': '保留真实记录', 'category': 'context', 'source': 'saved-thread'}]}
    memory_file.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    original = memory_file.read_bytes()
    modified = memory_file.stat().st_mtime_ns
    manager = DeerMem({'storage_path': str(storage), 'token_counting': 'char'})
    manager._updater._llm = Mock()
    monkeypatch.setattr(office, 'get_memory_manager', lambda: manager)
    monkeypatch.setattr(office, 'get_effective_user_id', lambda: 'default')
    app = FastAPI()
    app.state.office_employee_service = service
    app.include_router(office.router)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get('/api/office/employees/employee-one/memory')
            assert response.status_code == 200
            assert response.json()['facts'][0]['content'] == '保留真实记录'
        assert client.get('/api/office/employees/employee-two/memory').json()['facts'] == []
    assert memory_file.read_bytes() == original
    assert memory_file.stat().st_mtime_ns == modified
    assert list(storage.rglob('memory.json')) == [memory_file]
    manager._updater._llm.invoke.assert_not_called()
