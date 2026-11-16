import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.retrieval_evidence import (
    RetrievalEvidenceMiddleware,
    project_evidence,
    retrieval_scope,
)
from deerflow.sandbox import tools
from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.sandbox.retrieval import (
    EVIDENCE_KEY,
    InflightRetrievals,
    current_retrieval,
)
from deerflow.skills.access import _read_scope


@pytest.fixture
def environment(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}, "description": "x" * 300}))
    sandbox = LocalSandbox(id="local:test-user-autouse:retrieval", path_mappings=[
        PathMapping(container_path="/mnt/user-data/workspace", local_path=str(project)),
    ])
    monkeypatch.setattr(tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
    monkeypatch.setattr(tools, "is_local_sandbox", lambda runtime: True)
    async def initialized(runtime):
        return sandbox
    monkeypatch.setattr(tools, "ensure_sandbox_initialized_async", initialized)
    runtime = SimpleNamespace(context={
        "office_work": {"user_id": "test-user-autouse", "project_id": "project", "thread_id": "thread", "work_id": "work"},
        "user_id": "test-user-autouse", "agent_name": "frontend", "run_id": "run",
        "office_project_path": str(project), "office_project_id": "project",
    }, state={"thread_data": {"workspace_path": str(project), "outputs_path": str(tmp_path / "outputs")},
              "messages": []}, tool_call_id="first")
    return project, sandbox, runtime, RetrievalEvidenceMiddleware()


def invoke(environment, name="grep", **overrides):
    _, _, runtime, middleware = environment
    args = {"description": "locate", "path": "/mnt/user-data/workspace"}
    if name == "grep":
        args.update(pattern="scripts", literal=True)
    elif name == "glob":
        args.update(pattern="*.json")
    elif name == "read_file":
        args["path"] += "/package.json"
    args.update(overrides)
    request = SimpleNamespace(runtime=runtime, state=runtime.state,
                              tool_call={"name": name, "args": args, "id": runtime.tool_call_id})
    return middleware.wrap_tool_call(request, lambda request: getattr(tools, name + "_tool").func(runtime=runtime, **args))


@pytest.mark.parametrize("name", ["grep", "glob", "ls", "read_file"])
def test_true_results_with_explicit_metadata(environment, name):
    message = invoke(environment, name)
    assert isinstance(message, ToolMessage)
    assert message.status == "success"
    assert message.additional_kwargs[EVIDENCE_KEY]["execution_count"] == 1
    assert "package.json" in message.content or "scripts" in message.content


def test_repeat_compacts_only_request_view_and_keeps_source(environment):
    _, _, runtime, _ = environment
    first = invoke(environment)
    runtime.state["messages"] = [first]
    runtime.tool_call_id = "second"
    second = invoke(environment, description="different explanation", max_results=100)
    assert second.additional_kwargs[EVIDENCE_KEY]["observation_delta"] == "unchanged"
    assert second.additional_kwargs[EVIDENCE_KEY]["execution_count"] == 1
    original = deepcopy([first, second])
    projected = project_evidence([first, second], retrieval_scope(runtime.context))
    assert "重复查询" in projected[0].content
    assert "second" in projected[0].content
    assert projected[1].content == second.content
    assert [first, second] == original
    assert [message.tool_call_id for message in projected] == ["first", "second"]


@pytest.mark.parametrize("changes", [dict(offset=1), dict(literal=False), dict(case_sensitive=True),
                                     dict(glob="*.json"), dict(max_results=1)])
def test_query_semantics_never_coalesce(environment, changes):
    first = invoke(environment)
    second = invoke(environment, **changes)
    assert first.additional_kwargs[EVIDENCE_KEY]["request_signature"] != second.additional_kwargs[EVIDENCE_KEY]["request_signature"]


def test_changed_added_deleted_files_are_searched_again(environment):
    project, _, runtime, _ = environment
    first = invoke(environment)
    runtime.state["messages"] = [first]
    (project / "new.json").write_text('{"scripts": "new"}')
    second = invoke(environment)
    assert "new.json" in second.content
    assert second.additional_kwargs[EVIDENCE_KEY]["observation_delta"] == "changed"
    (project / "package.json").unlink()
    third = invoke(environment)
    assert "package.json" not in third.content
    assert project_evidence([first, second, third], retrieval_scope(runtime.context)) == [first, second, third]


def test_empty_partial_error_are_distinct(environment):
    project, _, _, _ = environment
    empty = invoke(environment, pattern="absent")
    assert empty.status == "success"
    assert not empty.additional_kwargs[EVIDENCE_KEY]["completeness"]["has_more"]
    (project / "long.txt").write_text("x" * 10000 + "scripts")
    partial = invoke(environment, literal=False)
    assert partial.additional_kwargs[EVIDENCE_KEY]["completeness"]["skipped_lines"] == 1
    error = invoke(environment, pattern="[", literal=False)
    assert isinstance(error, str) and error.startswith("Error:")


def test_file_content_is_not_error_metadata(environment):
    project, _, _, _ = environment
    (project / "package.json").write_text("Error: permission denied, truncated, no results found")
    message = invoke(environment, "read_file")
    assert message.status == "success"
    assert message.additional_kwargs["deerflow_tool_meta"]["source"] == "tool_return"


def test_removed_or_budgeted_source_cannot_be_referenced(environment):
    _, _, runtime, _ = environment
    first = invoke(environment)
    second = first.model_copy(update={"tool_call_id": "second", "content": "summary only"})
    assert project_evidence([first, second], retrieval_scope(runtime.context)) == [first, second]
    assert project_evidence([first], retrieval_scope(runtime.context)) == [first]
    assert project_evidence([second], retrieval_scope(runtime.context)) == [second]


def test_scope_and_permission_changes_do_not_reuse(environment):
    _, _, runtime, _ = environment
    first = invoke(environment)
    runtime.context["office_tool_contract"] = {"revision": "new"}
    second = invoke(environment)
    assert first.additional_kwargs[EVIDENCE_KEY]["request_signature"] != second.additional_kwargs[EVIDENCE_KEY]["request_signature"]
    assert project_evidence([first, second], retrieval_scope(runtime.context)) == [first, second]
    token = _read_scope.set(lambda path: "denied")
    try:
        denied = invoke(environment)
        assert isinstance(denied, str) and denied.startswith("Error:")
    finally:
        _read_scope.reset(token)


def test_singleflight_only_merges_overlapping_operations():
    pool = InflightRetrievals()
    entered, release, joined = Event(), Event(), Event()
    executions = []
    def operation():
        executions.append(True)
        entered.set()
        assert release.wait(5)
        return {"matches": ["original"]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(pool.execute, "same", operation)
        assert entered.wait(5)
        pending = pool._pending["same"]
        original_result = pending.result
        def wait_result():
            joined.set()
            return original_result()
        pending.result = wait_result
        second = executor.submit(pool.execute, "same", operation)
        assert joined.wait(5)
        release.set()
        first_result, first_executed = first.result()
        second_result, second_executed = second.result()
    assert first_executed and not second_executed
    first_result["matches"].clear()
    assert second_result["matches"] == ["original"]
    assert len(executions) == 1
    assert pool.execute("same", lambda: "fresh") == ("fresh", True)
    assert not pool._pending


def test_index_is_metadata_only_and_not_injected(environment):
    _, _, runtime, middleware = environment
    first = invoke(environment)
    runtime.state["messages"] = [first]
    update = middleware.before_model(runtime.state, runtime)
    serialized = json.dumps(update)
    assert "source_tool_call_id" in serialized
    assert "scripts" not in serialized
    assert "messages" not in update


def test_observation_metadata_is_not_sent_as_model_content(environment):
    from langchain_openai.chat_models.base import _convert_message_to_dict
    message = invoke(environment)
    wire = _convert_message_to_dict(message)
    assert wire["content"] == message.content
    assert EVIDENCE_KEY not in json.dumps(wire)


def test_tool_authorization_still_precedes_retrieval(environment, monkeypatch):
    from deerflow.office.tool_guard import EmployeeToolGuard
    _, _, runtime, middleware = environment
    guard = EmployeeToolGuard(None, SimpleNamespace(revision="one", runtime_names=["read_file"]))
    monkeypatch.setattr(guard, "_check_revision", lambda: None)
    request = SimpleNamespace(runtime=runtime, state=runtime.state, tool_call={"name": "grep", "id": "denied", "args": {}})
    result = guard.wrap_tool_call(request, lambda request: middleware.wrap_tool_call(request, lambda request: pytest.fail("unauthorized execution")))
    assert result.status == "error"
    assert not middleware._inflight._pending


@pytest.mark.asyncio
async def test_async_tool_preserves_evidence_context(environment):
    _, _, runtime, middleware = environment
    args = dict(description="locate", path="/mnt/user-data/workspace", pattern="scripts")
    request = SimpleNamespace(runtime=runtime, state=runtime.state, tool_call={"name": "grep", "args": args})
    message = await middleware.awrap_tool_call(request, lambda request: tools.grep_tool.coroutine(runtime=runtime, **args))
    assert EVIDENCE_KEY in message.additional_kwargs
    assert current_retrieval.get() is None


def test_index_survives_compaction_but_does_not_hide_new_read(environment):
    _, _, runtime, middleware = environment
    first = invoke(environment)
    runtime.state["messages"] = [first]
    runtime.state.update(middleware.before_model(runtime.state, runtime))
    runtime.state["messages"] = [HumanMessage(content="continue")]
    runtime.state.update(middleware.before_model(runtime.state, runtime))
    second = invoke(environment)
    assert second.additional_kwargs[EVIDENCE_KEY]["observation_delta"] == "unchanged"
    assert second.additional_kwargs[EVIDENCE_KEY]["execution_count"] == 1
    assert project_evidence([second], retrieval_scope(runtime.context)) == [second]


@pytest.mark.parametrize("field", ["agent_name", "user_id", "office_native_task_id", "run_id"])
def test_task_scopes_are_separate(environment, field):
    _, _, runtime, _ = environment
    first = invoke(environment)
    runtime.context[field] = "another"
    second = invoke(environment)
    assert first.additional_kwargs[EVIDENCE_KEY]["scope"] != second.additional_kwargs[EVIDENCE_KEY]["scope"]


def test_read_ranges_and_truncation_are_explicit(environment, monkeypatch):
    project, _, _, _ = environment
    (project / "package.json").write_text("first\nsecond\nthird")
    first = invoke(environment, "read_file", start_line=1, end_line=2)
    second = invoke(environment, "read_file", start_line=2, end_line=3)
    assert first.content == "first\nsecond"
    assert first.additional_kwargs[EVIDENCE_KEY]["request_signature"] != second.additional_kwargs[EVIDENCE_KEY]["request_signature"]
    monkeypatch.setattr(tools, "_truncate_read_file_output", lambda text, cap: text[:2])
    truncated = invoke(environment, "read_file")
    assert truncated.additional_kwargs[EVIDENCE_KEY]["completeness"]["content_truncated"]


def test_singleflight_errors_do_not_become_cached_results():
    pool = InflightRetrievals()
    def denied():
        raise PermissionError("denied")
    with pytest.raises(PermissionError):
        pool.execute("key", denied)
    assert not pool._pending
    assert pool.execute("key", lambda: "fresh") == ("fresh", True)


def test_scope_filter_is_applied_to_each_call(environment):
    _, _, runtime, _ = environment
    first = invoke(environment)
    token = _read_scope.set(lambda path: "not allowed" if str(path).endswith("package.json") else None)
    try:
        second = invoke(environment)
    finally:
        _read_scope.reset(token)
    assert "package.json" not in second.content
    assert first.additional_kwargs[EVIDENCE_KEY]["result_fingerprint"] != second.additional_kwargs[EVIDENCE_KEY]["result_fingerprint"]
    assert project_evidence([first, second], retrieval_scope(runtime.context)) == [first, second]


@pytest.mark.asyncio
async def test_real_agent_chain_keeps_protocol_and_original_history(environment):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import Field

    from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware
    from deerflow.agents.thread_state import ThreadState
    from deerflow.config.tool_output_config import ToolOutputConfig

    class RecordingModel(FakeMessagesListChatModel):
        inputs: list = Field(default_factory=list)
        def bind_tools(self, tools, **kwargs):
            return self
        def _generate(self, messages, *args, **kwargs):
            self.inputs.append(deepcopy(messages))
            return super()._generate(messages, *args, **kwargs)

    _, _, runtime, middleware = environment
    args = dict(description="find scripts", path="/mnt/user-data/workspace", pattern="scripts", literal=True)
    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "grep", "args": args, "id": "call-1"}]),
        AIMessage(content="", tool_calls=[{"name": "grep", "args": args, "id": "call-2"}]),
        AIMessage(content="The dev command is vite."),
    ])
    agent = create_agent(model=model, tools=[tools.grep_tool], state_schema=ThreadState, middleware=[
        ToolOutputBudgetMiddleware(ToolOutputConfig()), middleware,
    ])
    result = await agent.ainvoke({**runtime.state, "messages": [HumanMessage(content="What is the dev command?")]},
                                context=runtime.context)
    stored = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    sent = [message for message in model.inputs[-1] if isinstance(message, ToolMessage)]
    assert len(stored) == len(sent) == 2
    assert "scripts" in stored[0].content and "scripts" in stored[1].content
    assert "重复查询" in sent[0].content
    assert "scripts" in sent[1].content
    assert [message.tool_call_id for message in sent] == ["call-1", "call-2"]
    assert result["retrieval_evidence"]["entries"]


def test_budget_removes_full_body_without_creating_dangling_reference(environment, tmp_path):
    from deerflow.agents.middlewares.tool_output_budget_middleware import _patch_tool_message
    from deerflow.config.tool_output_config import ToolOutputConfig
    _, _, runtime, _ = environment
    first = invoke(environment)
    second = first.model_copy(update={"tool_call_id": "second"})
    config = ToolOutputConfig(externalize_min_chars=20, preview_head_chars=10, preview_tail_chars=10)
    budgeted = _patch_tool_message(second, config, str(tmp_path))
    assert budgeted.content != second.content
    assert project_evidence([first, budgeted], retrieval_scope(runtime.context)) == [first, budgeted]


@pytest.mark.asyncio
async def test_cancelling_waiter_does_not_cancel_shared_search():
    pool = InflightRetrievals()
    entered, waiting, release = Event(), Event(), Event()
    def operation():
        entered.set()
        assert release.wait(5)
        return "result"
    owner = asyncio.create_task(asyncio.to_thread(pool.execute, "key", operation))
    assert await asyncio.to_thread(entered.wait, 5)
    pending = pool._pending["key"]
    original_result = pending.result
    def wait_result():
        waiting.set()
        return original_result()
    pending.result = wait_result
    waiter = asyncio.create_task(asyncio.to_thread(pool.execute, "key", operation))
    assert await asyncio.to_thread(waiting.wait, 5)
    waiter.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert await owner == ("result", True)
    assert not pool._pending
