import json
import sqlite3
from types import SimpleNamespace

import pytest
from office_history_fixture import seed_history

from deerflow.config.paths import Paths
from deerflow.office.workflow_service import WorkflowService
from deerflow.office.workflows import WorkflowStore


def test_history_has_no_execution_or_creation_surface(tmp_path):
    store = WorkflowStore(paths=Paths(tmp_path), user_id='alice')
    for name in ('create', 'save', 'apply_result', 'update_execution', 'get_or_create'):
        assert not hasattr(store, name)
    assert store.get('missing') is None
    assert store.list() == ()
    assert store.clear(project_id='p') == 0
    assert not store.path.exists(), 'a new office must not create an obsolete workflow database'


def test_retire_preserves_provenance_and_is_idempotent(tmp_path):
    store = WorkflowStore(paths=Paths(tmp_path), user_id='alice', now=lambda: 10)
    original = seed_history(store, requirement={'summary': 'keep'},
                            phase_runs=[{'run_id': 'old-run'}],
                            knowledge_facts=[{'statement': 'source'}], historical_extension={'source': 'preserve'})
    retired = store.retire('history', project_id='p')
    assert retired.status.value == 'cancelled'
    assert retired.requirement == original.requirement
    assert retired.knowledge_facts == original.knowledge_facts
    assert retired.phase_runs == original.phase_runs
    assert store.retire('history', project_id='p') == retired
    with sqlite3.connect(store.path) as db:
        payload = json.loads(db.execute('SELECT payload_json FROM workflows').fetchone()[0])
    assert payload['historical_extension'] == {'source': 'preserve'}
    with pytest.raises(ValueError):
        store.retire('history', project_id='other')


def test_history_cannot_cross_owner_even_with_explicit_owner(tmp_path):
    store = WorkflowStore(paths=Paths(tmp_path), user_id='alice')
    seed_history(store, workflow_id='bob-history', user_id='bob')
    assert store.get('bob-history', user_id='bob') is None
    assert store.list() == ()


@pytest.mark.anyio
async def test_history_api_has_no_pause_resume_and_preserves_project_scope(tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.gateway.routers import office

    store = WorkflowStore(paths=Paths(tmp_path), user_id='alice')
    seed_history(store)
    binding = SimpleNamespace(project_id='p')
    service = WorkflowService(store=store, project_service=SimpleNamespace(get_binding=lambda: binding))
    app = FastAPI()
    app.include_router(office.router)
    app.dependency_overrides[office.get_office_workflow_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        assert (await client.get('/api/office/workflows/current')).json()['workflow']['workflow_id'] == 'history'
        for action in ('pause', 'resume'):
            assert (await client.post(f'/api/office/workflows/history/{action}')).status_code == 404
        assert (await client.post('/api/office/workflows/history/cancel')).json()['workflow']['status'] == 'cancelled'
        binding.project_id = 'other'
        assert (await client.get('/api/office/workflows/history')).status_code == 404
    app.state.office_workflow_service = service
    from deerflow.runtime.user_context import reset_current_user, set_current_user
    token = set_current_user(SimpleNamespace(id='bob'))
    try:
        with pytest.raises(Exception) as exc:
            office.get_office_workflow_service(SimpleNamespace(app=app))
        assert exc.value.status_code == 403
    finally:
        reset_current_user(token)
