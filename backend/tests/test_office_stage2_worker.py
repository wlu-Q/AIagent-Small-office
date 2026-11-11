"""Real Gateway/Worker/Agent graph/task tool; deterministic model and native executor boundary."""
import asyncio
import html
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from test_office_workflow_integration import runtime as runtime_fixture
from test_task_tool_core_logic import _make_result

from app.gateway import services
from app.gateway.routers.office import _start_office_run
from deerflow.agents.lead_agent import agent
from deerflow.config.agents_config import AgentConfig
from deerflow.config.app_config import AppConfig
from deerflow.office.run_gate import install_process_employee_run_gate
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import RunContext
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.executor import SubagentStatus


@pytest.mark.anyio
async def test_stage_free_task_runs_through_native_gateway_worker_and_checkpoint(tmp_path, monkeypatch):
    rt = runtime_fixture.__wrapped__(tmp_path, monkeypatch)
    cfg = AppConfig.model_validate({'models': [{'name': 'profile-primary', 'model': 'controlled', 'use': 'unused'}],
        'tools': [{'name': 'read_file', 'use': 'deerflow.sandbox.tools:read_file_tool', 'group': 'file:read'}],
        'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'}, 'memory': {'enabled': False},
        'title': {'enabled': False}, 'summarization': {'enabled': False}, 'tool_search': {'enabled': False}})
    manager, checkpoint, events = RunManager(), InMemorySaver(), MemoryRunEventStore()
    rt.projection.checkpointer = checkpoint
    rt.pipeline.run_launcher.checkpointer = checkpoint
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create('office__user-a__pm__group', assistant_id='pm', user_id='user-a')
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id='user-a'), auth_source='internal'), headers={}, cookies={})
    task_module = importlib.import_module('deerflow.tools.builtins.task_tool')
    assignments = [('review', 'reviewer', '先审查方案'), ('docs', 'writer', '再写介绍文档')]
    async def registry():
        return [{'employee_id': employee, 'enabled': True, 'can_delegate': False} for _, employee, _ in assignments]
    rt.pipeline.run_launcher._employee_registry_snapshot = registry
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self
    records, results, launched = [], {}, []
    class Executor:
        def __init__(self, **kwargs):
            self.employee = kwargs['config'].name
        def execute_async(self, prompt, task_id=None):
            approved = json.loads(html.unescape(prompt.split('<work_data>', 1)[1].split('</work_data>', 1)[0]))
            item = next(t for t in assignments if t[2] == approved['task'])
            assert approved['background']['goal'] == '先审查，再写文档'
            assert self.employee == item[1]
            launched.append(item[0])
            report, steps = '完成', []
            result = _make_result(SubagentStatus.COMPLETED, result=report, ai_messages=steps)
            results[task_id] = result
            return task_id
    monkeypatch.setattr(task_module, 'SubagentExecutor', Executor)
    monkeypatch.setattr(task_module, 'get_background_task_result', lambda tid: results[tid])
    monkeypatch.setattr(task_module, 'cleanup_background_task', lambda tid: None)
    monkeypatch.setattr(task_module, 'update_employee_runtime_state', lambda *a, **k: None)
    from deerflow.office.tool_contract import ResolvedEmployeeTools
    monkeypatch.setattr(task_module, 'resolve_employee_subagent_package', lambda employee, *args: SimpleNamespace(
        employee_id=employee, config=SubagentConfig(name=employee, description='employee', system_prompt='完成当前任务', skills=[], model='profile-primary'),
        tool_groups=(), allowed_configured_tool_names=(), allowed_external_tool_names=(),
        tool_contract=ResolvedEmployeeTools(
            revision='test', authorized_ids=('report_to_pm',), available_ids=('report_to_pm',),
            unavailable_reasons={}, runtime_names=('report_to_pm',), derived_groups=(),
        )))
    monkeypatch.setattr(agent, 'is_office_managed_agent', lambda *a, **k: True)
    monkeypatch.setattr(agent, 'load_agent_config', lambda _: AgentConfig(name='pm', skills=[], runtime_contract={'authority': {'can_delegate': True}}))
    monkeypatch.setattr(agent, 'load_agent_soul', lambda _: 'PM')
    monkeypatch.setattr(agent, '_load_enabled_employee_skills', lambda *a, **k: [])
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **kw: [task_module.task_tool] if kw.get('subagent_enabled') else [])
    install_process_employee_run_gate(rt.gate)
    async def start(body, thread_id, req):
        assert 'office_workflow' not in body.context
        replies = [AIMessage(content='', id='native-ai', tool_calls=[{'name': 'task', 'id': task_id, 'args': {
            'description': prompt, 'prompt': prompt, 'subagent_type': employee}} for task_id, employee, prompt in assignments]),
            AIMessage(content='本轮员工工作完成')]
        model = Model(responses=replies)
        monkeypatch.setattr(agent, 'create_chat_model', lambda **kwargs: model)
        record = await _start_office_run(body, thread_id, req)
        records.append(record)
        return record
    rt.pipeline.run_view.find_message_run = AsyncMock(side_effect=lambda employee, kind, mid:
        next((r for r in records if r.metadata.get('office_message_id') == mid), None))
    run_ctx = RunContext(checkpointer=checkpoint, event_store=events, thread_store=thread_store, app_config=cfg,
        on_run_started=rt.projection.handle_run_started,
        on_run_input_checkpointed=rt.projection.handle_run_input_checkpointed,
        on_run_completed=rt.projection.handle_run_completed)
    monkeypatch.setattr(services, 'get_run_manager', lambda _: manager)
    monkeypatch.setattr(services, 'get_run_context', lambda _: run_ctx)
    monkeypatch.setattr(services, 'get_stream_bridge', lambda _: SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock()))
    from deerflow.config.paths import get_paths
    from deerflow.office.execution_facts import DelegationIdentity, ExecutionReceiptStore, WorkIdentity
    from deerflow.office.run_gate import QueuedOfficeRun
    from deerflow.runtime.runs.execution import RunExecutionSpecV1
    try:
        admission = await rt.gate.admit(QueuedOfficeRun('work', 'pm', 'direct', '先审查，再写文档', 1, conversation_kind='group'))
        record = await rt.pipeline.run_launcher.launch('pm', 'profile-primary',
            RunExecutionSpecV1(version=1, mode='new', input={'messages': [{'role': 'user', 'content': '先审查，再写文档'}]}),
            start_run=start, request=request, thread_id='office__user-a__pm__group',
            office_lease_id=admission.lease_id, project_binding=rt.project.get_binding(),
            message_metadata={'office_conversation_kind': 'group'})
        async with asyncio.timeout(10):
            while record.status in {RunStatus.pending, RunStatus.running}:
                await asyncio.sleep(.02)
        assert record.status == RunStatus.success, record.last_ai_message
        assert set(launched) == {'review', 'docs'} and len(launched) == 2
        assert len(records) == 1
        work = WorkIdentity.from_record(record.kwargs['launch_contract']['trusted_context']['office_work'])
        receipts = ExecutionReceiptStore(paths=get_paths(), user_id='user-a')
        for task_id, _, _ in assignments:
            receipt = receipts.get(DelegationIdentity(work, 'native-ai', task_id))
            assert receipt.status == 'succeeded'
    finally:
        await rt.pipeline.shutdown()
        await manager.shutdown()
