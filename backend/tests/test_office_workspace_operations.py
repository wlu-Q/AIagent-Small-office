import json
from patch_helpers import add_patch, update_patch
from deerflow.sandbox.patch_tool import apply_patch_tool
from deerflow.sandbox import patch as patch_engine
import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware
from deerflow.office.execution_workspace import ExecutionWorkspaceService, _git
from deerflow.office.execution_facts import WorkIdentity
from deerflow.config.paths import Paths
from deerflow.sandbox import tools
from deerflow.sandbox.local.local_sandbox import LocalSandbox


@pytest.fixture
def environment(tmp_path, monkeypatch):
    _git(tmp_path, 'init')
    (tmp_path / '.gitignore').write_text('state/\n')
    _git(tmp_path, 'add', '.gitignore')
    _git(tmp_path, 'commit', '-m', 'baseline')
    paths = Paths(str(tmp_path / 'state'))
    monkeypatch.setattr('deerflow.office.execution_workspace.get_paths', lambda: paths)
    gate = ExecutionWorkspaceService(paths=paths, user_id='alice')
    sandboxes = {key: LocalSandbox(key) for key in ('a', 'b')}
    def sandbox(runtime):
        return sandboxes[runtime.context['run_id']]
    async def async_sandbox(runtime):
        return sandbox(runtime)
    monkeypatch.setattr(tools, 'ensure_sandbox_initialized', sandbox)
    monkeypatch.setattr(tools, 'ensure_sandbox_initialized_async', async_sandbox)
    monkeypatch.setattr(tools, 'is_local_sandbox', lambda _: True)
    monkeypatch.setattr(tools, 'is_host_bash_allowed', lambda: True)
    runtimes = {key: SimpleNamespace(context={'user_id': 'alice', 'run_id': key, 'thread_id': key, 'office_employee_id': key,
                    'office_work': {'work_id': 'work'}, 'office_project_path': str(tmp_path),
                    'abort_event': threading.Event()}, state={'thread_data': {
                        'workspace_path': str(tmp_path), 'uploads_path': str(tmp_path / 'uploads'),
                        'outputs_path': str(tmp_path / 'outputs')}, 'messages': []}) for key in sandboxes}
    for key, runtime in runtimes.items():
        work = WorkIdentity('alice', 'project', key, key)
        runtime.context['office_work'] = work.to_record()
        runtime.context['office_project_id'] = work.project_id
        runtime.context['office_execution_workspace'] = gate.allocate(work, employee_id=key, owner_key=key, project_path=tmp_path)
    return gate, sandboxes, runtimes


async def invoke(runtime, name, call_id, *, synchronous=False, **args):
    if 'path' in args:
        args['path'] = '/mnt/user-data/workspace/' + os.path.relpath(args['path'], runtime.context['office_project_path']).replace('\\', '/')
    if name == 'apply_patch':
        path = args.pop('path')
        content = args.pop('content', '')
        old = args.pop('old', None)
        args = {'patch': add_patch(path, content) if old is None else update_patch(path, old, content)}
    tool = {'apply_patch': apply_patch_tool,
            'read_file': tools.read_file_tool, 'bash': tools.bash_tool}[name]
    runtime.tool_call_id = call_id
    request = ToolCallRequest(tool_call={'name': name, 'id': call_id, 'args': (args if name == 'apply_patch' else {'description': 'test', **args})},
                              tool=tool, state=runtime.state, runtime=runtime)
    from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

    errors = ToolErrorHandlingMiddleware()

    def sync_handler(req):
        result = tool.func(runtime=runtime, **req.tool_call['args'])
        return result if isinstance(result, ToolMessage) else ToolMessage(content=result, tool_call_id=call_id, name=name)

    async def handler(req):
        result = await tool.coroutine(runtime=runtime, **req.tool_call['args'])
        return result if isinstance(result, ToolMessage) else ToolMessage(content=result, tool_call_id=call_id, name=name)
    async def handled(req):
        return await errors.awrap_tool_call(req, handler)

    def sync_handled(req):
        return errors.wrap_tool_call(req, sync_handler)

    middleware = ReadBeforeWriteMiddleware()
    result = (await asyncio.to_thread(middleware.wrap_tool_call, request, sync_handled) if synchronous
              else await middleware.awrap_tool_call(request, handled))
    runtime.state['messages'].append(result)
    return result


@pytest.mark.anyio
@pytest.mark.parametrize('synchronous', [False, True])
@pytest.mark.parametrize('content', ['Error: example log', '{"error": "connection failed"}', '2 failed; timed out; truncated'])
async def test_successful_read_content_remains_data_in_project_evidence(environment, tmp_path, synchronous, content):
    from langchain_core.messages import AIMessage

    from deerflow.config.paths import Paths
    from deerflow.office.project_knowledge import ProjectKnowledgeStore
    from deerflow.office.project_knowledge_service import ProjectKnowledgeService
    from deerflow.subagents.step_events import capture_step_message

    _, _, runtimes = environment
    path = tmp_path / 'example.log'
    path.write_text(content, encoding='utf-8')
    message = await invoke(runtimes['a'], 'read_file', 'read-evidence', path=str(path), synchronous=synchronous)
    assert message.status == 'success'
    assert message.additional_kwargs['deerflow_tool_meta']['status'] == 'success'
    steps, seen = [], set()
    capture_step_message(AIMessage(content='', tool_calls=[{'name': 'read_file', 'id': 'read-evidence', 'args': {'path': str(path)}}]), steps, seen)
    capture_step_message(message, steps, seen)
    service = ProjectKnowledgeService(ProjectKnowledgeStore(paths=Paths(tmp_path / 'state'), user_id='alice'))
    fact = service.capture_verification(project_id='p', user_id='alice', run_id='a', native_task_id='employee-task',
        topic='inspect-log', steps=steps, occurred_at=1)
    assert fact.evidence[0]['summary'] == content
    assert 'not a claim that the current project passes' in fact.statement
    assert service.context_for('p', 'alice', pm=True)['facts'][0]['verification_scope'] == 'historical'




@pytest.mark.anyio
async def test_different_files_really_enter_io_together(environment, tmp_path, monkeypatch):
    _, sandboxes, runtimes = environment
    barrier = threading.Barrier(2, timeout=5)
    original = patch_engine._write
    def write(*args, **kwargs):
        barrier.wait()
        original(*args, **kwargs)
    monkeypatch.setattr(patch_engine, '_write', write)
    results = await asyncio.gather(*(invoke(runtimes[key], 'apply_patch', key, path=str(tmp_path / key), content=key) for key in runtimes))
    assert all(json.loads(r.content)['status'] == 'success' for r in results)
    assert [Path(tools.get_thread_data(runtimes[key])['workspace_path']).joinpath(key).read_text() for key in runtimes] == ['a\n', 'b\n']
    assert not (tmp_path / 'a').exists() and not (tmp_path / 'b').exists()


@pytest.mark.anyio
async def test_creation_and_inspection_fail_closed(environment, tmp_path):
    _, sandboxes, runtimes = environment
    path = str(tmp_path / 'new')
    results = await asyncio.gather(*(invoke(runtime, 'apply_patch', key, path=path, content=key) for key, runtime in runtimes.items()))
    assert sum(json.loads(r.content)['status'] == 'success' for r in results) == 2
    sandboxes['a'].read_file = lambda _: (_ for _ in ()).throw(PermissionError('unreadable'))
    result = await invoke(runtimes['a'], 'apply_patch', 'fail', path=path, content='overwrite')
    assert result.status == 'error' and 'exists' in result.content


@pytest.mark.anyio
async def test_invalid_write_reference_is_recoverable_tool_error(environment, tmp_path):
    svc, _, runtimes = environment
    runtime = runtimes['a']
    wid = runtime.context['office_execution_workspace']['workspace_id']
    request = ToolCallRequest(tool_call={'name': 'apply_patch', 'id': 'bad-path', 'args': {
        'patch': add_patch(f'/mnt/office-workspaces/{wid}/workspace/new.txt', 'bad')}},
        tool=apply_patch_tool, state=runtime.state, runtime=runtime)
    runtime.tool_call_id = 'bad-path'
    async def handler(req):
        return await apply_patch_tool.coroutine(runtime=runtime, **req.tool_call['args'])
    result = await ReadBeforeWriteMiddleware().awrap_tool_call(request, handler)
    assert result.status == 'error' and 'restricted' in result.content
    assert result.additional_kwargs['deerflow_tool_meta']['status'] == 'error'
    assert not Path(svc.get(wid)['root_path']).joinpath('new.txt').exists()
    corrected = await invoke(runtime, 'apply_patch', 'corrected-path', path=str(tmp_path / 'new.txt'), content='ok')
    assert corrected.status == 'success'
    assert not (tmp_path / 'new.txt').exists()


@pytest.mark.anyio
async def test_cancel_waits_for_real_io_before_release(environment, tmp_path, monkeypatch):
    gate, sandboxes, runtimes = environment
    entered, finish = threading.Event(), threading.Event()
    original = patch_engine._write
    def write(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        original(*args, **kwargs)
    monkeypatch.setattr(patch_engine, '_write', write)
    task = asyncio.create_task(invoke(runtimes['a'], 'apply_patch', 'write', path=str(tmp_path / 'a'), content='a'))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task








@pytest.mark.anyio
async def test_office_read_middleware_does_not_duplicate_tool_authorization(environment):
    from unittest.mock import AsyncMock
    _, _, runtimes = environment
    request = ToolCallRequest(tool_call={'name': 'configured_mcp_tool', 'id': 'external', 'args': {}},
        tool=SimpleNamespace(metadata={'deerflow_mcp': True}), state=runtimes['a'].state, runtime=runtimes['a'])
    handler = AsyncMock(return_value=ToolMessage(content='result', tool_call_id='external'))
    result = await ReadBeforeWriteMiddleware().awrap_tool_call(request, handler)
    assert result.content == 'result'
    handler.assert_awaited_once_with(request)


@pytest.mark.anyio
async def test_read_and_write_stay_in_workspace_after_bound_project_changes(environment, tmp_path):
    gate, sandboxes, runtimes = environment
    path = tmp_path / 'readable'
    path.write_text('observed')
    gate.prepare(runtimes['a'].context['office_execution_workspace']['workspace_id'],
                 work=WorkIdentity.from_record(runtimes['a'].context['office_work']))
    original = sandboxes['a'].read_file
    def read(target):
        content = original(target)
        path.write_text('newer')
        return content
    sandboxes['a'].read_file = read
    result = await asyncio.wait_for(invoke(runtimes['a'], 'read_file', 'read', path=str(path)), 2)
    assert result.content == 'observed'
    result = await invoke(runtimes['a'], 'apply_patch', 'workspace-write', path=str(path), old='observed', content='updated')
    assert result.status == 'success' and path.read_text() == 'newer'
    root = Path(tools.get_thread_data(runtimes['a'])['workspace_path'])
    assert (root / 'readable').read_text() == 'updated'


@pytest.mark.anyio
@pytest.mark.parametrize('synchronous', [False, True])
async def test_error_prefix_is_file_data_with_version_protection(environment, tmp_path, synchronous):
    _, _, runtimes = environment
    path = tmp_path / 'errors.txt'
    path.write_text('Error: old failure\n', encoding='utf-8')
    a, b = runtimes.values()
    for runtime in (a, b):
        read = await invoke(runtime, 'read_file', 'read-errors', path=str(path), synchronous=synchronous)
        assert read.content == 'Error: old failure\n'
        assert read.additional_kwargs['deerflow_tool_meta']['status'] == 'success'
        assert 'deerflow_read_mark' not in read.additional_kwargs
    changed = await invoke(a, 'apply_patch', 'change-errors', path=str(path), synchronous=synchronous,
                           old='Error: old failure\n', content='Error: new failure\n')
    assert changed.status == 'success'
    assert Path(tools.get_thread_data(a)['workspace_path']).joinpath('errors.txt').read_text() == 'Error: new failure\n'
    stale = await invoke(a, 'apply_patch', 'stale-errors', path=str(path), content='overwrite', synchronous=synchronous)
    assert stale.status == 'error' and 'exists' in stale.content
    assert Path(tools.get_thread_data(a)['workspace_path']).joinpath('errors.txt').read_text() == 'Error: new failure\n'


@pytest.mark.anyio
@pytest.mark.parametrize('synchronous', [False, True])
async def test_failed_read_never_authorizes_office_write(environment, tmp_path, synchronous):
    _, sandboxes, runtimes = environment
    path = tmp_path / 'denied.txt'
    path.write_text('keep', encoding='utf-8')
    original = sandboxes['a'].read_file
    sandboxes['a'].read_file = lambda _: (_ for _ in ()).throw(PermissionError('unreadable'))
    read = await invoke(runtimes['a'], 'read_file', 'failed-read', path=str(path), synchronous=synchronous)
    assert 'deerflow_read_mark' not in read.additional_kwargs
    assert read.additional_kwargs['deerflow_tool_meta']['status'] == 'error'
    blocked = await invoke(runtimes['a'], 'apply_patch', 'unreadable-write', path=str(path), content='overwrite', synchronous=synchronous)
    assert blocked.status == 'error' and 'exists' in blocked.content
    sandboxes['a'].read_file = original
    blocked = await invoke(runtimes['a'], 'apply_patch', 'unread-write', path=str(path), content='overwrite', synchronous=synchronous)
    assert blocked.status == 'error' and path.read_text() == 'keep'


@pytest.mark.anyio
@pytest.mark.parametrize('synchronous', [False, True])
async def test_office_todos_use_native_state_without_project_write_permission(environment, tmp_path, synchronous):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    from deerflow.agents.middlewares.todo_middleware import TodoMiddleware

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, *args, **kwargs):
            return self

    gate, _, _ = environment
    todos = [{'content': 'Review the request', 'status': 'completed'}]
    model = Model(responses=[AIMessage(content='', tool_calls=[{'name': 'write_todos', 'args': {'todos': todos}, 'id': 'todo-call'}]), AIMessage(content='done')])
    graph = create_agent(model=model, tools=[], middleware=[TodoMiddleware(), ReadBeforeWriteMiddleware()])
    # State-only work needs neither a project binding nor a project write permit.
    args = {'messages': [{'role': 'user', 'content': 'record the completed todo'}]}
    context = {'office_work': {'work_id': 'test'}}
    result = await asyncio.wait_for(
        asyncio.to_thread(graph.invoke, args, context=context) if synchronous else graph.ainvoke(args, context=context), 5)
    assert result.get('todos') == todos
    messages = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert len(messages) == 1 and messages[0].status == 'success'


@pytest.mark.anyio
@pytest.mark.parametrize('direct', [True, False])
async def test_repeated_edits_need_no_read_receipt(environment, tmp_path, direct):
    _, _, runtimes = environment
    runtime = runtimes['a']
    if direct:
        runtime.context.pop('office_execution_workspace')
    path = str(tmp_path / 'new.txt')
    assert (await invoke(runtime, 'apply_patch', 'create', path=path, content='one')).status == 'success'
    assert (await invoke(runtime, 'apply_patch', 'first-edit', path=path, old='one', content='two')).status == 'success'
    assert (await invoke(runtime, 'apply_patch', 'second-edit', path=path, old='two', content='three')).status == 'success'
    actual = Path(tools.get_thread_data(runtime)['workspace_path']) / 'new.txt'
    assert actual.read_text() == 'three\n'
    assert (tmp_path / 'new.txt').exists() == direct


@pytest.mark.anyio
@pytest.mark.parametrize('relative', ['.git/config', '.office-worktrees/other/tree/secret.txt', '../outside.txt'])
async def test_direct_project_patch_preserves_path_boundary(environment, tmp_path, relative):
    _, _, runtimes = environment
    runtime = runtimes['a']
    runtime.context.pop('office_execution_workspace')
    result = await invoke(runtime, 'apply_patch', 'bad-write', path=str(tmp_path / relative), content='bad')
    assert result.status == 'error'
