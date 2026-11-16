"""Keep retrieval provenance in state and deduplicate only the outgoing input."""

import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from deerflow.sandbox.retrieval import (
    EVIDENCE_KEY,
    READERS,
    InflightRetrievals,
    RetrievalCall,
    current_retrieval,
    fingerprint,
)


def retrieval_scope(context):
    if not context or not context.get("office_work"):
        return None
    return fingerprint({key: context.get(key) for key in (
        "office_work", "agent_name", "user_id", "run_id", "office_tool_contract",
        "office_native_task_id", "office_execution_workspace", "office_employee_id",
    )})


def project_evidence(messages, scope):
    projected = list(messages)
    representatives = {}
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, ToolMessage) or message.status != "success" or message.name not in READERS:
            continue
        evidence = message.additional_kwargs.get(EVIDENCE_KEY)
        if not evidence or evidence.get("scope") != scope:
            continue
        if fingerprint(message.content) != evidence.get("body_fingerprint"):
            continue
        key = (evidence["request_signature"], evidence["result_fingerprint"])
        representative = representatives.get(key)
        if representative is None:
            representatives[key] = message
            continue
        text = (f"重复查询：结果与当前输入中保留的工具结果 {representative.tool_call_id} 相同，范围与完整性未变。"
                "无新增观察；证据足够则执行，否则针对具体缺口补查。")
        if len(text) >= len(message.content):
            continue
        projected[index] = message.model_copy(update={"content": text})
    return projected


class RetrievalEvidenceMiddleware(AgentMiddleware):
    def __init__(self, index_chars=12000):
        self._inflight = InflightRetrievals()
        self._index_chars = index_chars

    def _call(self, request):
        scope = retrieval_scope(request.runtime.context)
        name = request.tool_call["name"]
        if scope is None or name not in READERS:
            return None
        previous = {}
        index = request.state.get("retrieval_evidence") or {}
        if index.get("scope") == scope:
            previous.update((entry["request_signature"], entry["result_fingerprint"]) for entry in index["entries"])
        for message in request.state.get("messages", []):
            if isinstance(message, ToolMessage):
                evidence = message.additional_kwargs.get(EVIDENCE_KEY)
                if evidence and evidence.get("scope") == scope and message.status == "success":
                    previous[evidence["request_signature"]] = evidence["result_fingerprint"]
        return RetrievalCall(scope, name, request.tool_call.get("args", {}), self._inflight, previous=previous)

    def wrap_tool_call(self, request, handler):
        token = current_retrieval.set(self._call(request))
        try:
            return handler(request)
        finally:
            current_retrieval.reset(token)

    async def awrap_tool_call(self, request, handler):
        token = current_retrieval.set(self._call(request))
        try:
            return await handler(request)
        finally:
            current_retrieval.reset(token)

    def before_model(self, state, runtime):
        scope = retrieval_scope(runtime.context)
        if scope is None:
            return None
        entries = {}
        size = 0
        candidates = []
        for message in reversed(state.get("messages", [])):
            if not isinstance(message, ToolMessage) or message.status != "success":
                continue
            evidence = message.additional_kwargs.get(EVIDENCE_KEY)
            if not evidence or evidence.get("scope") != scope:
                continue
            entry = {key: evidence[key] for key in (
                "request_signature", "result_fingerprint", "completeness", "freshness")}
            entry.update(tool=message.name, source_tool_call_id=message.tool_call_id)
            candidates.append(entry)
        existing = state.get("retrieval_evidence") or {}
        if existing.get("scope") == scope:
            candidates.extend(existing.get("entries", []))
        for entry in candidates:
            signature = entry["request_signature"]
            if signature in entries:
                continue
            size += len(json.dumps(entry, ensure_ascii=False))
            if size > self._index_chars:
                break
            entries[signature] = entry
        return {"retrieval_evidence": {"scope": scope, "entries": list(entries.values())}}

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    def wrap_model_call(self, request, handler):
        scope = retrieval_scope(request.runtime.context)
        return handler(request.override(messages=project_evidence(request.messages, scope)) if scope else request)

    async def awrap_model_call(self, request, handler):
        scope = retrieval_scope(request.runtime.context)
        return await handler(request.override(messages=project_evidence(request.messages, scope)) if scope else request)
