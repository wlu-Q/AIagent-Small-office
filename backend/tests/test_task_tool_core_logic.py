# 本文件属于 DeerFlow task 工具回归测试模块：只验证 Subagent 委派、事件、取消和 Office 许可接缝。
# 不负责 PM 群聊路由、员工能力画像、公共任务系统或 Subagent 内部 Agent Loop 质量。
# 关联：tools/builtins/task_tool.py 是被测原生工具，SubagentExecutor 与 Office Gate 提供执行边界。
"""Core behavior tests for task tool orchestration."""

import asyncio
import importlib
import inspect
import json
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.status_contract import (
    SUBAGENT_ERROR_KEY,
    SUBAGENT_MODEL_NAME_KEY,
    SUBAGENT_RESULT_BRIEF_KEY,
    SUBAGENT_RESULT_SHA256_KEY,
    SUBAGENT_STATUS_KEY,
    SUBAGENT_STOP_REASON_KEY,
    SUBAGENT_TOKEN_USAGE_KEY,
)

# Use module import so tests can patch the exact symbols referenced inside task_tool().
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


def _test_tool_contract(*runtime_names):
    from deerflow.office.tool_contract import ResolvedEmployeeTools

    names = tuple(runtime_names)
    return ResolvedEmployeeTools(
        revision="test-contract",
        authorized_ids=names,
        available_ids=names,
        unavailable_reasons={},
        runtime_names=names,
        derived_groups=(),
    )


@pytest.fixture(autouse=True)
def isolated_workflow_paths(tmp_path, monkeypatch):
    from deerflow.config import paths

    monkeypatch.setattr(paths, '_paths', paths.Paths(tmp_path / 'workflow-state'))

    from langchain_core.messages import AIMessage
    original = task_tool_module.task_tool.coroutine
    async def persisted_call(**kwargs):
        runtime = kwargs['runtime']
        if runtime is not None and runtime.context.get('office_work'):
            call_id = kwargs['tool_call_id']
            args = {key: kwargs[key] for key in ('description', 'prompt', 'subagent_type')}
            if not any(isinstance(m, AIMessage) and any(c['id'] == call_id for c in m.tool_calls) for m in runtime.state.get('messages', [])):
                runtime.state.setdefault('messages', []).append(AIMessage(content='', id='ai-' + call_id, tool_calls=[{'id': call_id, 'name': 'task', 'args': args}]))
        return await original(**kwargs)
    monkeypatch.setattr(task_tool_module.task_tool, 'coroutine', persisted_call)


def _make_approved_office_runtime(*, metadata):
    """Native Office work, deliberately without a Workflow or confirmed task plan."""
    from deerflow.office.execution_facts import WorkIdentity
    from deerflow.office.work_context import build_work_context
    metadata = {**metadata, 'office_employee_id': 'pm'}
    runtime = _make_runtime(metadata=metadata)
    work = WorkIdentity('default', 'project-one', runtime.context['thread_id'], 'work')
    runtime.context.update(user_id='default', office_project_id='project-one', office_work=work.to_record(),
                           office_work_context=build_work_context(**work.to_record(), latest_user_message='User goal'))
    return runtime


class FakeSubagentStatus(Enum):
    # Match production enum values so branch comparisons behave identically.
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


def _make_runtime(*, app_config=None, metadata=None) -> SimpleNamespace:
    # Minimal ToolRuntime-like object; task_tool only reads these three attributes.
    context = {"thread_id": "thread-1"}
    if app_config is not None:
        context["app_config"] = app_config
    return SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context=context,
        config={
            "metadata": {
                "model_name": "ark-model",
                "trace_id": "trace-1",
                **(metadata or {}),
            }
        },
    )


def _make_subagent_config(name: str = "general-purpose") -> SubagentConfig:
    return SubagentConfig(
        name=name,
        description="General helper",
        system_prompt="Base system prompt",
        max_turns=50,
        timeout_seconds=10,
    )


def _make_result(
    status: FakeSubagentStatus,
    *,
    ai_messages: list[dict] | None = None,
    result: str | None = None,
    error: str | None = None,
    stop_reason: str | None = None,
    token_usage_records: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        ai_messages=ai_messages or [],
        result=result,
        error=error,
        stop_reason=stop_reason,
        token_usage_records=token_usage_records or [],
        usage_reported=False,
    )


def _run_task_tool(**kwargs) -> str | Command:
    """Execute the task tool across LangChain sync/async wrapper variants."""
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


def _task_tool_message(result: str | Command) -> ToolMessage:
    assert isinstance(result, Command)
    assert isinstance(result.update, dict)
    messages = result.update["messages"]
    assert len(messages) == 1
    message = messages[0]
    assert isinstance(message, ToolMessage)
    return message


def _patch_immediate_office_gates(monkeypatch) -> None:
    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='office-test-lease')

        async def wait_until_started(self, _queue_id):
            raise AssertionError('immediate gate must not wait')

        async def release(self, _lease_id):
            return True

    class ProjectGate:
        async def acquire(self, _project_id, _lease_id, *, write_required, parallel=False):
            return SimpleNamespace(write_required=write_required)

        async def release(self, _lease_id):
            return True

    monkeypatch.setattr(
        task_tool_module,
        '_office_employee_gate',
        lambda: EmployeeGate(),
    )


def _office_employee_package(employee_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        employee_id=employee_id,
        config=SubagentConfig(
            name=employee_id,
            description='Office employee',
            system_prompt='EMPLOYEE_PROMPT_ONLY',
            skills=[],
            model='employee-model',
            max_turns=10,
            timeout_seconds=30,
        ),
        tool_groups=('file:read',),
        allowed_configured_tool_names=('read_file',),
        allowed_external_tool_names=(),
        tool_contract=_test_tool_contract('read_file', 'report_to_pm'),
    )


@pytest.mark.parametrize('enabled,selected', [(True, True), (False, True), (True, False)])
def test_office_task_loads_only_authorized_memory_tools(monkeypatch, enabled, selected):
    from deerflow.config import get_app_config
    app = get_app_config().model_copy(deep=True)
    app.memory = app.memory.model_copy(update={'enabled': enabled, 'mode': 'tool'})
    employee_id = 'memory-worker'
    package = _office_employee_package(employee_id)
    package.tool_contract = _test_tool_contract('report_to_pm', *(('memory_search',) if selected else ()))
    runtime = _make_approved_office_runtime(metadata={'office_employee_registry_snapshot': [{'employee_id': employee_id, 'enabled': True}]})
    runtime.context['app_config'] = app
    runtime.context['run_id'] = 'memory-parent-run'
    captured = {}
    class Executor:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def execute_async(self, prompt, task_id=None):
            return task_id
    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *args: package)
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', Executor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, 'get_background_task_result', lambda _: _make_result(FakeSubagentStatus.COMPLETED, result='done'))
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _: None)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', lambda _: None)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **kwargs: [])
    _patch_immediate_office_gates(monkeypatch)
    output = _run_task_tool(runtime=runtime, description='memory', prompt='recall', subagent_type=employee_id, tool_call_id='memory-call')
    assert captured, _task_tool_message(output).content
    names = {t.name for t in captured['tools']}
    assert ('memory_search' in names) == (enabled and selected)
    assert names.isdisjoint({'memory_add', 'memory_update', 'memory_delete', 'ask_clarification'})
    assert captured['office_context']['office_tool_contract'] == package.tool_contract.snapshot()


def test_office_task_corrects_group_name_without_switching_employee(monkeypatch):
    from langchain_core.messages import AIMessage
    from deerflow.sandbox.patch_tool import apply_patch_tool
    from deerflow.sandbox.tools import read_file_tool, ls_tool, grep_tool

    employee_id = 'document-a'
    package = _office_employee_package(employee_id)
    package.tool_contract = _test_tool_contract('read_file', 'ls', 'grep', 'apply_patch', 'report_to_pm')
    package.allowed_configured_tool_names = ('read_file', 'ls', 'grep', 'apply_patch')
    runtime = _make_approved_office_runtime(metadata={'office_employee_registry_snapshot': [{'employee_id': employee_id, 'enabled': True}]})
    runtime.context['run_id'] = 'document-parent-run'
    calls = []

    class Executor:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def execute_async(self, prompt, task_id=None):
            return task_id

    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *args: package)
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', Executor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, 'get_background_task_result', lambda _: _make_result(FakeSubagentStatus.COMPLETED, result='done'))
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _: None)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', lambda _: None)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **kwargs: [read_file_tool, ls_tool, grep_tool, apply_patch_tool])
    _patch_immediate_office_gates(monkeypatch)
    for call_id, required in [('invalid', 'file:write'), ('corrected', 'apply_patch')]:
        args = dict(description='Write product overview', prompt='Write PRODUCT_OVERVIEW.md',
                    subagent_type=employee_id, required_tools=['read_file', 'ls', 'grep', required])
        runtime.state.setdefault('messages', []).append(AIMessage(content='', id=f'ai-{call_id}', tool_calls=[dict(id=call_id, name='task', args=args)]))
        output = _run_task_tool(runtime=runtime, tool_call_id=call_id, **args)
        message = _task_tool_message(output)
        if call_id == 'invalid':
            assert 'Invalid required_tools: file:write' in message.content
            assert calls == []
        else:
            assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == 'completed', message.content
    assert len(calls) == 1
    assert calls[0]['config'].name == employee_id
    assert {tool.name for tool in calls[0]['tools']} == set(package.tool_contract.runtime_names)


def test_task_result_command_derives_content_from_status_payload():
    signature = inspect.signature(task_tool_module._task_result_command)
    assert "content" not in signature.parameters

    completed = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-completed",
            status="completed",
            result="done",
        )
    )
    assert completed.content == "Task execution ended. Result: done"
    assert completed.additional_kwargs[SUBAGENT_STATUS_KEY] == "completed"
    assert completed.additional_kwargs[SUBAGENT_RESULT_BRIEF_KEY] == "done"

    completed_with_runtime_metadata = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-completed-metadata",
            status="completed",
            result="done",
            model_name="claude-3-7-sonnet",
            usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        )
    )
    assert completed_with_runtime_metadata.additional_kwargs[SUBAGENT_MODEL_NAME_KEY] == "claude-3-7-sonnet"
    assert completed_with_runtime_metadata.additional_kwargs[SUBAGENT_TOKEN_USAGE_KEY]["total_tokens"] == 120

    failed = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-failed",
            status="failed",
            error="boom",
        )
    )
    assert failed.content == "Task failed. Error: boom"
    assert failed.additional_kwargs[SUBAGENT_STATUS_KEY] == "failed"
    assert failed.additional_kwargs[SUBAGENT_ERROR_KEY] == "boom"

    failed_without_detail = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-failed-empty",
            status="failed",
            error=None,
        )
    )
    assert failed_without_detail.content == "Task failed."
    assert failed_without_detail.additional_kwargs[SUBAGENT_STATUS_KEY] == "failed"
    assert failed_without_detail.additional_kwargs[SUBAGENT_ERROR_KEY] == "Task failed."

    cancelled = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-cancelled",
            status="cancelled",
            error=None,
        )
    )
    assert cancelled.content == "Task cancelled by user."
    assert cancelled.additional_kwargs[SUBAGENT_STATUS_KEY] == "cancelled"
    assert cancelled.additional_kwargs[SUBAGENT_ERROR_KEY] == "Task cancelled by user."

    timed_out_without_detail = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-timeout-empty",
            status="timed_out",
            error="",
        )
    )
    assert timed_out_without_detail.content == "Task timed out."
    assert timed_out_without_detail.additional_kwargs[SUBAGENT_STATUS_KEY] == "timed_out"
    assert timed_out_without_detail.additional_kwargs[SUBAGENT_ERROR_KEY] == "Task timed out."

    # #3875 Phase 2: a capped run keeps a normal status and carries the cap on
    # the additive ``subagent_stop_reason`` field; the model-visible text folds
    # a ``(capped: ...)`` note in. The recovered partial work still travels on
    # ``result_brief`` like a clean success.
    capped = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-capped",
            status="completed",
            result="investigated 3 of 5 sources",
            stop_reason="token_capped",
        )
    )
    assert capped.content == "Task execution ended (capped: token budget). Result: investigated 3 of 5 sources"
    assert capped.additional_kwargs[SUBAGENT_STATUS_KEY] == "completed"
    assert capped.additional_kwargs[SUBAGENT_RESULT_BRIEF_KEY] == "investigated 3 of 5 sources"
    assert len(capped.additional_kwargs[SUBAGENT_RESULT_SHA256_KEY]) == 64
    assert capped.additional_kwargs[SUBAGENT_STOP_REASON_KEY] == "token_capped"


def test_task_result_command_carries_loop_capped_from_real_loop_detection():
    """Real-path (#3875 Phase 2, ggnnggez review): drive the actual
    ``LoopDetectionMiddleware`` to a hard stop with repeated identical tool
    calls, feed the produced ``loop_capped`` through ``_task_result_command``,
    and assert the final task ``ToolMessage`` carries
    ``subagent_stop_reason=loop_capped`` — proving the loop cap reaches the wire
    the lead/ledger read, not just the in-memory result."""
    from langchain_core.messages import AIMessage

    from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware

    # Drive the real middleware to a hard stop (4 identical calls, hard_limit=4).
    mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
    runtime = SimpleNamespace(context={"thread_id": "t", "run_id": "r1"})
    tool_calls = [{"name": "bash", "args": {"command": "ls"}, "id": "c1", "type": "tool_call"}]
    for _ in range(3):
        mw._apply({"messages": [AIMessage(content="", tool_calls=tool_calls)]}, runtime)
    hard_stop = mw._apply({"messages": [AIMessage(content="", tool_calls=tool_calls)]}, runtime)
    assert hard_stop is not None  # hard stop fired

    stop_reason = mw.consume_stop_reason("r1")
    assert stop_reason == "loop_capped"

    # The produced reason flows through the task-tool result path onto the wire.
    message = _task_tool_message(
        task_tool_module._task_result_command(
            tool_call_id="tc-loop",
            status="completed",
            result="partial work before the loop was broken",
            stop_reason=stop_reason,
        )
    )
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "completed"
    assert message.additional_kwargs[SUBAGENT_STOP_REASON_KEY] == "loop_capped"
    assert message.additional_kwargs[SUBAGENT_RESULT_BRIEF_KEY] == "partial work before the loop was broken"
    assert "capped: repeated tool-call loop" in message.content


async def _no_sleep(_: float) -> None:
    return None


class _DummyScheduledTask:
    def add_done_callback(self, _callback):
        return None


def test_task_tool_returns_error_for_unknown_subagent(monkeypatch):
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: None)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda: ["general-purpose"])

    result = _run_task_tool(
        runtime=None,
        description="执行任务",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-1",
    )

    message = _task_tool_message(result)
    assert message.content == "Task failed. Error: Unknown subagent type 'general-purpose'. Available: general-purpose"
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "failed"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == "Unknown subagent type 'general-purpose'. Available: general-purpose"


def test_office_parent_cannot_fall_back_to_builtin_subagent(monkeypatch):
    monkeypatch.setattr('deerflow.office.delegation.lookup_delegation', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        task_tool_module,
        "resolve_employee_subagent_package",
        MagicMock(side_effect=FileNotFoundError("missing employee")),
    )
    get_config = MagicMock(side_effect=AssertionError("must not use builtin subagent"))
    monkeypatch.setattr(task_tool_module, "get_subagent_config", get_config)

    result = _run_task_tool(
        runtime=_make_runtime(metadata={"office_employee_id": "pm-r2", "office_employee_registry_snapshot": []}),
        description="执行任务",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-office-missing",
    )

    message = _task_tool_message(result)
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "failed"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == (
        "missing employee"
    )
    get_config.assert_not_called()


def test_office_parent_checks_current_employee_existence(monkeypatch):
    monkeypatch.setattr('deerflow.office.delegation.lookup_delegation', lambda *args, **kwargs: None)
    resolver = MagicMock(side_effect=FileNotFoundError('Employee not found: other-worker'))
    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', resolver)

    result = _run_task_tool(
        runtime=_make_runtime(
            metadata={
                'office_employee_id': 'pm-r2',
                'office_employee_registry_snapshot': [
                    {'employee_id': 'allowed-worker', 'enabled': True}
                ],
            }
        ),
        description='执行任务',
        prompt='do work',
        subagent_type='other-worker',
        tool_call_id='tc-office-outside-snapshot',
    )

    message = _task_tool_message(result)
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == 'failed'
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == (
        'Employee not found: other-worker'
    )
    resolver.assert_called_once()


def test_office_task_resolves_unique_display_name_to_snapshot_employee_id():
    resolve_target = getattr(task_tool_module, "_resolve_office_employee_target", None)

    assert resolve_target is not None
    assert resolve_target(
        {
            "office_employee_registry_snapshot": [
                {
                    "employee_id": "zhouli",
                    "display_name": "周礼",
                    "enabled": True,
                }
            ]
        },
        "周礼",
    ) == "zhouli"


def test_office_parent_checks_current_employee_enabled_state(monkeypatch):
    monkeypatch.setattr('deerflow.office.delegation.lookup_delegation', lambda *args, **kwargs: None)
    resolver = MagicMock(side_effect=RuntimeError('Office employee is disabled: disabled-worker'))
    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', resolver)

    result = _run_task_tool(
        runtime=_make_runtime(
            metadata={
                'office_employee_id': 'pm-r2',
                'office_employee_registry_snapshot': [
                    {'employee_id': 'disabled-worker', 'enabled': False}
                ],
            }
        ),
        description='执行任务',
        prompt='do work',
        subagent_type='disabled-worker',
        tool_call_id='tc-office-disabled',
    )

    message = _task_tool_message(result)
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == 'failed'
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == (
        'Office employee is disabled: disabled-worker'
    )
    resolver.assert_called_once()


def test_non_office_parent_cannot_delegate_office_employee(monkeypatch):
    employee_id = 'employee-office-only'
    runtime = _make_runtime(metadata={})
    executor_started = []
    gate_loader = MagicMock(
        side_effect=AssertionError('rejected delegation must not acquire Office gates')
    )

    class DummyExecutor:
        def __init__(self, **_kwargs):
            executor_started.append(True)

        def execute_async(self, _prompt, task_id=None):
            return task_id

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(FakeSubagentStatus.COMPLETED, result='unexpected'),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _payload: None)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', MagicMock())
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', _no_sleep)
    monkeypatch.setattr(task_tool_module, '_office_employee_gate', gate_loader)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])

    result = _run_task_tool(
        runtime=runtime,
        description='越权委派检查',
        prompt='执行办公室员工任务',
        subagent_type=employee_id,
        tool_call_id='tc-non-office-parent',
    )

    message = _task_tool_message(result)
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == 'failed'
    assert 'Office PM run' in str(message.additional_kwargs[SUBAGENT_ERROR_KEY])
    assert executor_started == []
    gate_loader.assert_not_called()


@pytest.mark.parametrize('stop_reason', [None, 'token_capped', 'turn_capped', 'loop_capped'])
@pytest.mark.parametrize('status', [FakeSubagentStatus.COMPLETED, FakeSubagentStatus.FAILED])
def test_office_task_uses_employee_package_without_parent_leakage(monkeypatch, stop_reason, status):
    memory = MagicMock()
    monkeypatch.setattr('deerflow.office.delegation_memory.get_memory_manager', lambda: memory)
    employee_id = 'employee-owned-package'
    employee_config = SubagentConfig(
        name=employee_id,
        description='Employee package',
        system_prompt='EMPLOYEE_PROMPT_ONLY',
        skills=['employee-skill'],
        model='employee-model',
        max_turns=17,
        timeout_seconds=30,
    )
    employee_package = SimpleNamespace(
        employee_id=employee_id,
        config=employee_config,
        tool_groups=('employee-tools',),
        allowed_configured_tool_names=('read_file', 'employee_mcp'),
        allowed_external_tool_names=('employee_mcp',),
        tool_contract=_test_tool_contract('employee-tool', 'report_to_pm'),
    )
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm-r2',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
            'available_skills': ['parent-skill'],
            'tool_groups': ['parent-tools'],
        }
    )
    runtime.context['run_id'] = 'run-pm-r2'
    events = []
    captured = {}
    tool_loader = MagicMock(return_value=['employee-tool'])
    resolver = MagicMock(return_value=employee_package)

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute_async(self, prompt, task_id=None):
            captured['prompt'] = prompt
            return task_id

    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', resolver)
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(status, result='employee done', stop_reason=stop_reason),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: events.append)
    _patch_immediate_office_gates(monkeypatch)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', MagicMock())
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', _no_sleep)
    monkeypatch.setattr('deerflow.tools.get_available_tools', tool_loader)

    output = _run_task_tool(
        runtime=runtime,
        description='员工独立包',
        prompt='complete employee work',
        subagent_type=employee_id,
        tool_call_id='tc-employee-owned-package',
    )

    message = _task_tool_message(output)
    succeeded = status == FakeSubagentStatus.COMPLETED
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == status.value
    assert message.additional_kwargs.get(SUBAGENT_STOP_REASON_KEY) == stop_reason
    if succeeded:
        assert message.content.startswith('Task execution ended') and 'Result: employee done' in message.content
        assert 'Office verification required' not in message.content
        assert 'employee report, not independent acceptance' not in message.content
        assert 'Office verification required' not in message.content
    assert captured['config'] is employee_config
    assert captured['config'].skills == ['employee-skill']
    assert captured['config'].system_prompt == 'EMPLOYEE_PROMPT_ONLY'
    assert captured['tools'][0] == 'employee-tool'
    assert captured['tools'][1].name == 'report_to_pm'
    assert captured['tools'][1].return_direct is False
    import html
    approved_context = json.loads(html.unescape(captured['prompt'].split('<work_data>', 1)[1].split('</work_data>', 1)[0]))
    assert approved_context['task'] == 'complete employee work'
    assert approved_context['background']['goal'] == 'User goal'
    from deerflow.config.app_config import AppConfig, get_app_config
    tool_args = dict(tool_loader.call_args.kwargs)
    assert isinstance(tool_args.pop('app_config'), AppConfig)
    assert tool_loader.call_args.kwargs['app_config'].models == get_app_config().models
    assert tool_args == {
        'model_name': 'employee-model',
        'groups': None,
        'subagent_enabled': False,
        'allowed_configured_tool_names': ['read_file', 'employee_mcp'],
        'allowed_external_tool_names': ['employee_mcp'],
    }
    assert all(event['employee_id'] == employee_id for event in events)
    assert all(event['subagent_type'] == employee_id for event in events)
    assert memory.add.call_count == 1
    memory_call = memory.add.call_args
    assert memory_call.kwargs['agent_name'] == employee_id
    assert memory_call.kwargs['user_id'] == 'default'
    record = json.loads(memory_call.args[1][0].content)
    assert record['task'] == 'complete employee work'
    assert record['execution_status'] == ('succeeded' if succeeded else 'failed')
    from deerflow.config.paths import get_paths
    from deerflow.office.execution_facts import ExecutionReceiptStore
    store = ExecutionReceiptStore(paths=get_paths(), user_id='default')
    receipt = store.recent_employee_receipts(employee_id=employee_id, project_id='project-one')[0]
    assert 'execution_status' not in receipt.result_ref
    assert 'partial' not in receipt.result_ref
    assert message.additional_kwargs['office_execution_workspace']['execution_status'] == receipt.status
    assert message.additional_kwargs['office_execution_workspace']['partial'] == (not succeeded or bool(stop_reason))
    event_count = len(events)
    runtime.context['run_id'] = 'resumed-parent-run'
    runtime.context['office_work_context']['latest_user_message'] = 'New clarification'
    replay = _run_task_tool(runtime=runtime, description='员工独立包', prompt='complete employee work',
                            subagent_type=employee_id, tool_call_id='tc-employee-owned-package')
    assert _task_tool_message(replay).additional_kwargs[SUBAGENT_STATUS_KEY] == status.value
    assert _task_tool_message(replay).additional_kwargs.get(SUBAGENT_STOP_REASON_KEY) == stop_reason
    if succeeded:
        assert _task_tool_message(replay).content.split('\n\n')[0] == message.content.split('\n\n')[0]
    assert len(events) == event_count
    assert memory.add.call_count == 1  # Replay must not enqueue another memory.
    fresh = _run_task_tool(runtime=runtime, description='New task', prompt='new goal',
                           subagent_type=employee_id, tool_call_id='tc-new')
    assert _task_tool_message(fresh).additional_kwargs[SUBAGENT_STATUS_KEY] == status.value
    assert len(events) > event_count
    assert memory.add.call_count == 2
    assert memory.add.call_args.args[0] != memory_call.args[0]


def test_busy_office_task_returns_without_waiting_claiming_or_starting(monkeypatch):
    employee_id = 'employee-gated'
    package = SimpleNamespace(
        employee_id=employee_id,
        config=SubagentConfig(
            name=employee_id,
            description='Gated employee',
            system_prompt='EMPLOYEE_PROMPT_ONLY',
            skills=[],
            model='employee-model',
            max_turns=10,
            timeout_seconds=30,
        ),
        tool_groups=('file:write',),
        allowed_configured_tool_names=('apply_patch',),
        allowed_external_tool_names=(),
        tool_contract=_test_tool_contract('apply_patch', 'report_to_pm'),
    )
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-r2-timeout'
    runtime.context.update(
        {
            'run_id': 'run-pm',
            'office_project_id': 'project-one',
        }
    )
    order: list[str] = []
    events: list[dict] = []

    class EmployeeGate:
        async def admit(self, item):
            order.append(f'admit:{item.parent_run_id}:{item.source_kind}')
            return SimpleNamespace(started=False, lease_id=None)

        async def wait_until_started(self, queue_id):
            order.append(f'wait:{queue_id}')
            return SimpleNamespace(started=True, lease_id='employee-lease')

        async def release(self, lease_id):
            order.append(f'release-employee:{lease_id}')

    class ProjectGate:
        async def acquire(self, project_id, lease_id, *, write_required, parallel=False):
            order.append(f'acquire-project:{project_id}:{lease_id}:{write_required}')

        async def release(self, lease_id):
            order.append(f'release-project:{lease_id}')

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            order.append(f'execute:{task_id}')
            return task_id

    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *_args: package)
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(FakeSubagentStatus.COMPLETED, result='done'),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: events.append)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', MagicMock())
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', _no_sleep)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [SimpleNamespace(name='apply_patch')])
    monkeypatch.setattr(
        task_tool_module,
        '_office_employee_gate',
        lambda: EmployeeGate(),
    )

    message = _task_tool_message(
        _run_task_tool(
            runtime=runtime,
            description='写入项目',
            prompt='实现功能',
            subagent_type=employee_id,
            tool_call_id='tc-gated',
        )
    )

    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == 'failed'
    assert 'busy' in message.content
    assert events == []
    assert order == ['admit:run-pm:pm_delegate']


def test_office_task_projects_employee_working_until_terminal(monkeypatch):
    employee_id = 'employee-projected'
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context.update(
        {
            'run_id': 'run-pm-projection',
            'office_project_id': 'project-one',
        }
    )
    projection_calls: list[dict] = []

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            assert projection_calls[-1] == {
                'employee_id': employee_id,
                'state': 'working',
                'status_detail': '正在执行任务',
                'current_task_id': task_id,
            }
            return task_id

    def record_projection(employee_id, _user_id, **kwargs):
        projection_calls.append({'employee_id': employee_id, **kwargs})
        return kwargs

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(FakeSubagentStatus.COMPLETED, result='done'),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', lambda _task_id: None)
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', _no_sleep)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])
    _patch_immediate_office_gates(monkeypatch)
    monkeypatch.setattr(
        task_tool_module,
        'update_employee_runtime_state',
        record_projection,
        raising=False,
    )

    _run_task_tool(
        runtime=runtime,
        description='投影员工状态',
        prompt='执行真实员工任务',
        subagent_type=employee_id,
        tool_call_id='tc-projected',
    )

    assert projection_calls == [
        {
            'employee_id': employee_id,
            'state': 'working',
            'status_detail': '正在执行任务',
            'current_task_id': 'tc-projected',
        },
        {
            'employee_id': employee_id,
            'state': 'idle',
            'status_detail': '等待新任务',
            'current_task_id': None,
            'expected_task_id': 'tc-projected',
        },
    ]


@pytest.mark.parametrize(
    'terminal_status',
    [
        FakeSubagentStatus.COMPLETED,
        FakeSubagentStatus.FAILED,
        FakeSubagentStatus.CANCELLED,
        FakeSubagentStatus.TIMED_OUT,
    ],
)
def test_office_task_releases_both_leases_for_every_subagent_terminal(
    monkeypatch,
    terminal_status,
):
    employee_id = 'employee-terminal-release'
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-terminal-release'
    releases: list[tuple[str, str]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='lease-terminal')

        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(('project', lease_id))

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(
            terminal_status,
            result='done' if terminal_status == FakeSubagentStatus.COMPLETED else None,
            error='stopped' if terminal_status != FakeSubagentStatus.COMPLETED else None,
        ),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', lambda _task_id: None)
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', _no_sleep)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        '_office_employee_gate',
        lambda: EmployeeGate(),
    )

    _run_task_tool(
        runtime=runtime,
        description='终态释放',
        prompt='执行任务',
        subagent_type=employee_id,
        tool_call_id='tc-terminal-release',
    )

    assert releases == [
        ('employee', 'lease-terminal'),
    ]


def test_office_task_releases_both_leases_when_execute_async_fails(monkeypatch):
    employee_id = 'employee-execute-failure'
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-execute-failure'
    releases: list[tuple[str, str]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='lease-execute')

        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(('project', lease_id))

    class FailingExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            raise RuntimeError(f'execute failed: {task_id}')

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', FailingExecutor)
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        '_office_employee_gate',
        lambda: EmployeeGate(),
    )

    with pytest.raises(RuntimeError, match='execute failed'):
        _run_task_tool(
            runtime=runtime,
            description='启动失败',
            prompt='执行任务',
            subagent_type=employee_id,
            tool_call_id='tc-execute-failure',
        )

    assert releases == [
        ('employee', 'lease-execute'),
    ]


def test_office_parent_cancellation_defers_cleanup_when_terminal_poll_fails(
    monkeypatch,
):
    employee_id = 'employee-cancel-poll-failure'
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-cancel-poll-failure'
    result_calls = 0
    cancelled: list[str] = []
    releases: list[tuple[str, str]] = []
    deferred: list[tuple[str, str | None]] = []
    projections: list[tuple[str, str, dict]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='lease-cancel')

        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(('project', lease_id))

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    def get_result(_task_id):
        nonlocal result_calls
        result_calls += 1
        if result_calls == 1:
            return _make_result(FakeSubagentStatus.RUNNING)
        raise RuntimeError('terminal poll failed')

    async def cancel_on_sleep(_seconds):
        raise asyncio.CancelledError

    def schedule_cleanup(task_id, _trace_id, **kwargs):
        deferred.append((task_id, kwargs.get('office_lease_id')))

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, 'get_background_task_result', get_result)
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', cancel_on_sleep)
    monkeypatch.setattr(task_tool_module, 'request_cancel_background_task', cancelled.append)
    monkeypatch.setattr(
        task_tool_module,
        '_schedule_deferred_subagent_cleanup',
        schedule_cleanup,
    )
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        '_office_employee_gate',
        lambda: EmployeeGate(),
    )
    monkeypatch.setattr(
        task_tool_module,
        'update_employee_runtime_state',
        lambda employee, user, **kwargs: projections.append((employee, user, kwargs)),
    )

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=runtime,
            description='父任务取消',
            prompt='执行任务',
            subagent_type=employee_id,
            tool_call_id='tc-cancel-poll-failure',
        )

    assert cancelled == ['tc-cancel-poll-failure']
    assert releases == []
    assert projections[-1][2]['state'] == 'working'
    assert deferred == [('tc-cancel-poll-failure', 'lease-cancel')]


def test_office_parent_cancellation_keeps_lease_before_deferred_cleanup(
    monkeypatch,
):
    employee_id = 'employee-cancel-immediate-release'
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-cancel-immediate-release'
    releases: list[tuple[str, str]] = []
    projections: list[tuple[str, str, dict]] = []
    deferred: list[tuple[str, str | None]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='lease-cancel-immediate')

        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def acquire(self, _project_id, _lease_id, *, write_required, parallel=False):
            return SimpleNamespace(write_required=write_required)

        async def release(self, lease_id):
            releases.append(('project', lease_id))

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    async def cancel_on_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(FakeSubagentStatus.RUNNING),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', cancel_on_sleep)
    monkeypatch.setattr(task_tool_module, 'request_cancel_background_task', lambda _task_id: None)
    monkeypatch.setattr(
        task_tool_module,
        '_schedule_deferred_subagent_cleanup',
        lambda task_id, _trace_id, **kwargs: deferred.append(
            (task_id, kwargs.get('office_lease_id'))
        ),
    )
    monkeypatch.setattr(
        task_tool_module,
        '_office_employee_gate',
        lambda: EmployeeGate(),
    )
    monkeypatch.setattr(
        task_tool_module,
        'update_employee_runtime_state',
        lambda employee, user, **kwargs: projections.append((employee, user, kwargs)),
    )
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=runtime,
            description='父任务取消',
            prompt='执行任务',
            subagent_type=employee_id,
            tool_call_id='tc-cancel-immediate-release',
        )

    assert releases == []
    assert projections[-1][2] == {
        'state': 'working',
        'status_detail': '正在执行任务',
        'current_task_id': 'tc-cancel-immediate-release',
    }
    assert deferred == [('tc-cancel-immediate-release', 'lease-cancel-immediate')]




def test_office_parent_cancellation_keeps_lease_when_cancel_request_fails(
    monkeypatch,
):
    """A failed native cancel request keeps cleanup ownership with the child."""
    employee_id = 'employee-cancel-request-failure'
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-cancel-request-failure'
    releases: list[tuple[str, str]] = []
    deferred: list[tuple[str, str | None]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='lease-cancel-request-failure')

        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(('project', lease_id))

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    async def cancel_on_sleep(_seconds):
        raise asyncio.CancelledError

    def failed_cancel(_task_id):
        raise RuntimeError('native cancel request failed')

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(FakeSubagentStatus.RUNNING),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', cancel_on_sleep)
    monkeypatch.setattr(task_tool_module, 'request_cancel_background_task', failed_cancel)
    monkeypatch.setattr(
        task_tool_module,
        '_schedule_deferred_subagent_cleanup',
        lambda task_id, _trace_id, **kwargs: deferred.append(
            (task_id, kwargs.get('office_lease_id'))
        ),
    )
    monkeypatch.setattr(task_tool_module, '_office_employee_gate', lambda: EmployeeGate())
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=runtime,
            description='父任务取消',
            prompt='执行任务',
            subagent_type=employee_id,
            tool_call_id='tc-cancel-request-failure',
        )

    assert releases == []
    assert deferred == [
        ('tc-cancel-request-failure', 'lease-cancel-request-failure'),
    ]






@pytest.mark.asyncio
async def test_office_task_releases_leases_when_employee_projection_update_fails(
    monkeypatch,
):
    """Setup exceptions after admission must not strand the Office leases."""
    employee_id = "employee-projection-failure"
    runtime = _make_approved_office_runtime(
        metadata={
            "office_employee_id": "pm",
            "office_employee_registry_snapshot": [
                {"employee_id": employee_id, "enabled": True},
            ],
        }
    )
    runtime.context["run_id"] = "run-pm-projection-failure"
    runtime.context["office_project_id"] = "project-one"
    releases: list[tuple[str, str]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id="lease-projection-failure")

        async def release(self, lease_id):
            releases.append(("employee", lease_id))

    class ProjectGate:
        async def acquire(self, project_id, lease_id, *, write_required, parallel=False):
            assert (project_id, write_required) == ("project-one", False)
            return SimpleNamespace()

        async def release(self, lease_id):
            releases.append(("project", lease_id))

    monkeypatch.setattr(
        task_tool_module,
        "resolve_employee_subagent_package",
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, "_office_employee_gate", lambda: EmployeeGate())
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "update_employee_runtime_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection failed")),
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        await task_tool_module.task_tool.coroutine(
            runtime=runtime,
            description="投影异常",
            prompt="执行任务",
            subagent_type=employee_id,
            tool_call_id="tc-projection-failure",
        )

    assert releases == [
        ("employee", "lease-projection-failure"),
    ]


@pytest.mark.parametrize('failure_point', ['task_started_event', 'result_poll'])
def test_office_task_cancels_and_defers_lease_cleanup_after_unexpected_failure(
    monkeypatch,
    failure_point,
):
    employee_id = 'employee-unexpected-failure'
    package = SimpleNamespace(
        employee_id=employee_id,
        config=SubagentConfig(
            name=employee_id,
            description='Failure employee',
            system_prompt='EMPLOYEE_PROMPT_ONLY',
            skills=[],
            model='employee-model',
            max_turns=10,
            timeout_seconds=30,
        ),
        tool_groups=(),
        allowed_configured_tool_names=(),
        allowed_external_tool_names=(),
        tool_contract=_test_tool_contract('report_to_pm'),
    )
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-unexpected-failure'
    cancelled: list[str] = []
    deferred: list[tuple[str, str | None]] = []

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    def writer(event):
        if failure_point == 'task_started_event' and event['type'] == 'task_started':
            raise RuntimeError('task event failed')

    def get_result(_task_id):
        if failure_point == 'result_poll':
            raise RuntimeError('result polling failed')
        return _make_result(FakeSubagentStatus.RUNNING)

    def schedule_cleanup(task_id, _trace_id, **kwargs):
        deferred.append((task_id, kwargs.get('office_lease_id')))

    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *_args: package)
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, 'get_background_task_result', get_result)
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: writer)
    monkeypatch.setattr(
        task_tool_module,
        'request_cancel_background_task',
        cancelled.append,
    )
    monkeypatch.setattr(
        task_tool_module,
        '_schedule_deferred_subagent_cleanup',
        schedule_cleanup,
    )
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])
    _patch_immediate_office_gates(monkeypatch)

    with pytest.raises(RuntimeError):
        _run_task_tool(
            runtime=runtime,
            description='触发异常',
            prompt='执行任务',
            subagent_type=employee_id,
            tool_call_id='tc-unexpected-failure',
        )

    assert cancelled == ['tc-unexpected-failure']
    assert deferred == [('tc-unexpected-failure', 'office-test-lease')]


def test_office_task_releases_lease_when_unexpected_cancel_request_fails(
    monkeypatch,
):
    """Unexpected task errors must still release the Office execution lease."""
    employee_id = 'employee-unexpected-cancel-failure'
    package = _office_employee_package(employee_id)
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-unexpected-cancel-failure'
    releases: list[tuple[str, str]] = []
    deferred: list[tuple[str, str | None]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='lease-unexpected-cancel-failure')

        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(('project', lease_id))

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    def failed_cancel(_task_id):
        raise RuntimeError('native cancel request failed')

    def failed_writer(event):
        if event['type'] == 'task_started':
            raise RuntimeError('task event failed')

    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *_args: package)
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', DummyExecutor)
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: failed_writer)
    monkeypatch.setattr(task_tool_module, 'request_cancel_background_task', failed_cancel)
    monkeypatch.setattr(
        task_tool_module,
        '_schedule_deferred_subagent_cleanup',
        lambda task_id, _trace_id, **kwargs: deferred.append(
            (task_id, kwargs.get('office_lease_id'))
        ),
    )
    monkeypatch.setattr(task_tool_module, '_office_employee_gate', lambda: EmployeeGate())
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])

    with pytest.raises(RuntimeError, match='task event failed'):
        _run_task_tool(
            runtime=runtime,
            description='异常清理',
            prompt='执行任务',
            subagent_type=employee_id,
            tool_call_id='tc-unexpected-cancel-failure',
        )

    assert releases == []
    assert deferred == [
        ('tc-unexpected-cancel-failure', 'lease-unexpected-cancel-failure'),
    ]


@pytest.mark.asyncio
async def test_office_task_releases_lease_when_cancelled_during_native_start(
    monkeypatch,
):
    """Cancellation between admission and native child start cannot leak a lease."""
    employee_id = 'employee-native-start-cancel'
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-native-start-cancel'
    releases: list[tuple[str, str]] = []
    projections: list[dict] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id='lease-native-start-cancel')

        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def acquire(self, *_args, **_kwargs):
            raise AssertionError('read-only child must not acquire project write access')

        async def release(self, lease_id):
            releases.append(('project', lease_id))

    class CancelledExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            raise asyncio.CancelledError

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', CancelledExecutor)
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        '_office_employee_gate',
        lambda: EmployeeGate(),
    )
    monkeypatch.setattr(
        task_tool_module,
        'update_employee_runtime_state',
        lambda employee_id, _user_id, **kwargs: projections.append(
            {'employee_id': employee_id, **kwargs}
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await task_tool_module.task_tool.coroutine(
            runtime=runtime,
            description='原生子任务启动时取消',
            prompt='执行只读检查',
            subagent_type=employee_id,
            tool_call_id='tc-native-start-cancel',
        )

    assert releases == [
        ('employee', 'lease-native-start-cancel'),
    ]
    assert projections[-1]['state'] == 'idle'
    assert projections[-1]['current_task_id'] is None


def test_task_tool_rejects_bash_subagent_when_host_bash_disabled(monkeypatch):
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: _make_subagent_config())
    monkeypatch.setattr(task_tool_module, "is_host_bash_allowed", lambda: False)

    result = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="run commands",
        subagent_type="bash",
        tool_call_id="tc-bash",
    )

    message = _task_tool_message(result)
    assert isinstance(message.content, str)
    assert message.content.startswith("Task failed. Error: Bash subagent is disabled")
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "failed"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE


def test_task_tool_threads_runtime_app_config_to_subagent_dependencies(monkeypatch):
    app_config = object()
    config = _make_subagent_config(name="bash")
    runtime = _make_runtime(app_config=app_config)
    events = []
    captured = {}

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            captured["prompt"] = prompt
            return task_id or "generated-task-id"

    def fake_get_available_subagent_names(*, app_config):
        captured["names_app_config"] = app_config
        return ["bash"]

    def fake_get_subagent_config(name, *, app_config):
        captured["config_lookup"] = (name, app_config)
        return config

    def fake_is_host_bash_allowed(config):
        captured["bash_gate_app_config"] = config
        return True

    def fake_get_available_tools(**kwargs):
        captured["tools_kwargs"] = kwargs
        return ["tool-a"]

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", fake_get_available_subagent_names)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", fake_get_subagent_config)
    monkeypatch.setattr(task_tool_module, "is_host_bash_allowed", fake_is_host_bash_allowed)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", fake_get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="运行命令",
        prompt="inspect files",
        subagent_type="bash",
        tool_call_id="tc-explicit-config",
    )

    message = _task_tool_message(output)
    assert message.content == "Task execution ended. Result: done"
    assert captured["names_app_config"] is app_config
    assert captured["config_lookup"] == ("bash", app_config)
    assert captured["bash_gate_app_config"] is app_config
    assert captured["tools_kwargs"]["app_config"] is app_config
    assert captured["executor_kwargs"]["app_config"] is app_config
    assert captured["executor_kwargs"]["tools"] == ["tool-a"]


def test_task_tool_emits_running_and_completed_events(monkeypatch):
    config = _make_subagent_config()
    runtime = _make_runtime()
    runtime.context["deerflow_trace_id"] = "task-trace-1"
    events = []
    captured = {}
    get_available_tools = MagicMock(return_value=["tool-a", "tool-b"])

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["executor_kwargs"] = kwargs

        def execute_async(self, prompt, task_id=None):
            captured["prompt"] = prompt
            captured["task_id"] = task_id
            return task_id or "generated-task-id"

    # Simulate two polling rounds: first running (with one message), then completed.
    responses = iter(
        [
            _make_result(FakeSubagentStatus.RUNNING, ai_messages=[{"id": "m1", "content": "phase-1"}]),
            _make_result(
                FakeSubagentStatus.COMPLETED,
                ai_messages=[{"id": "m1", "content": "phase-1"}, {"id": "m2", "content": "phase-2"}],
                result="all done",
            ),
        ]
    )

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: next(responses))
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    # task_tool lazily imports from deerflow.tools at call time, so patch that module-level function.
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="运行子任务",
        prompt="collect diagnostics",
        subagent_type="general-purpose",
        tool_call_id="tc-123",
    )

    message = _task_tool_message(output)
    assert message.content == "Task execution ended. Result: all done"
    assert captured["prompt"] == "collect diagnostics"
    assert captured["task_id"] == "tc-123"
    assert captured["executor_kwargs"]["thread_id"] == "thread-1"
    assert captured["executor_kwargs"]["parent_model"] == "ark-model"
    assert captured["executor_kwargs"]["deerflow_trace_id"] == "task-trace-1"
    assert captured["executor_kwargs"]["config"].max_turns == config.max_turns
    # Skills are no longer appended to system_prompt; they are loaded per-session
    # by SubagentExecutor and injected as conversation items (Codex pattern).
    assert captured["executor_kwargs"]["config"].system_prompt == "Base system prompt"

    get_available_tools.assert_called_once_with(model_name="ark-model", groups=None, subagent_enabled=False)

    event_types = [e["type"] for e in events]
    assert event_types == ["task_started", "task_running", "task_running", "task_completed"]
    assert events[0]["model_name"] == "ark-model"
    assert events[-1]["result"] == "all done"


def test_task_tool_emits_cumulative_usage_on_running_event(monkeypatch):
    config = _make_subagent_config()
    runtime = _make_runtime()
    events = []
    usage_records = [
        {
            "source_run_id": "subagent-call-1",
            "caller": "subagent:general-purpose",
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        }
    ]
    responses = iter(
        [
            _make_result(
                FakeSubagentStatus.RUNNING,
                ai_messages=[{"id": "m1", "content": "researching"}],
                token_usage_records=usage_records,
            ),
            _make_result(
                FakeSubagentStatus.COMPLETED,
                result="done",
                token_usage_records=usage_records,
            ),
        ]
    )

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: next(responses))
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", lambda *_: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])

    _run_task_tool(
        runtime=runtime,
        description="research",
        prompt="find facts",
        subagent_type="general-purpose",
        tool_call_id="tc-live-usage",
    )

    running = next(event for event in events if event["type"] == "task_running")
    assert running["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    assert running["model_name"] == "ark-model"


def test_task_tool_propagates_tool_groups_to_subagent(monkeypatch):
    """Verify tool_groups from parent metadata are passed to get_available_tools(groups=...)."""
    config = _make_subagent_config()
    parent_tool_groups = ["file:read", "file:write", "bash"]
    runtime = SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {"workspace_path": "/tmp/workspace"},
        },
        context={"thread_id": "thread-1"},
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1", "tool_groups": parent_tool_groups}},
    )
    events = []
    get_available_tools = MagicMock(return_value=["tool-a"])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="file work only",
        subagent_type="general-purpose",
        tool_call_id="tc-groups",
    )

    assert _task_tool_message(output).content == "Task execution ended. Result: done"
    # The key assertion: groups should be propagated from parent metadata
    get_available_tools.assert_called_once_with(model_name="ark-model", groups=parent_tool_groups, subagent_enabled=False)


def test_task_tool_uses_subagent_model_override_for_tool_loading(monkeypatch):
    """Subagent model overrides should drive model-gated tool loading."""
    config = SubagentConfig(
        name="general-purpose",
        description="General helper",
        system_prompt="Base system prompt",
        model="vision-subagent-model",
        max_turns=50,
        timeout_seconds=10,
    )
    runtime = _make_runtime()
    runtime.config["metadata"]["model_name"] = "parent-text-model"
    events = []
    get_available_tools = MagicMock(return_value=[])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="inspect image",
        prompt="inspect the uploaded image",
        subagent_type="general-purpose",
        tool_call_id="tc-issue-2543",
    )

    assert _task_tool_message(output).content == "Task execution ended. Result: done"
    get_available_tools.assert_called_once_with(
        model_name="vision-subagent-model",
        groups=None,
        subagent_enabled=False,
    )


def test_task_tool_inherits_parent_skill_allowlist_for_default_subagent(monkeypatch):
    config = _make_subagent_config()
    runtime = _make_runtime()
    runtime.config["metadata"]["available_skills"] = ["safe-skill"]
    events = []
    captured = {}

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["config"] = kwargs["config"]

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="use skills",
        subagent_type="general-purpose",
        tool_call_id="tc-skills",
    )

    assert _task_tool_message(output).content == "Task execution ended. Result: done"
    assert captured["config"].skills == ["safe-skill"]


def test_task_tool_intersects_parent_and_subagent_skill_allowlists(monkeypatch):
    config = _make_subagent_config()
    config = SubagentConfig(
        name=config.name,
        description=config.description,
        system_prompt=config.system_prompt,
        max_turns=config.max_turns,
        timeout_seconds=config.timeout_seconds,
        skills=["safe-skill", "other-skill"],
    )
    runtime = _make_runtime()
    runtime.config["metadata"]["available_skills"] = ["safe-skill"]
    events = []
    captured = {}

    class DummyExecutor:
        def __init__(self, **kwargs):
            captured["config"] = kwargs["config"]

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="use skills",
        subagent_type="general-purpose",
        tool_call_id="tc-skills-intersection",
    )

    assert _task_tool_message(output).content == "Task execution ended. Result: done"
    assert captured["config"].skills == ["safe-skill"]


def test_task_tool_no_tool_groups_passes_none(monkeypatch):
    """Verify that when metadata has no tool_groups, groups=None is passed (backward compat)."""
    config = _make_subagent_config()
    # Default _make_runtime() has no tool_groups in metadata
    runtime = _make_runtime()
    events = []
    get_available_tools = MagicMock(return_value=[])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="ok"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)

    output = _run_task_tool(
        runtime=runtime,
        description="执行任务",
        prompt="normal work",
        subagent_type="general-purpose",
        tool_call_id="tc-no-groups",
    )

    assert _task_tool_message(output).content == "Task execution ended. Result: ok"
    # No tool_groups in metadata → groups=None (default behavior preserved)
    get_available_tools.assert_called_once_with(model_name="ark-model", groups=None, subagent_enabled=False)


def test_task_tool_runtime_none_passes_groups_none(monkeypatch):
    """Verify that when runtime is None, groups=None is passed (e.g., unknown subagent path exits early, but tools still load correctly)."""
    config = _make_subagent_config()
    events = []
    get_available_tools = MagicMock(return_value=[])

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="ok"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", get_available_tools)
    fallback_app_config = SimpleNamespace(models=[SimpleNamespace(name="default-model")])
    monkeypatch.setattr(task_tool_module, "get_app_config", lambda: fallback_app_config)

    output = _run_task_tool(
        runtime=None,
        description="执行任务",
        prompt="no runtime",
        subagent_type="general-purpose",
        tool_call_id="tc-no-runtime",
    )

    assert _task_tool_message(output).content == "Task execution ended. Result: ok"
    # runtime is None -> metadata is empty dict -> groups=None, model falls back to app default.
    get_available_tools.assert_called_once_with(
        model_name="default-model",
        groups=None,
        subagent_enabled=False,
        app_config=fallback_app_config,
    )

    config = _make_subagent_config()
    events = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.FAILED, error="subagent crashed"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="do fail",
        subagent_type="general-purpose",
        tool_call_id="tc-fail",
    )

    message = _task_tool_message(output)
    assert message.content == "Task failed. Error: subagent crashed"
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "failed"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == "subagent crashed"
    assert events[-1]["type"] == "task_failed"
    assert events[-1]["error"] == "subagent crashed"


def test_task_tool_returns_timed_out_message(monkeypatch):
    config = _make_subagent_config()
    events = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.TIMED_OUT, error="timeout"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="do timeout",
        subagent_type="general-purpose",
        tool_call_id="tc-timeout",
    )

    message = _task_tool_message(output)
    assert message.content == "Task timed out. Error: timeout"
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "timed_out"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == "timeout"
    assert events[-1]["type"] == "task_timed_out"
    assert events[-1]["error"] == "timeout"


def test_office_task_timeout_requires_a_real_new_task_call_for_retry(monkeypatch):
    employee_id = 'employee-timeout-r2'
    package = SimpleNamespace(
        employee_id=employee_id,
        config=SubagentConfig(
            name=employee_id,
            description='Employee timeout package',
            system_prompt='EMPLOYEE_PROMPT_ONLY',
            skills=[],
            model='employee-model',
            max_turns=17,
            timeout_seconds=30,
        ),
        tool_groups=('employee-tools',),
        allowed_configured_tool_names=('read_file',),
        allowed_external_tool_names=(),
        tool_contract=_test_tool_contract('report_to_pm'),
    )
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm-r2',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-r2-timeout'

    monkeypatch.setattr(task_tool_module, 'resolve_employee_subagent_package', lambda *_args: package)
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        'SubagentExecutor',
        type('DummyExecutor', (), {'__init__': lambda self, **kwargs: None, 'execute_async': lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(FakeSubagentStatus.TIMED_OUT, error='timeout'),
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    _patch_immediate_office_gates(monkeypatch)
    monkeypatch.setattr(task_tool_module, 'cleanup_background_task', MagicMock())
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', _no_sleep)
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **_kwargs: [])

    message = _task_tool_message(
        _run_task_tool(
            runtime=runtime,
            description='执行超时任务',
            prompt='do timeout',
            subagent_type=employee_id,
            tool_call_id='tc-office-timeout',
        )
    )

    assert 'Office retry rule' not in message.content
    assert 'Never claim a second attempt from memory or text alone' not in message.content


def test_task_tool_surfaces_stop_reason_for_capped_run(monkeypatch):
    """#3875 Phase 2: a capped run keeps a normal status (``completed`` when it
    produced a final answer) and carries the cap on ``subagent_stop_reason``.
    The polling loop threads ``result.stop_reason`` through so the lead's
    ToolMessage carries it without parsing the result text."""
    config = _make_subagent_config()
    events = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="investigated 3 of 5 sources", stop_reason="token_capped"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="do capped work",
        subagent_type="general-purpose",
        tool_call_id="tc-capped",
    )

    message = _task_tool_message(output)
    # The cap is folded into the model-visible text...
    assert message.content.startswith("Task execution ended (capped: token budget)")
    assert "investigated 3 of 5 sources" in message.content
    # ...and carried structurally on the additive field.
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "completed"
    assert message.additional_kwargs[SUBAGENT_STOP_REASON_KEY] == "token_capped"
    assert message.additional_kwargs[SUBAGENT_RESULT_BRIEF_KEY] == "investigated 3 of 5 sources"
    assert len(message.additional_kwargs[SUBAGENT_RESULT_SHA256_KEY]) == 64
    assert events[-1]["type"] == "task_completed"


def test_task_tool_polling_safety_timeout(monkeypatch):
    config = _make_subagent_config()
    # Keep max_poll_count small for test speed: (1 + 60) // 5 = 12
    config.timeout_seconds = 1
    events = []
    scheduled_cleanups = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.RUNNING, ai_messages=[]),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        task_tool_module,
        "_schedule_deferred_subagent_cleanup",
        lambda task_id, _trace_id, **_kwargs: scheduled_cleanups.append(task_id),
    )
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="never finish",
        subagent_type="general-purpose",
        tool_call_id="tc-safety-timeout",
    )

    message = _task_tool_message(output)
    assert isinstance(message.content, str)
    assert message.content.startswith("Task polling timed out after 0 minutes")
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "polling_timed_out"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == message.content
    assert events[0]["type"] == "task_started"
    assert events[-1]["type"] == "task_timed_out"
    assert scheduled_cleanups == ["tc-safety-timeout"]


def test_cleanup_called_on_completed(monkeypatch):
    """Verify cleanup_background_task is called when task completes."""
    config = _make_subagent_config()
    events = []
    cleanup_calls = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="complete task",
        subagent_type="general-purpose",
        tool_call_id="tc-cleanup-completed",
    )

    assert _task_tool_message(output).content == "Task execution ended. Result: done"
    assert cleanup_calls == ["tc-cleanup-completed"]


def test_cleanup_called_on_failed(monkeypatch):
    """Verify cleanup_background_task is called when task fails."""
    config = _make_subagent_config()
    events = []
    cleanup_calls = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.FAILED, error="error"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="fail task",
        subagent_type="general-purpose",
        tool_call_id="tc-cleanup-failed",
    )

    assert _task_tool_message(output).content == "Task failed. Error: error"
    assert cleanup_calls == ["tc-cleanup-failed"]


def test_cleanup_called_on_timed_out(monkeypatch):
    """Verify cleanup_background_task is called when task times out."""
    config = _make_subagent_config()
    events = []
    cleanup_calls = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.TIMED_OUT, error="timeout"),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="timeout task",
        subagent_type="general-purpose",
        tool_call_id="tc-cleanup-timedout",
    )

    assert _task_tool_message(output).content == "Task timed out. Error: timeout"
    assert cleanup_calls == ["tc-cleanup-timedout"]


def test_cleanup_not_called_on_polling_safety_timeout(monkeypatch):
    """Verify cleanup_background_task is NOT called directly on polling safety timeout.

    The task is still RUNNING so it cannot be safely removed yet. Instead,
    cooperative cancellation is requested and a deferred cleanup is scheduled.
    """
    config = _make_subagent_config()
    # Keep max_poll_count small for test speed: (1 + 60) // 5 = 12
    config.timeout_seconds = 1
    events = []
    cleanup_calls = []
    cancel_requests = []
    scheduled_cleanups = []

    class DummyCleanupTask:
        def add_done_callback(self, _callback):
            return None

    def fake_create_task(coro):
        scheduled_cleanups.append(coro)
        coro.close()
        return DummyCleanupTask()

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.RUNNING, ai_messages=[]),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(task_tool_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )
    monkeypatch.setattr(
        task_tool_module,
        "request_cancel_background_task",
        lambda task_id: cancel_requests.append(task_id),
    )

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="never finish",
        subagent_type="general-purpose",
        tool_call_id="tc-no-cleanup-safety-timeout",
    )

    message = _task_tool_message(output)
    assert isinstance(message.content, str)
    assert message.content.startswith("Task polling timed out after 0 minutes")
    # cleanup_background_task must NOT be called directly (task is still RUNNING)
    assert cleanup_calls == []
    # cooperative cancellation must be requested
    assert cancel_requests == ["tc-no-cleanup-safety-timeout"]
    # a deferred cleanup coroutine must be scheduled
    assert len(scheduled_cleanups) == 1


def test_office_polling_does_not_limit_total_task_duration(
    monkeypatch,
):
    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    employee_id = 'employee-polling-cancel-failure'
    package = _office_employee_package(employee_id)
    runtime = _make_approved_office_runtime(
        metadata={
            'office_employee_id': 'pm',
            'office_employee_registry_snapshot': [
                {'employee_id': employee_id, 'enabled': True}
            ],
        }
    )
    runtime.context['run_id'] = 'run-pm-polling-cancel-failure'
    package.config.timeout_seconds = 1
    scheduled_cleanups: list[tuple[str, str | None]] = []
    polls = []

    def continued_result(_task_id):
        polls.append(True)
        return (_make_result(FakeSubagentStatus.COMPLETED, result='completed after many rounds')
                if len(polls) > 30 else _make_result(FakeSubagentStatus.RUNNING))

    def failed_cancel(_task_id):
        raise RuntimeError('native cancel request failed')

    monkeypatch.setattr(
        task_tool_module,
        'resolve_employee_subagent_package',
        lambda *_args: package,
    )
    monkeypatch.setattr(task_tool_module, 'get_available_subagent_names', lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        'SubagentExecutor',
        type(
            'DummyExecutor',
            (),
            {
                '__init__': lambda self, **kwargs: None,
                'execute_async': lambda self, prompt, task_id=None: task_id,
            },
        ),
    )
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        continued_result,
    )
    monkeypatch.setattr(task_tool_module, 'get_stream_writer', lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', _no_sleep)
    monkeypatch.setattr(task_tool_module, 'request_cancel_background_task', failed_cancel)
    monkeypatch.setattr(
        task_tool_module,
        '_schedule_deferred_subagent_cleanup',
        lambda task_id, _trace_id, **kwargs: scheduled_cleanups.append(
            (task_id, kwargs.get('office_lease_id'))
        ),
    )
    monkeypatch.setattr('deerflow.tools.get_available_tools', lambda **kwargs: [])
    _patch_immediate_office_gates(monkeypatch)

    output = _run_task_tool(
        runtime=runtime,
        description='轮询超时',
        prompt='持续执行任务',
        subagent_type=employee_id,
        tool_call_id='tc-polling-cancel-failure',
    )

    message = _task_tool_message(output)
    assert 'completed after many rounds' in message.content
    assert len(polls) > 30
    assert scheduled_cleanups == []


def test_cleanup_scheduled_on_cancellation(monkeypatch):
    """Verify cancellation handler synchronously cleans up after shielded wait."""
    config = _make_subagent_config()
    events = []
    cleanup_calls = []
    poll_count = 0

    def get_result(_: str):
        nonlocal poll_count
        poll_count += 1
        # Main loop polls RUNNING twice, then shielded wait gets COMPLETED
        if poll_count <= 2:
            return _make_result(FakeSubagentStatus.RUNNING, ai_messages=[])
        return _make_result(FakeSubagentStatus.COMPLETED, result="done")

    sleep_count = 0

    async def cancel_on_second_sleep(_: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(task_tool_module, "get_background_task_result", get_result)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", cancel_on_second_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="cancel task",
            subagent_type="general-purpose",
            tool_call_id="tc-cancelled-cleanup",
        )

    # Cleanup happens synchronously within the cancellation handler
    assert cleanup_calls == ["tc-cancelled-cleanup"]


def test_cancelled_cleanup_stops_after_timeout(monkeypatch):
    """Verify cancellation handler survives a shielded-wait timeout gracefully.

    When the subagent never reaches a terminal state, the shielded wait times
    out (or is interrupted), the handler reports whatever usage it can, calls
    cleanup (which is a no-op for non-terminal tasks), and re-raises.
    """
    config = _make_subagent_config()
    events = []
    report_calls = []
    cleanup_calls = []
    scheduled_cleanups = []

    # Always return RUNNING — subagent never finishes
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.RUNNING, ai_messages=[]),
    )

    async def cancel_on_first_sleep(_: float) -> None:
        raise asyncio.CancelledError

    def fake_report_subagent_usage(runtime, result):
        report_calls.append((runtime, result))

    class DummyCleanupTask:
        def __init__(self, coro):
            self.coro = coro

        def add_done_callback(self, callback):
            self.callback = callback

    def fake_create_task(coro):
        scheduled_cleanups.append(coro)
        coro.close()
        return DummyCleanupTask(coro)

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", cancel_on_first_sleep)
    monkeypatch.setattr(task_tool_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", fake_report_subagent_usage)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="cancel task",
            subagent_type="general-purpose",
            tool_call_id="tc-cancelled-timeout",
        )

    # Non-terminal tasks cannot be cleaned immediately; a deferred cleanup
    # keeps polling after the parent cancellation path exits.
    assert cleanup_calls == []
    assert len(scheduled_cleanups) == 1
    # _report_subagent_usage is called (but skips because result has no records)
    assert len(report_calls) == 1


def test_cancellation_reads_current_subagent_result_without_waiting(monkeypatch):
    """Cancelled parent records an already-terminal child without waiting."""
    config = _make_subagent_config()
    events = []
    report_calls = []
    cleanup_calls = []
    result_polls = 0
    terminal_result = _make_result(FakeSubagentStatus.COMPLETED, result="done")

    def get_result(_: str):
        nonlocal result_polls
        result_polls += 1
        if result_polls == 1:
            return _make_result(FakeSubagentStatus.RUNNING, ai_messages=[])
        return terminal_result

    async def cancel_on_first_sleep(_: float) -> None:
        raise asyncio.CancelledError

    def fake_report_subagent_usage(runtime, result):
        report_calls.append((runtime, result))

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", get_result)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", cancel_on_first_sleep)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", fake_report_subagent_usage)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="cancel task",
            subagent_type="general-purpose",
            tool_call_id="tc-cancel-budget",
        )

    assert report_calls == [(_make_runtime(), terminal_result)]
    assert cleanup_calls == ["tc-cancel-budget"]
    assert result_polls == 2


def test_cancellation_calls_request_cancel(monkeypatch):
    """Verify CancelledError path calls request_cancel_background_task(task_id)."""
    config = _make_subagent_config()
    events = []
    cancel_requests = []

    async def cancel_on_first_sleep(_: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: _make_result(FakeSubagentStatus.RUNNING, ai_messages=[]),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", cancel_on_first_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "request_cancel_background_task",
        lambda task_id: cancel_requests.append(task_id),
    )
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: None,
    )

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="cancel me",
            subagent_type="general-purpose",
            tool_call_id="tc-cancel-request",
        )

    assert cancel_requests == ["tc-cancel-request"]


def test_task_tool_returns_cancelled_message(monkeypatch):
    """Verify polling a CANCELLED result emits task_cancelled event and returns message."""
    config = _make_subagent_config()
    events = []
    cleanup_calls = []

    # First poll: RUNNING, second poll: CANCELLED
    responses = iter(
        [
            _make_result(FakeSubagentStatus.RUNNING, ai_messages=[]),
            _make_result(FakeSubagentStatus.CANCELLED, error="Cancelled by user"),
        ]
    )

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)

    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: next(responses))
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="执行任务",
        prompt="some task",
        subagent_type="general-purpose",
        tool_call_id="tc-poll-cancelled",
    )

    message = _task_tool_message(output)
    assert message.content == "Task cancelled by user. Error: Cancelled by user"
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "cancelled"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == "Cancelled by user"
    assert any(e.get("type") == "task_cancelled" for e in events)
    assert cleanup_calls == ["tc-poll-cancelled"]


def test_task_tool_emits_completed_metadata(monkeypatch):
    config = _make_subagent_config()

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: _make_result(FakeSubagentStatus.COMPLETED, result="done"))
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", lambda *_: None)
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    message = _task_tool_message(
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="do work",
            subagent_type="general-purpose",
            tool_call_id="tc-completed-metadata",
        )
    )

    assert message.content == "Task execution ended. Result: done"
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "completed"
    assert message.additional_kwargs[SUBAGENT_RESULT_BRIEF_KEY] == "done"
    assert len(message.additional_kwargs[SUBAGENT_RESULT_SHA256_KEY]) == 64


def test_task_tool_emits_disappeared_task_metadata(monkeypatch):
    config = _make_subagent_config()
    events = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: None)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    message = _task_tool_message(
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="missing task",
            subagent_type="general-purpose",
            tool_call_id="tc-missing",
        )
    )

    assert message.content == "Task failed. Error: Task tc-missing disappeared from background tasks"
    assert message.additional_kwargs[SUBAGENT_STATUS_KEY] == "failed"
    assert message.additional_kwargs[SUBAGENT_ERROR_KEY] == "Task tc-missing disappeared from background tasks"
    assert events[-1]["type"] == "task_failed"


@pytest.mark.asyncio
async def test_deferred_cleanup_releases_employee_when_background_entry_disappears(monkeypatch):
    releases = []
    monkeypatch.setattr(task_tool_module, 'get_background_task_result', lambda _task_id: None)

    class EmployeeGate:
        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(('project', lease_id))

    await task_tool_module._deferred_cleanup_subagent_task(
        'task-missing',
        'trace-missing',
        employee_gate=EmployeeGate(),
        office_lease_id='lease-missing',
    )

    assert releases == [
        ('employee', 'lease-missing'),
    ]


@pytest.mark.asyncio
async def test_deferred_cleanup_waits_for_terminal_child_before_releasing_lease(
    monkeypatch,
):
    """A polling budget must not release an Office lease for a live child."""
    releases = []
    projections = []
    child_status = [FakeSubagentStatus.RUNNING]

    monkeypatch.setattr(task_tool_module, 'SubagentStatus', FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        lambda _task_id: _make_result(child_status[0]),
    )
    monkeypatch.setattr(
        task_tool_module,
        'update_employee_runtime_state',
        lambda employee_id, user_id, **kwargs: projections.append(
            (employee_id, user_id, kwargs)
        ),
    )

    async def advance_child(_seconds):
        child_status[0] = FakeSubagentStatus.CANCELLED

    monkeypatch.setattr(task_tool_module.asyncio, 'sleep', advance_child)

    class EmployeeGate:
        async def release(self, lease_id):
            assert child_status[0] == FakeSubagentStatus.CANCELLED
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            assert child_status[0] == FakeSubagentStatus.CANCELLED
            releases.append(('project', lease_id))

    await task_tool_module._deferred_cleanup_subagent_task(
        'task-live-child',
        'trace-live-child',
        employee_gate=EmployeeGate(),
        office_lease_id='lease-live-child',
        employee_id='employee-one',
        user_id='default',
    )

    assert releases == [
        ('employee', 'lease-live-child'),
    ]
    assert projections == [
        (
            'employee-one',
            'default',
            {
                'state': 'idle',
                'status_detail': '等待新任务',
                'current_task_id': None,
                'expected_task_id': 'task-live-child',
            },
        )
    ]


@pytest.mark.asyncio
async def test_deferred_cleanup_releases_lease_when_child_result_lookup_raises(
    monkeypatch,
):
    """A failed native result lookup must not leave a cancelled employee working."""
    releases = []
    projections = []

    def raise_lookup_error(_task_id):
        raise RuntimeError('native child result lookup failed')

    monkeypatch.setattr(
        task_tool_module,
        'get_background_task_result',
        raise_lookup_error,
    )
    monkeypatch.setattr(
        task_tool_module,
        'update_employee_runtime_state',
        lambda employee_id, user_id, **kwargs: projections.append(
            (employee_id, user_id, kwargs)
        ),
    )

    class EmployeeGate:
        async def release(self, lease_id):
            releases.append(('employee', lease_id))

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(('project', lease_id))

    await task_tool_module._deferred_cleanup_subagent_task(
        'task-result-lookup-error',
        'trace-result-lookup-error',
        employee_gate=EmployeeGate(),
        office_lease_id='lease-result-lookup-error',
        employee_id='employee-one',
        user_id='default',
    )

    assert releases == [
        ('employee', 'lease-result-lookup-error'),
    ]
    assert projections == [
        (
            'employee-one',
            'default',
            {
                'state': 'idle',
                'status_detail': '等待新任务',
                'current_task_id': None,
                'expected_task_id': 'task-result-lookup-error',
            },
        )
    ]


@pytest.mark.asyncio
async def test_office_release_still_frees_employee_when_project_release_fails() -> None:
    releases: list[str] = []

    class EmployeeGate:
        async def release(self, lease_id):
            releases.append(lease_id)

    class ProjectGate:
        async def release(self, _lease_id):
            raise RuntimeError('project release failed')

    await task_tool_module._release_office_execution_lease(EmployeeGate(), 'lease-one')

    assert releases == ['lease-one']


def test_task_tool_bounds_large_result_metadata(monkeypatch):
    config = _make_subagent_config()
    huge = "x" * 10000

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: _make_result(FakeSubagentStatus.COMPLETED, result=huge))
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", lambda *_: None)
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    message = _task_tool_message(
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="large result",
            subagent_type="general-purpose",
            tool_call_id="tc-large-result",
        )
    )

    assert message.content == f"Task execution ended. Result: {huge}"
    assert len(message.additional_kwargs[SUBAGENT_RESULT_BRIEF_KEY]) <= 2000
    assert len(message.additional_kwargs[SUBAGENT_RESULT_SHA256_KEY]) == 64


def test_cancellation_reports_subagent_usage(monkeypatch):
    """Verify cancellation handler waits (shielded) for subagent terminal state,
    then reports the final token usage before re-raising CancelledError.

    The report must happen synchronously within the cancellation handler so
    the parent worker's finally block sees the updated journal totals.
    """
    config = _make_subagent_config()
    events = []
    report_calls = []
    cleanup_calls = []

    # Terminal result with token usage collected after cancellation processing
    cancel_result = _make_result(FakeSubagentStatus.CANCELLED, error="Cancelled by user")
    cancel_result.token_usage_records = [{"source_run_id": "sub-run-1", "caller": "subagent:gp", "input_tokens": 50, "output_tokens": 25, "total_tokens": 75}]
    cancel_result.usage_reported = False

    poll_count = 0

    def get_result(_: str):
        nonlocal poll_count
        poll_count += 1
        # Main loop polls 3 times (RUNNING each time to keep looping)
        if poll_count <= 3:
            running = _make_result(FakeSubagentStatus.RUNNING, ai_messages=[])
            running.token_usage_records = []
            running.usage_reported = False
            return running
        # Shielded wait poll gets the terminal result
        return cancel_result

    sleep_count = 0

    async def cancel_on_third_sleep(_: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 3:
            raise asyncio.CancelledError

    def fake_report_subagent_usage(runtime, result):
        report_calls.append((runtime, result))

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", get_result)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", cancel_on_third_sleep)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", fake_report_subagent_usage)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(task_tool_module, "request_cancel_background_task", lambda _: None)
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: cleanup_calls.append(task_id),
    )

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=_make_runtime(),
            description="执行任务",
            prompt="cancel me",
            subagent_type="general-purpose",
            tool_call_id="tc-cancel-report",
        )

    # _report_subagent_usage is called synchronously within the cancellation
    # handler (after the shielded wait), before CancelledError is re-raised.
    assert len(report_calls) == 1
    assert report_calls[0][1] is cancel_result
    assert cleanup_calls == ["tc-cancel-report"]


@pytest.mark.asyncio
async def test_cancellation_does_not_wait_for_a_non_terminal_subagent(monkeypatch):
    """A parent cancellation must finish without waiting for a stuck child."""
    config = _make_subagent_config()
    started = asyncio.Event()
    events = []
    cancel_requests = []
    deferred = []

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type(
            "DummyExecutor",
            (),
            {
                "__init__": lambda self, **kwargs: None,
                "execute_async": lambda self, prompt, task_id=None: task_id,
            },
        ),
    )
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _task_id: _make_result(FakeSubagentStatus.RUNNING),
    )

    def writer(event):
        events.append(event)
        if event.get("type") == "task_started":
            started.set()

    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: writer)
    monkeypatch.setattr(task_tool_module, "request_cancel_background_task", cancel_requests.append)
    monkeypatch.setattr(
        task_tool_module,
        "_schedule_deferred_subagent_cleanup",
        lambda task_id, _trace_id, **kwargs: deferred.append(
            (task_id, kwargs.get("office_lease_id"))
        ),
    )
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])

    async def invoke():
        return await task_tool_module.task_tool.coroutine(
            runtime=_make_runtime(),
            description="取消卡住的任务",
            prompt="执行一个会卡住的任务",
            subagent_type="general-purpose",
            tool_call_id="tc-cancel-stuck",
        )

    task = asyncio.create_task(invoke())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    done, _pending = await asyncio.wait({task}, timeout=0.2)
    assert task in done
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_requests == ["tc-cancel-stuck"]
    assert deferred == [("tc-cancel-stuck", None)]


@pytest.mark.asyncio
async def test_cancelled_office_subagent_keeps_lease_until_native_child_terminates(
    monkeypatch,
):
    """An Office child keeps its execution lease while native cancellation is pending."""
    employee_id = "employee-cancelled"
    package = _office_employee_package(employee_id)
    runtime = _make_approved_office_runtime(
        metadata={
            "office_employee_id": "pm",
            "office_employee_registry_snapshot": [
                {"employee_id": employee_id, "enabled": True}
            ],
        }
    )
    runtime.context["run_id"] = "run-pm-cancel"
    started = asyncio.Event()
    releases: list[tuple[str, str]] = []
    deferred: list[dict[str, object]] = []
    projections: list[dict[str, object]] = []

    class EmployeeGate:
        async def admit(self, _item):
            return SimpleNamespace(started=True, lease_id="lease-child")

        async def release(self, lease_id):
            releases.append(("employee", lease_id))
            return True

    class ProjectGate:
        async def release(self, lease_id):
            releases.append(("project", lease_id))
            return True

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    def writer(event):
        if event.get("type") == "task_started":
            started.set()

    monkeypatch.setattr(
        task_tool_module,
        "resolve_employee_subagent_package",
        lambda *_args: package,
    )
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _task_id: _make_result(FakeSubagentStatus.RUNNING),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: writer)
    monkeypatch.setattr(task_tool_module, "request_cancel_background_task", lambda _task_id: None)
    monkeypatch.setattr(
        task_tool_module,
        "_schedule_deferred_subagent_cleanup",
        lambda _task_id, _trace_id, **kwargs: deferred.append(kwargs),
    )
    monkeypatch.setattr(
        task_tool_module,
        "_office_employee_gate",
        lambda: EmployeeGate(),
    )
    monkeypatch.setattr(
        task_tool_module,
        "update_employee_runtime_state",
        lambda employee_id, _user_id, **kwargs: projections.append(
            {"employee_id": employee_id, **kwargs}
        ),
    )
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kwargs: [])

    async def invoke():
        return await task_tool_module.task_tool.coroutine(
            runtime=runtime,
            description="取消员工任务",
            prompt="执行一个需要取消的任务",
            subagent_type=employee_id,
            tool_call_id="tc-office-cancel-stuck",
        )

    task = asyncio.create_task(invoke())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert releases == []
    assert len(deferred) == 1
    assert deferred[0]["office_lease_id"] == "lease-child"
    assert projections == [
        {
            "employee_id": employee_id,
            "state": "working",
            "status_detail": "正在执行任务",
            "current_task_id": "tc-office-cancel-stuck",
        }
    ]


@pytest.mark.asyncio
async def test_office_pm_cancellation_removes_pending_siblings_but_preserves_direct_work(
    monkeypatch,
):
    """Cancelling an active PM child also removes its pending siblings only."""
    from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun

    employee_id = "employee-active-cancel"
    parent_run_id = "run-pm-active-cancel"
    runtime = _make_approved_office_runtime(
        metadata={
            "office_employee_id": "pm",
            "office_employee_registry_snapshot": [
                {"employee_id": employee_id, "enabled": True}
            ],
        }
    )
    runtime.context["run_id"] = parent_run_id
    gate = EmployeeRunGate()
    started = asyncio.Event()
    cancel_requests: list[str] = []
    deferred: list[dict[str, object]] = []

    class GateWrapper:
        async def admit(self, item):
            return await gate.admit(item)

        async def wait_until_started(self, queue_id):
            return await gate.wait_until_started(queue_id)

        async def cancel_parent(self, run_id):
            return await gate.cancel_parent(run_id)

        async def release(self, lease_id):
            return await gate.release(lease_id)

    class ProjectGate:
        async def acquire(self, *_args, **_kwargs):
            return SimpleNamespace()

        async def release(self, _lease_id):
            return True

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    def writer(event):
        if event.get("type") == "task_started":
            started.set()

    monkeypatch.setattr(
        task_tool_module,
        "resolve_employee_subagent_package",
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _task_id: _make_result(FakeSubagentStatus.RUNNING),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: writer)
    monkeypatch.setattr(task_tool_module, "request_cancel_background_task", cancel_requests.append)
    monkeypatch.setattr(
        task_tool_module,
        "_schedule_deferred_subagent_cleanup",
        lambda _task_id, _trace_id, **kwargs: deferred.append(kwargs),
    )
    monkeypatch.setattr(
        task_tool_module,
        "_office_employee_gate",
        lambda: GateWrapper(),
    )
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kwargs: [])
    monkeypatch.setattr(
        task_tool_module,
        "update_employee_runtime_state",
        lambda *_args, **_kwargs: None,
    )

    task = asyncio.create_task(
        task_tool_module.task_tool.coroutine(
            runtime=runtime,
            description="取消 PM 子员工",
            prompt="执行一个需要取消的任务",
            subagent_type=employee_id,
            tool_call_id="tc-active-cancel",
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    await gate.admit(
        QueuedOfficeRun(
            queue_id="pm-pending-sibling",
            target_employee_id=employee_id,
            source_kind="pm_delegate",
            text="另一个 PM 委派",
            enqueued_at=2,
            parent_run_id=parent_run_id,
            conversation_kind="group",
        )
    )
    await gate.admit(
        QueuedOfficeRun(
            queue_id="direct-pending",
            target_employee_id=employee_id,
            source_kind="direct",
            text="用户直接任务",
            enqueued_at=3,
            conversation_kind="private",
        )
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancel_requests == ["tc-active-cancel"]
    assert len(deferred) == 1
    assert [item.queue_id for item in await gate.list_pending(employee_id)] == [
        "direct-pending"
    ]


@pytest.mark.asyncio
async def test_deferred_cleanup_task_is_retained_until_native_child_finishes(monkeypatch):
    """Deferred Office cleanup must stay strongly reachable until it releases the lease."""
    release_cleanup = asyncio.Event()
    created_tasks: list[asyncio.Task[None]] = []

    async def fake_cleanup(*_args, **_kwargs):
        await release_cleanup.wait()

    real_create_task = asyncio.create_task

    def capture_create_task(coro):
        task = real_create_task(coro)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(
        task_tool_module,
        "_deferred_cleanup_subagent_task",
        fake_cleanup,
    )
    monkeypatch.setattr(task_tool_module.asyncio, "create_task", capture_create_task)

    task_tool_module._schedule_deferred_subagent_cleanup(
        "child-cleanup",
        "trace-cleanup",
        office_lease_id="lease-cleanup",
    )
    await asyncio.sleep(0)

    assert len(created_tasks) == 1
    retained_tasks = getattr(task_tool_module, "_deferred_cleanup_tasks", set())
    assert created_tasks[0] in retained_tasks

    release_cleanup.set()
    await asyncio.wait_for(created_tasks[0], timeout=1)
    assert created_tasks[0] not in getattr(
        task_tool_module,
        "_deferred_cleanup_tasks",
        set(),
    )


@pytest.mark.parametrize(
    "status, expected_type",
    [
        (FakeSubagentStatus.COMPLETED, "task_completed"),
        (FakeSubagentStatus.FAILED, "task_failed"),
        (FakeSubagentStatus.CANCELLED, "task_cancelled"),
        (FakeSubagentStatus.TIMED_OUT, "task_timed_out"),
    ],
)
def test_terminal_events_include_usage(monkeypatch, status, expected_type):
    """Terminal task events include a usage summary from token_usage_records."""
    config = _make_subagent_config()
    runtime = _make_runtime()
    events = []

    records = [
        {"source_run_id": "r1", "caller": "subagent:general-purpose", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        {"source_run_id": "r2", "caller": "subagent:general-purpose", "input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
    ]
    result = _make_result(status, result="ok" if status == FakeSubagentStatus.COMPLETED else None, error="err" if status != FakeSubagentStatus.COMPLETED else None, token_usage_records=records)

    monkeypatch.setattr(task_tool_module, 'SubagentExecutor', type('Executor', (), {
        '__init__': lambda self, **kw: None,
        'execute_async': lambda self, prompt, task_id=None: task_id,
    }))
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: result)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", lambda *_: None)
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    _run_task_tool(
        runtime=runtime,
        description="test",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-usage",
    )

    terminal_events = [e for e in events if e["type"] == expected_type]
    assert len(terminal_events) == 1
    assert terminal_events[0]["usage"] == {
        "input_tokens": 300,
        "output_tokens": 130,
        "total_tokens": 430,
    }


def test_terminal_event_usage_none_when_no_records(monkeypatch):
    """Terminal event has usage=None when token_usage_records is empty."""
    config = _make_subagent_config()
    runtime = _make_runtime()
    events = []

    result = _make_result(FakeSubagentStatus.COMPLETED, result="done", token_usage_records=[])

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: result)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", lambda *_: None)
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    _run_task_tool(
        runtime=runtime,
        description="test",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-no-records",
    )

    completed = [e for e in events if e["type"] == "task_completed"]
    assert len(completed) == 1
    assert completed[0]["usage"] is None


def test_subagent_usage_cache_is_skipped_when_config_file_is_missing(monkeypatch):
    monkeypatch.setattr(
        task_tool_module,
        "get_app_config",
        MagicMock(side_effect=FileNotFoundError("missing config")),
    )

    assert task_tool_module._token_usage_cache_enabled(None) is False


def test_subagent_usage_cache_is_skipped_when_token_usage_is_disabled(monkeypatch):
    config = _make_subagent_config()
    app_config = SimpleNamespace(token_usage=SimpleNamespace(enabled=False))
    runtime = _make_runtime(app_config=app_config)
    records = [{"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}]
    result = _make_result(FakeSubagentStatus.COMPLETED, result="done", token_usage_records=records)

    task_tool_module._subagent_usage_cache.clear()
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda *, app_config: ["general-purpose"])
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _, *, app_config: config)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: result)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr(task_tool_module, "_report_subagent_usage", lambda *_: None)
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    _run_task_tool(
        runtime=runtime,
        description="test",
        prompt="do work",
        subagent_type="general-purpose",
        tool_call_id="tc-disabled-cache",
    )

    assert task_tool_module.pop_cached_subagent_usage("tc-disabled-cache") is None


def test_subagent_usage_cache_is_cleared_when_polling_raises(monkeypatch):
    config = _make_subagent_config()
    app_config = SimpleNamespace(token_usage=SimpleNamespace(enabled=True))
    runtime = _make_runtime(app_config=app_config)

    task_tool_module._subagent_usage_cache["tc-error"] = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda *, app_config: ["general-purpose"])
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _, *, app_config: config)
    monkeypatch.setattr(
        task_tool_module,
        "SubagentExecutor",
        type("DummyExecutor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}),
    )
    monkeypatch.setattr(task_tool_module, "get_background_task_result", MagicMock(side_effect=RuntimeError("poll failed")))
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    with pytest.raises(RuntimeError, match="poll failed"):
        _run_task_tool(
            runtime=runtime,
            description="test",
            prompt="do work",
            subagent_type="general-purpose",
            tool_call_id="tc-error",
        )

    assert task_tool_module.pop_cached_subagent_usage("tc-error") is None


@pytest.mark.asyncio
async def test_office_task_releases_terminal_child_lease_after_outer_failure(
    monkeypatch,
):
    """A terminal native child must not keep an Office employee lease after an outer error."""
    from deerflow.office.run_gate import EmployeeRunGate

    employee_id = "employee-terminal-outer-failure"
    tool_call_id = "tc-terminal-outer-failure"
    runtime = _make_approved_office_runtime(
        metadata={
            "office_employee_id": "pm",
            "office_employee_registry_snapshot": [
                {"employee_id": employee_id, "enabled": True},
            ],
        }
    )
    runtime.context["run_id"] = "run-pm-terminal-outer-failure"
    gate = EmployeeRunGate()
    projections: list[dict[str, object]] = []
    deferred: list[str] = []

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id

    def writer(event):
        if event["type"] == "task_started":
            raise RuntimeError("event sink failed")

    monkeypatch.setattr(
        task_tool_module,
        "resolve_employee_subagent_package",
        lambda *_args: _office_employee_package(employee_id),
    )
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda **_kwargs: [])
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _task_id: _make_result(
            FakeSubagentStatus.FAILED,
            error="native child failed",
        ),
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: writer)
    monkeypatch.setattr(task_tool_module, "request_cancel_background_task", lambda _task_id: None)
    monkeypatch.setattr(
        task_tool_module,
        "_schedule_deferred_subagent_cleanup",
        lambda task_id, _trace_id, **_kwargs: deferred.append(task_id),
    )
    monkeypatch.setattr(
        task_tool_module,
        "_office_employee_gate",
        lambda: gate,
    )
    monkeypatch.setattr(
        task_tool_module,
        "update_employee_runtime_state",
        lambda _employee_id, _user_id, **kwargs: projections.append(kwargs),
    )
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_kwargs: [])

    with pytest.raises(RuntimeError, match="event sink failed"):
        await task_tool_module.task_tool.coroutine(
            runtime=runtime,
            description="外层事件异常",
            prompt="执行一个已经失败的子任务",
            subagent_type=employee_id,
            tool_call_id=tool_call_id,
        )

    assert await gate.get_admission(tool_call_id) is None
    assert await gate.list_pending(employee_id) == []
    assert deferred == []
    assert projections[-1] == {
        "state": "idle",
        "status_detail": "等待新任务",
        "current_task_id": None,
        "expected_task_id": tool_call_id,
    }
