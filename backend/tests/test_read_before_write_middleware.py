"""Tests for the read-before-write gate (issue #3857, output layer)."""

from patch_helpers import update_patch
import hashlib
import posixpath
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_request(name, args, messages=()):
    if name == 'apply_patch' and 'patch' not in args:
        path = args.get('path', '/mnt/user-data/outputs/report.md')
        args = {'patch': f'*** Begin Patch\n*** Update File: {path}\n@@\n-old\n+new\n*** End Patch'}
    runtime = MagicMock()
    runtime.context = {"thread_id": "t-test"}
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-1"},
        tool=None,
        state={"messages": list(messages)},
        runtime=runtime,
    )


def _read_marked_message(path, content, tool_call_id="r1"):
    msg = ToolMessage(content=content[:20], tool_call_id=tool_call_id, name="read_file")
    msg.additional_kwargs["deerflow_read_mark"] = {"path": path, "hash": _sha(content)}
    return msg


def _middleware(files: dict[str, str]):
    from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware

    def reader(_runtime, path):
        normalized = posixpath.normpath(path)
        if normalized not in files:
            raise FileNotFoundError(path)
        value = files[normalized]
        if isinstance(value, Exception):
            raise value
        return value

    return ReadBeforeWriteMiddleware(content_reader=reader)


class TestReadCurrentFileContent:
    def test_reads_via_sandbox_with_resolution(self):
        from deerflow.sandbox import tools as sandbox_tools

        sandbox = MagicMock()
        sandbox.read_file.return_value = "hello"
        runtime = MagicMock()
        with (
            patch.object(sandbox_tools, "ensure_sandbox_initialized", return_value=sandbox),
            patch.object(sandbox_tools, "ensure_thread_directories_exist"),
            patch.object(sandbox_tools, "is_local_sandbox", return_value=False),
        ):
            assert sandbox_tools.read_current_file_content(runtime, "/mnt/user-data/outputs/report.md") == "hello"
        sandbox.read_file.assert_called_once_with("/mnt/user-data/outputs/report.md")

    def test_propagates_file_not_found(self):
        from deerflow.sandbox import tools as sandbox_tools

        sandbox = MagicMock()
        sandbox.read_file.side_effect = FileNotFoundError()
        with (
            patch.object(sandbox_tools, "ensure_sandbox_initialized", return_value=sandbox),
            patch.object(sandbox_tools, "ensure_thread_directories_exist"),
            patch.object(sandbox_tools, "is_local_sandbox", return_value=False),
        ):
            with pytest.raises(FileNotFoundError):
                sandbox_tools.read_current_file_content(MagicMock(), "/mnt/user-data/outputs/missing.md")


class TestReadMarkStamping:
    def test_read_file_success_stamps_mark(self):
        mw = _middleware({"/mnt/user-data/outputs/report.md": "v1"})
        request = _make_request("read_file", {"description": "d", "path": "/mnt/user-data/outputs/report.md"})
        handler = MagicMock(return_value=ToolMessage(content="v1", tool_call_id="call-1", name="read_file"))
        result = mw.wrap_tool_call(request, handler)
        mark = result.additional_kwargs["deerflow_read_mark"]
        assert mark == {"path": "/mnt/user-data/outputs/report.md", "hash": _sha("v1")}

    def test_ranged_read_stamps_full_file_hash(self):
        mw = _middleware({"/mnt/user-data/outputs/report.md": "line1\nline2\nline3"})
        request = _make_request(
            "read_file",
            {"description": "d", "path": "/mnt/user-data/outputs/report.md", "start_line": 3, "end_line": 3},
        )
        handler = MagicMock(return_value=ToolMessage(content="line3", tool_call_id="call-1", name="read_file"))
        result = mw.wrap_tool_call(request, handler)
        assert result.additional_kwargs["deerflow_read_mark"]["hash"] == _sha("line1\nline2\nline3")

    def test_error_tool_message_gets_no_mark(self):
        mw = _middleware({"/mnt/user-data/outputs/report.md": "v1"})
        request = _make_request("read_file", {"description": "d", "path": "/mnt/user-data/outputs/report.md"})
        handler = MagicMock(return_value=ToolMessage(content="boom", tool_call_id="call-1", name="read_file", status="error"))
        result = mw.wrap_tool_call(request, handler)
        assert "deerflow_read_mark" not in result.additional_kwargs

    def test_reader_failure_means_no_mark(self):
        mw = _middleware({"/mnt/user-data/outputs/report.md": RuntimeError("sandbox down")})
        request = _make_request("read_file", {"description": "d", "path": "/mnt/user-data/outputs/report.md"})
        handler = MagicMock(return_value=ToolMessage(content="v1", tool_call_id="call-1", name="read_file"))
        result = mw.wrap_tool_call(request, handler)
        assert "deerflow_read_mark" not in result.additional_kwargs

    def test_non_file_tools_untouched(self):
        mw = _middleware({})
        request = _make_request("bash", {"description": "d", "command": "ls"})
        sentinel = ToolMessage(content="ok", tool_call_id="call-1", name="bash")
        handler = MagicMock(return_value=sentinel)
        assert mw.wrap_tool_call(request, handler) is sentinel


class TestWriteGate:
    PATH = "/mnt/user-data/outputs/report.md"

    def test_new_file_write_allowed(self):
        mw = _middleware({})  # file does not exist
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v1"})
        handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch"))
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once()
        assert result.status != "error"

    def test_overwrite_existing_without_read_blocked(self):
        mw = _middleware({self.PATH: "v1"})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"})
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert isinstance(result, ToolMessage)
        assert result.status == "error"
        assert result.tool_call_id == "call-1"
        assert "read" in result.content.lower()

    def test_append_without_read_blocked(self):
        mw = _middleware({self.PATH: "v1"})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "more", "append": True})
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert result.status == "error"

    def test_apply_patch_without_read_blocked(self):
        mw = _middleware({self.PATH: "v1"})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "old_str": "v1", "new_str": "v2"})
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert result.status == "error"

    def test_apply_patch_missing_file_passes_through(self):
        mw = _middleware({})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "old_str": "a", "new_str": "b"})
        native_error = ToolMessage(content="Error: File not found", tool_call_id="call-1", name="apply_patch", status="error")
        handler = MagicMock(return_value=native_error)
        assert mw.wrap_tool_call(request, handler) is native_error

    def test_fresh_mark_allows_write(self):
        mw = _middleware({self.PATH: "v1"})
        messages = [HumanMessage("hi"), AIMessage(""), _read_marked_message(self.PATH, "v1")]
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"}, messages)
        handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch"))
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once()
        assert result.status != "error"

    def test_stale_mark_after_modification_blocked(self):
        mw = _middleware({self.PATH: "v2"})  # file changed since the read of v1
        messages = [_read_marked_message(self.PATH, "v1")]
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v3", "append": True}, messages)
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert result.status == "error"

    def test_newest_mark_wins(self):
        mw = _middleware({self.PATH: "v2"})
        messages = [_read_marked_message(self.PATH, "v1", "r1"), _read_marked_message(self.PATH, "v2", "r2")]
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v3"}, messages)
        handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch"))
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once()
        assert result.status != "error"

    def test_mark_removed_by_summarization_blocks(self):
        mw = _middleware({self.PATH: "v1"})
        messages = [HumanMessage("Here is a summary of the conversation to date: ...", name="summary")]
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"}, messages)
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert result.status == "error"

    def test_gate_read_failure_fails_open(self):
        mw = _middleware({self.PATH: RuntimeError("sandbox hiccup")})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"})
        handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch"))
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once()
        assert result.status != "error"

    def test_normalized_path_matching(self):
        mw = _middleware({self.PATH: "v1"})
        messages = [_read_marked_message(self.PATH, "v1")]
        request = _make_request("apply_patch", {"description": "d", "path": "/mnt/user-data/outputs/report.md", "content": "v2"}, messages)
        handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch"))
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once()
        assert result.status != "error"

    def test_blocked_write_has_deerflow_tool_meta(self):
        from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY

        mw = _middleware({self.PATH: "v1"})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"})
        result = mw.wrap_tool_call(request, MagicMock())
        meta = (result.additional_kwargs or {}).get(TOOL_META_KEY)
        assert meta is not None, "blocked write must carry deerflow_tool_meta"
        assert meta["recoverable_by_model"] is True


class TestAsyncPaths:
    PATH = "/mnt/user-data/outputs/report.md"

    def test_async_block(self):
        import asyncio

        mw = _middleware({self.PATH: "v1"})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"})

        async def handler(_request):
            raise AssertionError("handler must not run when blocked")

        result = asyncio.run(mw.awrap_tool_call(request, handler))
        assert result.status == "error"

    def test_async_blocked_write_has_deerflow_tool_meta(self):
        import asyncio

        from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY

        mw = _middleware({self.PATH: "v1"})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"})

        async def handler(_request):
            raise AssertionError("handler must not run when blocked")

        result = asyncio.run(mw.awrap_tool_call(request, handler))
        meta = (result.additional_kwargs or {}).get(TOOL_META_KEY)
        assert meta is not None, "async blocked write must carry deerflow_tool_meta"
        assert meta["recoverable_by_model"] is True

    def test_async_read_stamps_mark(self):
        import asyncio

        mw = _middleware({self.PATH: "v1"})
        request = _make_request("read_file", {"description": "d", "path": self.PATH})

        async def handler(_request):
            return ToolMessage(content="v1", tool_call_id="call-1", name="read_file")

        result = asyncio.run(mw.awrap_tool_call(request, handler))
        assert result.additional_kwargs["deerflow_read_mark"]["hash"] == _sha("v1")

    def test_async_allowed_write_calls_handler(self):
        import asyncio

        mw = _middleware({self.PATH: "v1"})
        messages = [_read_marked_message(self.PATH, "v1")]
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"}, messages)

        async def handler(_request):
            return ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch")

        result = asyncio.run(mw.awrap_tool_call(request, handler))
        assert result.status != "error"


def _wiring_app_config(**overrides):
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig

    return AppConfig(sandbox=SandboxConfig(use="test"), **overrides)


class TestChainWiring:
    def test_enabled_by_default_in_runtime_chain(self):
        from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware
        from deerflow.agents.middlewares.sandbox_audit_middleware import SandboxAuditMiddleware
        from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware, build_lead_runtime_middlewares

        middlewares = build_lead_runtime_middlewares(app_config=_wiring_app_config())
        types = [type(m) for m in middlewares]
        assert ReadBeforeWriteMiddleware in types
        assert types.index(SandboxAuditMiddleware) < types.index(ReadBeforeWriteMiddleware) < types.index(ToolErrorHandlingMiddleware)

    def test_disabled_preserves_office_guard_but_skips_non_office_version_gate(self):
        from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
        from deerflow.config.read_before_write_config import ReadBeforeWriteConfig

        app_config = _wiring_app_config(read_before_write=ReadBeforeWriteConfig(enabled=False))
        middlewares = build_lead_runtime_middlewares(app_config=app_config)
        middleware = next(m for m in middlewares if isinstance(m, ReadBeforeWriteMiddleware))
        assert middleware.enabled is False
        request = _make_request('apply_patch', {'path': '/test'})
        handler = MagicMock(return_value=ToolMessage(content='OK', tool_call_id='call-1'))
        assert middleware.wrap_tool_call(request, handler).content == 'OK'
        handler.assert_called_once()

    def test_subagents_get_the_gate_too(self):
        from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares

        middlewares = build_subagent_runtime_middlewares(app_config=_wiring_app_config())
        assert ReadBeforeWriteMiddleware in [type(m) for m in middlewares]


class TestErrorStringSandboxes:
    """AIO/E2B sandboxes report read failures as "Error: ..." strings, not exceptions."""

    PATH = "/mnt/user-data/outputs/report.md"

    def _error_string_middleware(self, files):
        from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware

        def reader(_runtime, path):
            normalized = posixpath.normpath(path)
            if normalized not in files:
                return f"Error: can't read file {path}: file not found"
            return files[normalized]

        return ReadBeforeWriteMiddleware(content_reader=reader)

    def test_new_file_creation_not_blocked(self):
        mw = self._error_string_middleware({})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v1"})
        handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch"))
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once()
        assert result.status != "error"

    def test_no_mark_when_reread_returns_error_string(self):
        mw = self._error_string_middleware({})
        request = _make_request("read_file", {"description": "d", "path": self.PATH})
        handler = MagicMock(return_value=ToolMessage(content="v1", tool_call_id="call-1", name="read_file"))
        result = mw.wrap_tool_call(request, handler)
        assert "deerflow_read_mark" not in result.additional_kwargs

    def test_existing_file_still_gated(self):
        mw = self._error_string_middleware({self.PATH: "v1"})
        request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"})
        handler = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert result.status == "error"

    def test_existing_file_read_still_marked_and_write_allowed(self):
        mw = self._error_string_middleware({self.PATH: "v1"})
        read_request = _make_request("read_file", {"description": "d", "path": self.PATH})
        read_handler = MagicMock(return_value=ToolMessage(content="v1", tool_call_id="r1", name="read_file"))
        read_result = mw.wrap_tool_call(read_request, read_handler)
        assert read_result.additional_kwargs["deerflow_read_mark"]["hash"] == _sha("v1")

        write_request = _make_request("apply_patch", {"description": "d", "path": self.PATH, "content": "v2"}, [read_result])
        write_handler = MagicMock(return_value=ToolMessage(content="OK", tool_call_id="call-1", name="apply_patch"))
        result = mw.wrap_tool_call(write_request, write_handler)
        write_handler.assert_called_once()
        assert result.status != "error"


def test_same_response_writes_are_rejected_without_mutating_or_queueing():
    from langchain_core.messages import AIMessage
    path = '/mnt/user-data/workspace/a.txt'
    calls = [{'name': 'apply_patch', 'id': identity, 'args': {'patch': update_patch(path, 'old', 'new')}}
             for identity in ['one', 'two']]
    middleware = _middleware({path: 'old'})
    handler = MagicMock()
    for call in calls:
        result = middleware.wrap_tool_call(_make_request(call['name'], call['args'], [AIMessage(content='', tool_calls=calls)]), handler)
        assert result.status == 'error' and 'ambiguous' in result.content
    handler.assert_not_called()
