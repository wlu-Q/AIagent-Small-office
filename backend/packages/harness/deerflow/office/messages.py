# 本文件属于 Office 消息语义模块：只处理消息命令、Mention 校验、确定性路由和 PM 群聊增量的输入转换。
# 不负责消息投递、员工排队、Run 启动、群聊保存、游标推进、历史 API 或状态维护。
# 关联：message_pipeline.py 消费路由结果，group_messages.py 保存群聊事实，共同组成统一消息入口。
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from deerflow.office.group_messages import GroupMessage

ConversationKind = Literal["private", "group"]


@dataclass(frozen=True)
class OfficeMessageCommand:
    conversation_kind: ConversationKind
    text: str
    target_employee_id: str | None
    mention_employee_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedOfficeMessage:
    conversation_kind: ConversationKind
    text: str
    target_employee_id: str | None
    mention_employee_ids: tuple[str, ...]
    trigger_employee_ids: tuple[str, ...]
    preselected_employee_ids: tuple[str, ...]


def build_pm_group_input(
    messages: Iterable[GroupMessage],
    *,
    current_message_id: str | None = None,
    internal_request: str | None = None,
) -> list[dict[str, Any]]:
    """Convert group facts into context followed by the current PM request."""
    if (current_message_id is None) == (internal_request is None):
        raise ValueError('Exactly one user request or internal continuation is required')
    current_message = None
    context_messages: list[GroupMessage] = []
    sender_labels = {
        "user": "群聊用户",
        "employee": "群聊员工",
        "pm": "群聊 PM",
    }
    for message in messages:
        if message.message_id == current_message_id:
            current_message = message
        else:
            context_messages.append(message)

    if current_message is None and internal_request is None:
        raise ValueError("Current PM group message is missing from the delta")

    context_parts: list[str] = []
    for message in context_messages:
        label = sender_labels.get(message.sender_kind)
        if label is None:
            continue
        context_parts.append(
            f"[群聊上下文 message_id={message.message_id} {label} {message.sender_id}] {message.content}"
        )

    context_parts.append(
        f"[内部接续；不是用户批准]\n{internal_request}"
        if internal_request is not None
        else f"[当前 @PM 请求 message_id={current_message.message_id}]\n{current_message.content}"
    )
    return [
        {
            "role": "user",
            "content": "[群聊增量上下文]\n" + "\n".join(context_parts),
            **({"additional_kwargs": {"hide_from_ui": True}} if internal_request is not None else {}),
        }
    ]


def _employee_facts(employees: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    facts: dict[str, Mapping[str, Any]] = {}
    for employee in employees:
        employee_id = str(employee.get("id") or "").strip().lower()
        if employee_id:
            facts[employee_id] = employee
    return facts


def _deduplicate_employee_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("mention employee id must be a string")
        employee_id = value.strip().lower()
        if not employee_id:
            raise ValueError("mention employee id is required")
        if employee_id not in seen:
            seen.add(employee_id)
            result.append(employee_id)
    return tuple(result)


def resolve_message_command(
    command: OfficeMessageCommand,
    employees: Iterable[Mapping[str, Any]],
) -> ResolvedOfficeMessage:
    if not isinstance(command.text, str):
        raise ValueError("text must be a string")
    text = command.text.strip()
    if not text:
        raise ValueError("text is required")
    if command.conversation_kind not in {"private", "group"}:
        raise ValueError(f"Unsupported conversation kind: {command.conversation_kind}")

    facts = _employee_facts(employees)
    mentions = _deduplicate_employee_ids(command.mention_employee_ids)
    if command.target_employee_id is not None and not isinstance(
        command.target_employee_id,
        str,
    ):
        raise ValueError("target_employee_id must be a string")
    target_employee_id = (
        command.target_employee_id.strip().lower()
        if isinstance(command.target_employee_id, str)
        else None
    ) or None

    if command.conversation_kind == "private":
        if target_employee_id is None:
            raise ValueError("target_employee_id is required for private message")
        if mentions:
            raise ValueError("private message does not accept mentions")
        employee = facts.get(target_employee_id)
        if employee is None:
            raise FileNotFoundError(f"Employee not found: {target_employee_id}")
        if employee.get("canDelegate") is True:
            raise ValueError("PM does not have a private conversation")
        return ResolvedOfficeMessage(
            conversation_kind="private",
            text=text,
            target_employee_id=target_employee_id,
            mention_employee_ids=(),
            trigger_employee_ids=(target_employee_id,),
            preselected_employee_ids=(),
        )

    if target_employee_id is not None:
        raise ValueError("group message does not accept target_employee_id")
    for employee_id in mentions:
        if employee_id not in facts:
            raise FileNotFoundError(f"Employee not found: {employee_id}")
    pm_ids = tuple(
        employee_id
        for employee_id in mentions
        if facts[employee_id].get("canDelegate") is True
    )
    if pm_ids:
        return ResolvedOfficeMessage(
            conversation_kind="group",
            text=text,
            target_employee_id=None,
            mention_employee_ids=mentions,
            trigger_employee_ids=pm_ids,
            preselected_employee_ids=tuple(
                employee_id for employee_id in mentions if employee_id not in pm_ids
            ),
        )
    return ResolvedOfficeMessage(
        conversation_kind="group",
        text=text,
        target_employee_id=None,
        mention_employee_ids=mentions,
        trigger_employee_ids=mentions,
        preselected_employee_ids=(),
    )
