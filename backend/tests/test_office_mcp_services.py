"""Exercise the Office service lifecycle against an actual local MCP server."""
import asyncio
import json
import socket
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
import uvicorn
from mcp.server.fastmcp import FastMCP
from patch_helpers import update_patch
from test_apply_patch import tool_runtime as tool_runtime

from deerflow.config.extensions_config import reload_extensions_config
from deerflow.mcp.office_registry import registry
from deerflow.mcp.office_settings import Connection, ServiceChange, ServiceConflict, change_service, list_services


@asynccontextmanager
async def live_service(*, token=None):
    app = FastMCP('office-test', stateless_http=True, json_response=True)

    @app.tool()
    def echo(value: str) -> str:
        """Return the supplied text."""
        return value

    @app.tool()
    def square(value: int) -> int:
        """Square an integer."""
        return value * value

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        http_app = app.streamable_http_app()

        async def endpoint(scope, receive, send):
            from starlette.datastructures import Headers
            from starlette.responses import Response
            if token and scope['type'] == 'http' and Headers(scope=scope).get('authorization') != token:
                await Response(status_code=401)(scope, receive, send)
            else:
                await http_app(scope, receive, send)

        server = uvicorn.Server(uvicorn.Config(endpoint, log_level='error'))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(8):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.02)
            yield Connection(type='http', url=f'http://127.0.0.1:{port}/mcp')
        finally:
            server.should_exit = True
            await task


@pytest_asyncio.fixture
async def office(tmp_path, monkeypatch):
    path = tmp_path / 'extensions.json'
    path.write_text('{"skills": {"sample": {"enabled": true}}}')
    monkeypatch.setenv('DEER_FLOW_EXTENSIONS_CONFIG_PATH', str(path))
    await registry.start()
    try:
        yield path
    finally:
        await registry.stop()
        reload_extensions_config()


async def change(name, action, connection=None):
    revision = next((r['revision'] for r in list_services() if r['name'] == name), '')
    return await change_service(name, ServiceChange(action=action, revision=revision, connection=connection))


@pytest.mark.asyncio
async def test_real_http_grant_isolation_reconnect_and_restart(office):
    from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload
    from deerflow.office.tool_contract import build_tool_catalog, resolve_employee_tools
    from deerflow.office.authority import normalize_authority
    from deerflow.office.tool_manifest import EmployeeToolManifest
    service, _, _ = build_employee_service(office.parent)
    await service.create_employee(frontend_employee_payload())
    actor = 'frontend-a'

    async with live_service() as connection:
        row, = await change('research', 'create', connection)
        assert (row['name'], row['status'], row['count']) == ('research', 'ready', 2), row['error']
        config = service.get_employee_tools(actor)
        grant, = config['mcpServices']
        assert grant['id'] not in config['tools']
        saved = await service.save_employee_tools(actor, {'expectedRevision': config['revision'],
                                                           'tools': [*config['tools'], grant['id']]})
        assert {'mcp/research/echo', 'mcp/research/square'} <= set(saved['availableTools'])
        tools = {t.name: t for t in registry.loaded_tools()}
        result = await tools['research_echo'].ainvoke({'value': 'from-office'})
        assert 'from-office' in str(result)
        from types import SimpleNamespace
        from deerflow.office.tool_guard import EmployeeToolGuard
        directory = service.paths.user_agent_dir('default', actor)
        guard = EmployeeToolGuard(directory, SimpleNamespace(revision=saved['revision'],
                                                              runtime_names=['research_echo']))
        call = SimpleNamespace(tool_call={'name': 'research_echo', 'id': 'call'}, tool=tools['research_echo'])
        assert guard.wrap_tool_call(call, lambda _: 'called') == 'called'
        before = tools['research_echo'].metadata['office_mcp_binding']
        await change('research', 'reconnect')
        assert not registry.binding_is_current('research_echo', before)
        assert guard.wrap_tool_call(call, lambda _: 'called').status == 'error'
        assert guard.wrap_tool_call(SimpleNamespace(tool_call=call.tool_call), lambda _: 'called').status == 'error'
        assert service.get_employee_tools(actor)['mcpServices'] == [grant]
        await registry.stop()
        await registry.start()
        await asyncio.gather(*list(registry.tasks))
        assert grant['id'] in service.get_employee_tools(actor)['tools']
        await change('research', 'disable')
        assert service.get_employee_tools(actor)['mcpServices'] == []
        await change('research', 'enable')
        after = service.get_employee_tools(actor)
        assert grant['id'] != after['mcpServices'][0]['id']
        assert 'mcp/research/echo' not in after['availableTools']
        # A legacy per-tool selection also cannot authorize the replacement service.
        manifest = EmployeeToolManifest(1, ('read_file', 'report_to_pm', 'mcp/research/echo'))
        authority = normalize_authority(service._read_config(actor)['runtime_contract']['authority'])
        resolved = resolve_employee_tools(manifest, catalog=build_tool_catalog(), authority=authority)
        assert 'research_echo' not in resolved.runtime_names
        fresh = EmployeeToolManifest(1, ('read_file', 'report_to_pm', after['mcpServices'][0]['id']))
        deferred = resolve_employee_tools(fresh, catalog=build_tool_catalog(), authority=authority, deferred_enabled=True)
        assert {'tool_search', 'research_echo', 'research_square'} <= set(deferred.runtime_names)
        await change('research', 'delete')
        await change('research', 'create', connection)
        assert grant['id'] not in service.get_employee_tools(actor)['tools']


@pytest.mark.asyncio
async def test_concurrent_changes_conflict_only_on_same_revision(office):
    async with live_service() as connection:
        await asyncio.gather(*(change(n, 'create', connection) for n in ['left', 'right']))
        assert {r['name'] for r in list_services() if r['count'] == 2} == {'left', 'right'}
        rev = next(r['revision'] for r in list_services() if r['name'] == 'left')
        request = ServiceChange(action='disable', revision=rev)
        results = await asyncio.gather(change_service('left', request), change_service('left', request), return_exceptions=True)
        assert sum(isinstance(r, ServiceConflict) for r in results) == 1
        assert registry.entries()['right'].status == 'ready'
        assert json.loads(office.read_text())['skills']['sample']['enabled']


@pytest.mark.asyncio
async def test_failed_discovery_redaction_and_recovery(office, monkeypatch):
    monkeypatch.setenv('OFFICE_MCP_TEST_HEADER', 'fixture-value')
    with socket.socket() as offline:
        offline.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{offline.getsockname()[1]}/mcp'
        connection = Connection(type='http', url=url, headers={'X-Test': '$OFFICE_MCP_TEST_HEADER'})
        row, = await change('sample', 'create', connection)
        assert (row['name'], row['status'], row['count']) == ('sample', 'failed', 0)
        assert row['error']
        assert row['headers'] == {'X-Test': '***'}
        assert 'fixture-value' not in str(row) and 'fixture-value' not in office.read_text()
        await change('sample', 'edit', connection.model_copy(update={'headers': {'X-Test': '***'}}))
        assert '$OFFICE_MCP_TEST_HEADER' in office.read_text()
    async with live_service() as replacement:
        with pytest.raises(ValueError, match='credential'):
            await change('sample', 'edit', replacement.model_copy(update={'headers': {'X-Test': '***'}}))
        await change('sample', 'edit', replacement)
        assert list_services()[0]['status'] == 'ready'


@pytest.mark.parametrize('fields', [
    {'type': 'stdio', 'command': 'invalid'},
    {'type': 'stdio', 'url': 'https://example.com'},
    {'type': 'http', 'url': 'file:///tmp/mcp'},
    {'type': 'http', 'url': 'http://user:password@localhost'},
    {'type': 'http', 'url': 'http://localhost', 'args': ['arg']},
])
def test_invalid_connection(fields):
    with pytest.raises(ValueError):
        Connection(**fields)

@pytest.mark.asyncio
async def test_changed_service_during_discovery_cannot_be_resurrected(office, monkeypatch):
    from deerflow.mcp import tools as loader

    started, release = asyncio.Event(), asyncio.Event()

    async def wait_for_tools(config, *, strict):
        started.set()
        await release.wait()
        return []

    monkeypatch.setattr(loader, 'get_mcp_tools', wait_for_tools)
    opening = asyncio.create_task(change('slow', 'create', Connection(type='http', url='http://localhost:9000/mcp')))
    async with asyncio.timeout(5):
        await started.wait()
        await change('slow', 'delete')
        release.set()
        assert await opening == []
    assert not registry.entries()


@pytest.mark.asyncio
async def test_missing_variable_and_missing_local_launcher(office, monkeypatch):
    monkeypatch.delenv('OFFICE_MISSING_TOKEN', raising=False)
    await change('remote', 'create', Connection(type='http', url='http://localhost:9000/mcp',
                                                headers={'Authorization': '$OFFICE_MISSING_TOKEN'}))
    assert list_services()[0]['error'] == 'Missing environment variable OFFICE_MISSING_TOKEN'
    monkeypatch.setenv('PATH', str(office.parent))
    await change('local', 'create', Connection(type='stdio', command='uvx', args=['sample']))
    row = next(r for r in list_services() if r['name'] == 'local')
    assert row['status'] == 'failed'
    assert row['count'] == 0


@pytest.mark.asyncio
async def test_http_auth_change_and_file_reset(office, monkeypatch, tool_runtime):
    from deerflow.sandbox.patch_tool import apply_patch_tool
    token = 'Bearer local-test-secret'
    monkeypatch.setenv('OFFICE_TEST_AUTH', token)
    async with live_service(token=token) as conn:
        await change('private_api', 'create', conn)
        row, = list_services()
        assert row['status'] == 'failed' and row['error'] == 'HTTP 401'
        grant = registry.entries()['private_api'].grant
        await change('private_api', 'edit', conn.model_copy(update={'headers': {'Authorization': '$OFFICE_TEST_AUTH'}}))
        assert list_services()[0]['count'] == 2
        assert registry.entries()['private_api'].grant == grant
        assert token not in office.read_text() and token not in str(list_services())
        # A manual file edit and reset must discover added services and drop removed ones.
        before = office.read_text(encoding='utf-8')
        raw = json.loads(before)
        raw['mcpServers']['renamed'] = raw['mcpServers'].pop('private_api')
        patch = update_patch(f'/mnt/user-data/workspace/{office.name}', before, json.dumps(raw))
        assert apply_patch_tool.func(tool_runtime[0], patch).status == 'success'
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.gateway.auth_middleware import AuthMiddleware
        from app.gateway.routers.mcp import router

        monkeypatch.setenv('DEER_FLOW_AUTH_DISABLED', '1')
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://localhost') as client:
            response = await client.post('/api/mcp/cache/reset')
            assert response.status_code == 200, response.text
            assert response.json()['success']
        assert set(registry.entries()) == {'renamed'}
        assert registry.entries()['renamed'].grant != grant
        assert [r['count'] for r in list_services()] == [2]
