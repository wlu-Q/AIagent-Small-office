from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
from deerflow.office.projects import project_id_for_path
from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.tools import read_file_tool
from deerflow.sandbox.patch_tool import apply_patch_tool


def _as_posix(path: str) -> str:
    return path.replace("\\", "/")


class TestThreadDataMiddleware:
    def test_direct_chat_edits_bound_project_without_a_worktree(self, tmp_path, monkeypatch):
        project = tmp_path / 'bound-project'
        project.mkdir()
        (project / 'manual-agent-loop-test.md').write_text('original\n', encoding='utf-8')
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path / '.deer-flow'), lazy_init=True)
        context = {
            'thread_id': 'thread-123',
            'office_project_id': project_id_for_path(project),
            'office_project_path': str(project),
            'office_employee_id': 'writer',
            'office_work': {'user_id': 'test-user-autouse', 'project_id': project_id_for_path(project), 'thread_id': 'thread-123', 'work_id': 'direct'},
        }
        thread_data = middleware.before_agent(
            state={},
            runtime=Runtime(context=context),
        )['thread_data']
        sandbox = LocalSandbox('local:default:thread-123')
        tool_runtime = SimpleNamespace(
            state={
                'thread_data': thread_data,
                'sandbox': {'sandbox_id': sandbox.id},
            },
            context=context,
            config={},
            tool_call_id="direct-edit",
        )
        monkeypatch.setattr(
            'deerflow.sandbox.tools.ensure_sandbox_initialized',
            lambda runtime: sandbox,
        )

        write_result = apply_patch_tool.func(runtime=tool_runtime,
            patch='*** Begin Patch\n*** Update File: /mnt/user-data/workspace/manual-agent-loop-test.md\n@@\n-original\n+MANUAL_AGENT_LOOP_SUCCESS\n*** End Patch')
        read_result = read_file_tool.func(
            runtime=tool_runtime,
            description='read project verification file',
            path='/mnt/user-data/workspace/manual-agent-loop-test.md',
        )

        assert write_result.status == 'success'
        assert read_result.status == 'success'
        assert read_result.content == 'MANUAL_AGENT_LOOP_SUCCESS\n'
        assert not (project / '.git').exists()
        assert (project / 'manual-agent-loop-test.md').read_text(encoding='utf-8') == (
            'MANUAL_AGENT_LOOP_SUCCESS\n'
        )

    def test_bound_project_replaces_only_the_agent_working_directory(self, tmp_path):
        project = tmp_path / 'bound-project'
        project.mkdir()
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path / '.deer-flow'), lazy_init=True)
        runtime = Runtime(
            context={
                'thread_id': 'thread-123',
                'office_project_id': project_id_for_path(project),
                'office_project_path': str(project),
            }
        )

        result = middleware.before_agent(state={}, runtime=runtime)

        assert result is not None
        thread_data = result['thread_data']
        assert Path(thread_data['workspace_path']) == project.resolve()
        assert _as_posix(thread_data['private_workspace_path']).endswith(
            'users/test-user-autouse/threads/thread-123/user-data/workspace'
        )
        assert _as_posix(thread_data['uploads_path']).endswith(
            'users/test-user-autouse/threads/thread-123/user-data/uploads'
        )
        assert thread_data['project_id'] == project_id_for_path(project)

        assert _as_posix(thread_data['private_workspace_path']).endswith(
            'users/test-user-autouse/threads/thread-123/user-data/workspace'
        )

    def test_subagent_preserves_inherited_bound_project_thread_data(self, tmp_path):
        project = tmp_path / 'bound-project'
        project.mkdir()
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path / '.deer-flow'), lazy_init=True)
        inherited = {
            'thread_id': 'thread-123',
            'workspace_path': str(project.resolve()),
            'private_workspace_path': str(tmp_path / 'private-workspace'),
            'uploads_path': str(tmp_path / 'uploads'),
            'outputs_path': str(tmp_path / 'outputs'),
            'project_id': project_id_for_path(project),
        }

        result = middleware.before_agent(
            state={'thread_data': inherited},
            runtime=Runtime(context={'thread_id': 'thread-123', 'is_subagent': True}),
        )

        assert result is not None
        assert result['thread_data'] == inherited

    def test_before_agent_returns_paths_when_thread_id_present_in_context(self, tmp_path):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)

        result = middleware.before_agent(state={}, runtime=Runtime(context={"thread_id": "thread-123"}))

        assert result is not None
        assert _as_posix(result["thread_data"]["workspace_path"]).endswith("users/test-user-autouse/threads/thread-123/user-data/workspace")
        assert _as_posix(result["thread_data"]["uploads_path"]).endswith("users/test-user-autouse/threads/thread-123/user-data/uploads")
        assert _as_posix(result["thread_data"]["outputs_path"]).endswith("users/test-user-autouse/threads/thread-123/user-data/outputs")

    def test_before_agent_uses_thread_id_from_configurable_when_context_is_none(self, tmp_path, monkeypatch):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        runtime = Runtime(context=None)
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_config",
            lambda: {"configurable": {"thread_id": "thread-from-config"}},
        )

        result = middleware.before_agent(state={}, runtime=runtime)

        assert result is not None
        assert _as_posix(result["thread_data"]["workspace_path"]).endswith("users/test-user-autouse/threads/thread-from-config/user-data/workspace")
        assert runtime.context is None

    def test_before_agent_uses_thread_id_from_configurable_when_context_missing_thread_id(self, tmp_path, monkeypatch):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        runtime = Runtime(context={})
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_config",
            lambda: {"configurable": {"thread_id": "thread-from-config"}},
        )

        result = middleware.before_agent(state={}, runtime=runtime)

        assert result is not None
        assert _as_posix(result["thread_data"]["uploads_path"]).endswith("users/test-user-autouse/threads/thread-from-config/user-data/uploads")
        assert runtime.context == {}

    def test_before_agent_handles_none_context_with_trailing_human_message(self, tmp_path, monkeypatch):
        # Regression: run_id was read via the unguarded `runtime.context`, so a None context plus a
        # trailing HumanMessage raised AttributeError (thread_id still resolves from config.configurable).
        from langchain_core.messages import HumanMessage

        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        runtime = Runtime(context=None)
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_config",
            lambda: {"configurable": {"thread_id": "thread-from-config"}},
        )

        result = middleware.before_agent(state={"messages": [HumanMessage(content="hello", id="m1")]}, runtime=runtime)

        assert result is not None
        assert runtime.context is None

    def test_before_agent_raises_clear_error_when_thread_id_missing_everywhere(self, tmp_path, monkeypatch):
        middleware = ThreadDataMiddleware(base_dir=str(tmp_path), lazy_init=True)
        monkeypatch.setattr(
            "deerflow.agents.middlewares.thread_data_middleware.get_config",
            lambda: {"configurable": {}},
        )

        with pytest.raises(ValueError, match="Thread ID is required in runtime context or config.configurable"):
            middleware.before_agent(state={}, runtime=Runtime(context=None))
