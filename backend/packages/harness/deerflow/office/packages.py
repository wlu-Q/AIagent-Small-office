# 本文件属于 Office 员工 Agent Package 装配模块：只从员工最终配置解析 Prompt、Skills、Tools、MCP、模型和权限。
# 不负责任务分配、消息排队、Run 生命周期、上下文保存或能力评分。
# 关联：employees.py 提供员工配置，authority.py 约束工具权限，DeerFlow Subagent 消费装配结果。
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import yaml

from deerflow.config.agents_config import (
    is_office_managed_agent,
    load_agent_config,
    load_agent_soul,
    resolve_agent_dir,
)
from deerflow.config.app_config import get_app_config
from deerflow.office.authority import (
    normalize_authority,
)
from deerflow.office.identity import RESPONSE_LANGUAGE_GUIDANCE, employee_identity
from deerflow.office.presentation import FILE_DELIVERY_GUIDANCE
from deerflow.subagents.config import SubagentConfig

_RUNTIME_PROJECTION_STATES = {'idle', 'working', 'disabled'}

_EMPLOYEE_RUNTIME_STATE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_EMPLOYEE_RUNTIME_STATE_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class EmployeeSubagentPackage:
    employee_id: str
    config: SubagentConfig
    tool_groups: tuple[str, ...]
    allowed_configured_tool_names: tuple[str, ...]
    allowed_external_tool_names: tuple[str, ...]
    tool_contract: Any = None


def employee_office_lock(user_id: str, employee_id: str) -> threading.RLock:
    key = (user_id, employee_id)
    with _EMPLOYEE_RUNTIME_STATE_LOCKS_GUARD:
        lock = _EMPLOYEE_RUNTIME_STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _EMPLOYEE_RUNTIME_STATE_LOCKS[key] = lock
        return lock


def update_employee_runtime_state(
    employee_id: str,
    user_id: str,
    *,
    state: str,
    status_detail: str,
    current_task_id: str | None,
    last_report: str | None = None,
    expected_task_id: str | None = None,
) -> dict[str, Any]:
    normalized_employee_id = str(employee_id or '').strip()
    normalized_user_id = str(user_id or '').strip()
    if state not in _RUNTIME_PROJECTION_STATES:
        raise ValueError(f'Unsupported employee runtime state: {state}')
    if not is_office_managed_agent(
        normalized_employee_id,
        user_id=normalized_user_id,
    ):
        raise FileNotFoundError(
            f'Office employee not found: {normalized_employee_id}'
        )

    office_path = resolve_agent_dir(
        normalized_employee_id,
        user_id=normalized_user_id,
    ) / 'office.yaml'
    lock = employee_office_lock(normalized_user_id, normalized_employee_id)
    with lock:
        office = yaml.safe_load(office_path.read_text(encoding='utf-8')) or {}
        projection = office.setdefault('projection', {})
        active_task_id = projection.get('current_task_id')
        if state == 'working' and (
            projection.get('enabled') is False
            or projection.get('state') == 'disabled'
        ):
            raise RuntimeError(
                f'Employee {normalized_employee_id} is disabled'
            )
        if expected_task_id is not None and active_task_id != expected_task_id:
            return dict(projection)
        if (
            state == 'working'
            and projection.get('state') == 'working'
            and active_task_id not in {None, current_task_id}
        ):
            raise RuntimeError(
                f'Employee {normalized_employee_id} is already working on '
                f'{active_task_id}'
            )

        projection['state'] = state
        projection['status_detail'] = str(status_detail or '').strip()
        projection['current_task_id'] = current_task_id
        if last_report is not None:
            projection['last_report'] = str(last_report)
        office['updated_at'] = int(time.time() * 1000)

        temporary_path = office_path.with_name(
            f'.{office_path.name}.{uuid.uuid4().hex}.tmp'
        )
        try:
            temporary_path.write_text(
                yaml.safe_dump(office, allow_unicode=True, sort_keys=False),
                encoding='utf-8',
            )
            os.replace(temporary_path, office_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return dict(projection)


def resolve_employee_subagent_package(
    employee_id: str,
    user_id: str,
    app_config: Any,
) -> EmployeeSubagentPackage:
    normalized_employee_id = str(employee_id or '').strip()
    normalized_user_id = str(user_id or '').strip()
    if not normalized_employee_id:
        raise ValueError('employee_id is required')
    if not normalized_user_id:
        raise ValueError('user_id is required')
    if not is_office_managed_agent(
        normalized_employee_id,
        user_id=normalized_user_id,
    ):
        raise FileNotFoundError(
            f'Office employee not found: {normalized_employee_id}'
        )

    agent_config = load_agent_config(
        normalized_employee_id,
        user_id=normalized_user_id,
    )
    if agent_config is None:
        raise FileNotFoundError(
            f'Agent config not found: {normalized_employee_id}'
        )
    agent_dir = resolve_agent_dir(
        normalized_employee_id,
        user_id=normalized_user_id,
    )
    office = yaml.safe_load(
        (agent_dir / 'office.yaml').read_text(encoding='utf-8')
    ) or {}
    projection = office.get('projection')
    projection = projection if isinstance(projection, dict) else {}
    if projection.get('enabled') is False or projection.get('state') == 'disabled':
        raise RuntimeError(
            f'Office employee is disabled: {normalized_employee_id}'
        )
    raw_config = yaml.safe_load(
        (agent_dir / 'config.yaml').read_text(encoding='utf-8')
    ) or {}
    runtime_contract = (
        agent_config.runtime_contract
        if isinstance(agent_config.runtime_contract, dict)
        else {}
    )
    authority = normalize_authority(runtime_contract.get('authority'))
    current_app_config = app_config if app_config is not None else get_app_config()
    from deerflow.office.model_binding import resolve_employee_model
    model_name = resolve_employee_model(raw_config)
    from deerflow.office.tool_contract import employee_tool_contract
    resolved = employee_tool_contract(agent_dir, raw_config, app_config=current_app_config,
                                      execution_kind='delegated', model_name=model_name)
    allowed_configured_tool_names = resolved.runtime_names
    allowed_external_tool_names = resolved.runtime_names
    description = str(
        agent_config.description
        or normalized_employee_id
    ).strip()
    max_turns = int(raw_config.get('max_turns') or 50)
    timeout_seconds = int(raw_config.get('timeout_seconds') or 900)
    if max_turns < 1:
        raise ValueError('employee max_turns must be positive')
    if timeout_seconds < 1:
        raise ValueError('employee timeout_seconds must be positive')

    return EmployeeSubagentPackage(
        employee_id=normalized_employee_id,
        config=SubagentConfig(
            name=normalized_employee_id,
            description=description,
            system_prompt=employee_identity(description) + "\n\n" + FILE_DELIVERY_GUIDANCE + "\n\n" + load_agent_soul(
                normalized_employee_id,
                user_id=normalized_user_id,
            ) + "\n\n" + RESPONSE_LANGUAGE_GUIDANCE,
            tools=list(resolved.runtime_names),
            disallowed_tools=['task', 'ask_clarification'],
            skills=list(agent_config.skills or []),
            model=model_name or 'inherit',
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        ),
        tool_groups=resolved.derived_groups,
        tool_contract=resolved,
        allowed_configured_tool_names=allowed_configured_tool_names,
        allowed_external_tool_names=allowed_external_tool_names,
    )
