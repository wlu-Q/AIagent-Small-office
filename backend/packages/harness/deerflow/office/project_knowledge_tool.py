"""Restricted PM tool for promoting an actual user message to project knowledge."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool

from deerflow.config.paths import get_paths
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.project_knowledge import ProjectKnowledgeStore
from deerflow.office.project_knowledge_service import ProjectKnowledgeService
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime


def _runtime_metadata(runtime: Runtime) -> dict:
    config = getattr(runtime, "config", None)
    metadata = config.get("metadata", {}) if isinstance(config, dict) else {}
    return metadata if isinstance(metadata, dict) else {}


def _runtime_context(runtime: Runtime) -> dict:
    context = getattr(runtime, "context", None)
    return context if isinstance(context, dict) else {}


def _required_text(value: object, field: str, *, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ValueError(f"{field} is too long")
    return normalized


@tool("capture_user_message", parse_docstring=True)
def capture_user_message(
    message_id: str,
    category: str,
    topic: str,
    runtime: Runtime,
    scope: Literal["project"] = "project",
) -> dict:
    """Save one real user message as a confirmed project fact.

    The message must already exist in the current Office group conversation.
    This tool accepts only its stable message ID; it never accepts model-written
    source text. Use it only when the user explicitly stated a durable project
    requirement, decision, or constraint.

    Args:
        message_id: Stable ID of the persisted user group message to quote.
        category: Project knowledge category selected for the quoted message.
        topic: Stable topic key used to supersede conflicting facts.
        scope: The only supported scope is the long-lived project scope.
    """
    if scope != "project":
        raise ValueError("Only project scope can be captured by this tool")

    user_id = resolve_runtime_user_id(runtime)
    context = _runtime_context(runtime)
    metadata = _runtime_metadata(runtime)
    project_id = context.get("office_project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("A bound Office project is required")
    project_id = project_id.strip()
    if metadata.get("office_employee_id") != "pm":
        raise ValueError("Only the PM can capture project knowledge")
    if metadata.get("office_conversation_kind") != "group":
        raise ValueError("Project knowledge source must come from the PM group conversation")

    sequence_end = metadata.get("office_group_sequence_end")
    if type(sequence_end) is not int or sequence_end < 1:
        raise ValueError("The current PM group message boundary is unavailable")

    source_id = _required_text(message_id, "message_id")
    message_store = GroupMessageStore(paths=get_paths(), user_id=user_id)
    source = message_store.find(source_id)
    if source is None:
        raise ValueError("The source user message was not found")
    if source.sequence > sequence_end:
        raise ValueError("The source message is outside the current PM run")
    if source.sender_kind != "user" or source.sender_id != user_id:
        raise ValueError("The source must be an actual message from the current user")
    if source.project_id != project_id:
        raise ValueError("The source message does not belong to the current project")

    run_id = context.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("The current Office Run identity is unavailable")

    service = ProjectKnowledgeService(
        ProjectKnowledgeStore(paths=get_paths(), user_id=user_id)
    )
    fact = service.capture_user_message(
        project_id=project_id,
        user_id=user_id,
        run_id=run_id,
        message_id=source.message_id,
        text=source.content,
        category=_required_text(category, "category"),
        topic=_required_text(topic, "topic"),
        scope=scope,
        occurred_at=source.created_at,
    )
    if fact is None:
        raise RuntimeError("Project knowledge was not captured")
    return {
        "captured": True,
        "source": {
            "message_id": source.message_id,
            "sequence": source.sequence,
            "run_id": run_id,
            "project_id": project_id,
        },
        "fact": fact.to_record(),
    }
