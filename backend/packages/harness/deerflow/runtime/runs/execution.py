"""Validated execution contracts for durable DeerFlow runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.types import Command

from deerflow.runtime.security import contains_credential_fields

CHECKPOINT_RUN_ID_METADATA_KEY = "deerflow_run_id"


class RunExecutionSpecValidationError(ValueError):
    """Raised when a persisted run execution contract is invalid."""


class RunLaunchContractValidationError(ValueError):
    """Raised when a persisted worker launch contract is invalid."""


_MISSING = object()


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RunExecutionSpecValidationError(f"{key} must be a non-empty string")
    return value.strip()


def _required_launch_string(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunLaunchContractValidationError(
            f'launch_contract.{key} must be a non-empty string'
        )
    return value.strip()


def _optional_launch_string(value: Any, key: str) -> str | None:
    if value is None:
        return None
    return _required_launch_string(value, key)


@dataclass(frozen=True, slots=True)
class RunExecutionSpecV1:
    """Versioned, mode-specific input needed to execute or recover a run."""

    version: Literal[1]
    mode: Literal["new", "branch", "resume"]
    input: Any = None
    source_run_id: str | None = None
    checkpoint_id: str | None = None
    resume_payload: Any = None

    @classmethod
    def parse(cls, value: Mapping[str, Any] | object) -> RunExecutionSpecV1:
        if not isinstance(value, Mapping):
            raise RunExecutionSpecValidationError("execution_spec must be an object")

        raw = dict(value)
        version = raw.get("version")
        mode = raw.get("mode")
        if version != 1:
            raise RunExecutionSpecValidationError("execution_spec.version must be 1")
        if mode not in {"new", "branch", "resume"}:
            raise RunExecutionSpecValidationError(
                "execution_spec.mode must be new, branch, or resume"
            )

        common = {"version", "mode"}
        if mode == "new":
            allowed = common | {"input"}
            if "input" not in raw:
                raise RunExecutionSpecValidationError("new execution requires input")
            spec = cls(version=1, mode="new", input=raw["input"])
        elif mode == "branch":
            allowed = common | {
                "source_run_id",
                "checkpoint_id",
                "input",
            }
            if "input" not in raw:
                raise RunExecutionSpecValidationError("branch execution requires input")
            spec = cls(
                version=1,
                mode="branch",
                source_run_id=_required_string(raw, "source_run_id"),
                checkpoint_id=_required_string(raw, "checkpoint_id"),
                input=raw["input"],
            )
        else:
            allowed = common | {
                "source_run_id",
                "checkpoint_id",
                "resume_payload",
            }
            resume_payload = raw.get("resume_payload", _MISSING)
            if resume_payload is _MISSING:
                raise RunExecutionSpecValidationError(
                    "resume execution requires resume_payload"
                )
            spec = cls(
                version=1,
                mode="resume",
                source_run_id=_required_string(raw, "source_run_id"),
                checkpoint_id=_required_string(raw, "checkpoint_id"),
                resume_payload=resume_payload,
            )

        unknown = set(raw) - allowed
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise RunExecutionSpecValidationError(
                f"execution_spec contains unknown fields: {keys}"
            )
        return spec

    def to_dict(self) -> dict[str, Any]:
        if self.mode == "new":
            return {"version": 1, "mode": "new", "input": self.input}
        if self.mode == "branch":
            return {
                "version": 1,
                "mode": "branch",
                "source_run_id": self.source_run_id,
                "checkpoint_id": self.checkpoint_id,
                "input": self.input,
            }
        return {
            "version": 1,
            "mode": "resume",
            "source_run_id": self.source_run_id,
            "checkpoint_id": self.checkpoint_id,
            "resume_payload": self.resume_payload,
        }

    def graph_input(self) -> Any:
        """Return the exact LangGraph input for this execution mode."""
        if self.mode == "resume":
            # Continue a checkpoint without an interrupt answer using native
            # None input. An empty Command is not a continuation request.
            return None if self.resume_payload is None else Command(resume=self.resume_payload)
        return self.input


def checkpoint_run_id(checkpoint_tuple: object | None) -> str | None:
    """Read the explicit owner run ID from checkpoint metadata."""
    if checkpoint_tuple is None:
        return None
    if isinstance(checkpoint_tuple, Mapping):
        metadata = checkpoint_tuple.get("metadata")
    else:
        metadata = getattr(checkpoint_tuple, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get(CHECKPOINT_RUN_ID_METADATA_KEY)
    return value if isinstance(value, str) and value else None


def checkpoint_configurable(checkpoint: object) -> dict[str, Any]:
    """Return the LangGraph configurable identity stored on a checkpoint."""
    if isinstance(checkpoint, Mapping):
        raw_config = checkpoint.get("config")
    else:
        raw_config = getattr(checkpoint, "config", None)
    if not isinstance(raw_config, Mapping):
        return {}
    configurable = raw_config.get("configurable")
    return dict(configurable) if isinstance(configurable, Mapping) else {}


async def find_owned_checkpoint_config(
    checkpointer: Any,
    *,
    thread_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Return the newest checkpoint explicitly owned by one DeerFlow Run."""
    if checkpointer is None:
        raise RuntimeError(f"Run {run_id} cannot resume without a checkpointer")
    async for checkpoint in checkpointer.alist(
        {"configurable": {"thread_id": thread_id}}
    ):
        if checkpoint_run_id(checkpoint) != run_id:
            continue
        configurable = checkpoint_configurable(checkpoint)
        if (
            configurable.get("thread_id") != thread_id
            or not configurable.get("checkpoint_id")
        ):
            continue
        return configurable
    raise RuntimeError(
        f"Run {run_id} has no owned checkpoint and cannot be resumed"
    )


def _interrupt_nodes(value: Any, key: str) -> list[str] | Literal["*"] | None:
    if value is None or value == "*":
        return value
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RunLaunchContractValidationError(
            f"launch_contract.{key} must be null, '*', or a list of strings"
        )
    return list(value)


@dataclass(frozen=True, slots=True)
class RunLaunchContractV1:
    """Credential-free inputs required to deterministically relaunch a run."""

    version: Literal[1]
    model_profile_id: str | None
    execution_spec: RunExecutionSpecV1
    config: dict[str, Any]
    context: dict[str, Any]
    stream_modes: list[str]
    stream_subgraphs: bool
    interrupt_before: list[str] | Literal["*"] | None
    interrupt_after: list[str] | Literal["*"] | None
    trusted_context: dict[str, Any]

    @classmethod
    def build(
        cls,
        *,
        model_profile_id: str | None,
        execution_spec: RunExecutionSpecV1,
        config: Mapping[str, Any] | None,
        context: Mapping[str, Any] | None,
        stream_modes: list[str] | None,
        stream_subgraphs: bool,
        interrupt_before: list[str] | Literal["*"] | None,
        interrupt_after: list[str] | Literal["*"] | None,
        trusted_context: Mapping[str, Any] | None = None,
    ) -> RunLaunchContractV1:
        if not isinstance(execution_spec, RunExecutionSpecV1):
            raise RunLaunchContractValidationError(
                "launch_contract.execution_spec must be a RunExecutionSpecV1"
            )
        safe_config = dict(config or {})
        safe_context = dict(context or {})
        safe_trusted_context = dict(trusted_context or {})
        persisted_inputs = [
            safe_config,
            safe_context,
            safe_trusted_context,
            execution_spec.to_dict(),
        ]
        if any(contains_credential_fields(value) for value in persisted_inputs):
            raise RunLaunchContractValidationError(
                "launch_contract contains credential-bearing fields"
            )
        return cls(
            version=1,
            model_profile_id=_optional_launch_string(
                model_profile_id,
                "model_profile_id",
            ),
            execution_spec=execution_spec,
            config=safe_config,
            context=safe_context,
            stream_modes=list(stream_modes or ["values"]),
            stream_subgraphs=bool(stream_subgraphs),
            interrupt_before=_interrupt_nodes(interrupt_before, "interrupt_before"),
            interrupt_after=_interrupt_nodes(interrupt_after, "interrupt_after"),
            trusted_context=safe_trusted_context,
        )

    @classmethod
    def parse(cls, value: Mapping[str, Any] | object) -> RunLaunchContractV1:
        if not isinstance(value, Mapping):
            raise RunLaunchContractValidationError("launch_contract must be an object")
        raw = dict(value)
        allowed = {
            "version",
            "model_profile_id",
            "execution_spec",
            "config",
            "context",
            "trusted_context",
            "stream_modes",
            "stream_subgraphs",
            "interrupt_before",
            "interrupt_after",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise RunLaunchContractValidationError(
                f"launch_contract contains unknown fields: {', '.join(sorted(unknown))}"
            )
        if raw.get("version") != 1:
            raise RunLaunchContractValidationError(
                "launch_contract.version must be 1"
            )
        if "execution_spec" not in raw:
            raise RunLaunchContractValidationError(
                "launch_contract.execution_spec is required"
            )
        try:
            execution_spec = RunExecutionSpecV1.parse(raw["execution_spec"])
        except RunExecutionSpecValidationError as exc:
            raise RunLaunchContractValidationError(str(exc)) from exc

        config = raw.get("config")
        context = raw.get("context")
        trusted_context = raw.get("trusted_context", {})
        modes = raw.get("stream_modes")
        if (
            not isinstance(config, Mapping)
            or not isinstance(context, Mapping)
            or not isinstance(trusted_context, Mapping)
        ):
            raise RunLaunchContractValidationError(
                "launch_contract config, context, and trusted_context must be objects"
            )
        if (
            not isinstance(modes, list)
            or not modes
            or not all(isinstance(item, str) and item for item in modes)
        ):
            raise RunLaunchContractValidationError(
                "launch_contract.stream_modes must be a non-empty list of strings"
            )
        if not isinstance(raw.get("stream_subgraphs"), bool):
            raise RunLaunchContractValidationError(
                "launch_contract.stream_subgraphs must be a boolean"
            )
        persisted_inputs = [
            dict(config),
            dict(context),
            dict(trusted_context),
            execution_spec.to_dict(),
        ]
        if any(contains_credential_fields(item) for item in persisted_inputs):
            raise RunLaunchContractValidationError(
                "launch_contract contains credential-bearing fields"
            )
        return cls(
            version=1,
            model_profile_id=_optional_launch_string(
                raw.get("model_profile_id"),
                "model_profile_id",
            ),
            execution_spec=execution_spec,
            config=dict(config),
            context=dict(context),
            stream_modes=list(modes),
            stream_subgraphs=raw["stream_subgraphs"],
            interrupt_before=_interrupt_nodes(
                raw.get("interrupt_before"),
                "interrupt_before",
            ),
            interrupt_after=_interrupt_nodes(
                raw.get("interrupt_after"),
                "interrupt_after",
            ),
            trusted_context=dict(trusted_context),
        )

    def to_dict(self) -> dict[str, Any]:
        serialized = {
            "version": 1,
            "execution_spec": self.execution_spec.to_dict(),
            "config": dict(self.config),
            "context": dict(self.context),
            "stream_modes": list(self.stream_modes),
            "stream_subgraphs": self.stream_subgraphs,
            "interrupt_before": self.interrupt_before,
            "interrupt_after": self.interrupt_after,
        }
        if self.model_profile_id is not None:
            serialized["model_profile_id"] = _required_launch_string(
                self.model_profile_id,
                "model_profile_id",
            )
        if self.trusted_context:
            serialized["trusted_context"] = dict(self.trusted_context)
        return serialized
