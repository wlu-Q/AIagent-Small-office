"""Callback handler that collects LLM token usage within a subagent.

Each subagent execution creates its own collector. Calls are reported to the
parent RunJournal as they finish; retained records provide the final snapshot.
The journal deduplicates both paths by model-call identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from deerflow.runtime.context_metrics import measure_context


class SubagentTokenCollector(BaseCallbackHandler):
    """Lightweight callback handler that collects LLM token usage within a subagent."""

    def __init__(self, caller: str, reporter=None):
        super().__init__()
        self.caller = caller
        self._reporter = reporter
        self._records: list[dict[str, Any]] = []
        self._counted_run_ids: set[str] = set()
        self._input_metrics: dict[str, dict] = {}
        self._previous_input: tuple | None = None

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        try:
            metrics, prefix = measure_context(messages, kwargs.get("invocation_params"), self._previous_input)
            self._previous_input = prefix
        except Exception as exc:
            # Diagnostics never block execution or retain exception text/secrets.
            metrics = {"unavailable": True, "reason": type(exc).__name__}
        self._input_metrics[str(run_id)] = metrics

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        self._input_metrics.pop(str(run_id), None)

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id)
        input_context = self._input_metrics.pop(rid, None)
        if rid in self._counted_run_ids:
            return

        for generation in response.generations:
            for gen in generation:
                if not hasattr(gen, "message"):
                    continue
                usage = getattr(gen.message, "usage_metadata", None)
                usage_dict = dict(usage) if usage else {}
                input_tk = usage_dict.get("input_tokens", 0) or 0
                output_tk = usage_dict.get("output_tokens", 0) or 0
                total_tk = usage_dict.get("total_tokens", 0) or 0
                if total_tk <= 0:
                    total_tk = input_tk + output_tk
                if total_tk <= 0:
                    continue
                # Prompt-cache hits (needed for cache-aware cost accounting)
                details = usage_dict.get("input_token_details") or {}
                cache_read_tk = 0
                if isinstance(details, Mapping):
                    try:
                        cache_read_tk = max(int(details.get("cache_read") or 0), 0)
                    except (TypeError, ValueError):
                        cache_read_tk = 0
                # Capture the model that actually produced this response so the
                # parent journal can bucket tokens by real model rather than the
                # lead agent's resolved model
                response_metadata = getattr(gen.message, "response_metadata", None) or {}
                model_name: str | None = None
                if isinstance(response_metadata, Mapping):
                    model_name = response_metadata.get("model_name") or response_metadata.get("model")
                self._counted_run_ids.add(rid)
                record: dict[str, Any] = {
                    "source_run_id": rid,
                    "caller": self.caller,
                    "model_name": model_name,
                    "input_tokens": input_tk,
                    "output_tokens": output_tk,
                    "total_tokens": total_tk,
                }
                # Sparse, matching the journal's per-model buckets: the key is
                # only present when the provider actually reported cache hits.
                if cache_read_tk > 0:
                    record["cache_read_tokens"] = cache_read_tk
                if input_context is not None:
                    record["input_context"] = input_context
                self._records.append(record)
                if self._reporter is not None:
                    self._reporter([record])
                return

    def snapshot_records(self) -> list[dict[str, Any]]:
        """Return a copy of the accumulated usage records."""
        return list(self._records)
