"""Task-local observations of authorized local file retrievals."""

import hashlib
import json
from concurrent.futures import Future
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Lock

from langchain_core.messages import ToolMessage

from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY, ToolResultMeta

EVIDENCE_KEY = "office_retrieval"
READERS = frozenset({"read_file", "grep", "glob", "ls"})


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class InflightRetrievals:
    def __init__(self):
        self._lock = Lock()
        self._pending = {}

    def execute(self, key, operation):
        with self._lock:
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = self._pending[key] = Future()
        if not owner:
            return deepcopy(pending.result()), False
        try:
            result = operation()
            pending.set_result(result)
            return deepcopy(result), True
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)


@dataclass
class RetrievalCall:
    scope: str
    tool: str
    args: dict
    inflight: InflightRetrievals
    signature: str | None = None
    executions: int = 0
    joined: bool = False
    previous: dict | None = None


current_retrieval: ContextVar[RetrievalCall | None] = ContextVar("office_retrieval", default=None)


def retrieve_local(sandbox_id, tool, resolved_path, effective_args, operation):
    call = current_retrieval.get()
    if call is None or call.tool != tool:
        return operation()
    from deerflow.skills.access import current_skill_read_scope

    args = {key: value for key, value in call.args.items() if key not in {"description", "path", "target"}}
    args.update(effective_args)
    if tool == "read_file":
        args = {**{"start_line": None, "end_line": None}, **args}
    call.signature = fingerprint([call.scope, sandbox_id, str(resolved_path), tool, args])
    result, executed = call.inflight.execute((call.signature, current_skill_read_scope()), operation)
    call.executions += int(executed)
    call.joined = not executed
    return result


def retrieval_result(runtime, tool, content, **completeness):
    if not (runtime.context or {}).get("office_work"):
        return content
    partial = any(completeness.get(key) for key in ("has_more", "skipped_files", "skipped_lines", "content_truncated"))
    metadata = {TOOL_META_KEY: asdict(ToolResultMeta(
        status="partial_success" if partial else "success", error_type=None, recoverable_by_model=True,
        recommended_next_action="continue", source="tool_return"))}
    call = current_retrieval.get()
    if call is not None and call.signature:
        result_hash = fingerprint(["success", completeness, content])
        previous_hash = (call.previous or {}).get(call.signature)
        metadata[EVIDENCE_KEY] = {
            "scope": call.scope,
            "request_signature": call.signature,
            "result_fingerprint": result_hash,
            "body_fingerprint": fingerprint(content),
            "completeness": completeness,
            "freshness": "historical",
            "execution_count": call.executions,
            "joined_inflight": call.joined,
            "result_chars": len(content),
            "observed_at": datetime.now(UTC).isoformat(),
            "observation_delta": "unchanged" if previous_hash == result_hash else "changed" if previous_hash else "first",
        }
    return ToolMessage(content=content, name=tool, tool_call_id=runtime.tool_call_id,
                       status="success", additional_kwargs=metadata)
