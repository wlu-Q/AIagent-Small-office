# 本文件属于 Office 员工生命周期模块：只负责模板化创建、读取、修改、禁用、删除和运行状态投影。
# 不负责消息路由、任务拆分、员工排队、DeerFlow Run 启动或 Agent 上下文保存。
# 关联：templates.py 提供岗位事实，thread_ids.py 提供会话身份，packages.py 读取最终员工配置装配 Agent。
from __future__ import annotations

from deerflow.office.user_errors import OfficeBusyError, OfficeInputError

import asyncio
import copy
import json
import re
import shutil
import tarfile
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langgraph.checkpoint.base import empty_checkpoint

from deerflow.agents.memory.backends.deermem.deermem.core.storage import create_empty_memory
from deerflow.config.paths import Paths
from deerflow.office.authority import (
    normalize_authority,
)
from deerflow.office.packages import employee_office_lock
from deerflow.office.model_binding import resolve_employee_model
from deerflow.office.templates import EmployeeTemplate, get_employee_template
from deerflow.office.thread_ids import ConversationKind, employee_thread_id
from deerflow.persistence.thread_meta.base import ThreadMetaStore
from deerflow.tools.tools import get_runtime_tool_inventory

EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
EMPLOYEE_ID_MAX_LENGTH = 128
_SERVER_OWNED_TOOL_NAMES = frozenset(
    {'task', 'report_to_pm', 'search_employee_registry'}
)
TOOL_GROUPS = {"web", "file:read", "file:write", "bash"}
PROJECTION_STATES = {"idle", "working", "disabled"}
PROJECTION_FIELDS = {
    "state",
    "statusDetail",
    "currentTaskId",
    "lastReport",
    "enabled",
    "disabledReason",
}
CREATE_EMPLOYEE_FIELDS = {
    "id",
    "templateId",
    "displayName",
    "avatar",
    "modelProfileId",
    "prompt",
    "skills",
}
DEFAULT_WORKSTATION_IDS = tuple(f"workstation-{index:02d}" for index in range(1, 13))
_TOOL_GROUP_PERMISSIONS = {
    "web": "network.read",
    "file:read": "workspace.read",
    "file:write": "workspace.write",
    "bash": "process.execute",
}

@dataclass
class _EmployeeLifecycleLockEntry:
    lock: asyncio.Lock
    users: int = 0

_EMPLOYEE_LIFECYCLE_LOCKS: dict[tuple[str, str], _EmployeeLifecycleLockEntry] = {}

def _now_ms() -> int:
    return int(time.time() * 1000)

def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _write_yaml(path: Path, payload: Any) -> None:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

def _manifest_id(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            candidate = str(value.get(key, "")).strip()
            if candidate:
                return candidate
    return ""

def _string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        candidate = value.strip()
        return {candidate} if candidate else set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()

def _manifest_values(value: Any, *keys: str) -> set[str]:
    if not isinstance(value, dict):
        return set()
    result: set[str] = set()
    for key in keys:
        result.update(_string_values(value.get(key)))
    return result

def map_tool_group(tool_id: str) -> str | None:
    normalized = tool_id.lower().replace("_", ".").replace("-", ".")
    if normalized in TOOL_GROUPS:
        return normalized
    if any(token in normalized for token in ("browser", "web", "http", "url")):
        return "web"
    if any(token in normalized for token in ("write", "edit", "patch", "create.file")):
        return "file:write"
    if any(token in normalized for token in ("read", "search", "list.file", "workspace")):
        return "file:read"
    if any(token in normalized for token in ("bash", "shell", "terminal", "command")):
        return "bash"
    return None

class OfficeEmployeeService:
    """Own the one-to-one employee, Agent, Thread, Checkpoint and Workspace lifecycle."""

    def __init__(
        self,
        *,
        paths: Paths,
        thread_store: ThreadMetaStore,
        checkpointer: Any,
        user_id: str,
        run_manager: Any | None = None,
        model_profile_exists: Callable[[str], bool] | None = None,
        default_model_profile_id: str | None = None,
        workstation_ids: tuple[str, ...] = DEFAULT_WORKSTATION_IDS,
    ) -> None:
        self.paths = paths
        self.thread_store = thread_store
        self.checkpointer = checkpointer
        self.user_id = user_id
        self.run_manager = run_manager
        self.model_profile_exists = model_profile_exists
        self.model_profile_selectable: Callable[[str], bool] | None = None
        self.default_model_profile_id = self._validate_model_profile(
            default_model_profile_id,
            required=False,
        )
        self.workstation_ids = tuple(
            dict.fromkeys(str(item).strip() for item in workstation_ids if str(item).strip())
        )
        if not self.workstation_ids:
            raise ValueError("At least one workstation is required")

    @asynccontextmanager
    async def _employee_lifecycle(self, employee_id: str) -> AsyncIterator[None]:
        key = (str(self.paths.user_dir(self.user_id).resolve()), employee_id)
        entry = _EMPLOYEE_LIFECYCLE_LOCKS.get(key)
        if entry is None:
            entry = _EmployeeLifecycleLockEntry(lock=asyncio.Lock())
            _EMPLOYEE_LIFECYCLE_LOCKS[key] = entry
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0 and _EMPLOYEE_LIFECYCLE_LOCKS.get(key) is entry:
                del _EMPLOYEE_LIFECYCLE_LOCKS[key]

    @staticmethod
    def validate_employee_id(employee_id: str) -> str:
        value = str(employee_id or "").strip().lower()
        if len(value) > EMPLOYEE_ID_MAX_LENGTH:
            raise ValueError(f"Invalid employee_id: maximum length is {EMPLOYEE_ID_MAX_LENGTH} characters")
        if not EMPLOYEE_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid employee_id: only letters, digits, and hyphens are allowed")
        return value

    def thread_id(self, employee_id: str, kind: ConversationKind) -> str:
        normalized = self.validate_employee_id(employee_id)
        is_pm = self._read_office(normalized).get("template_id") == "pm"
        return employee_thread_id(self.user_id, normalized, kind, is_pm=is_pm)

    def _agent_dir(self, employee_id: str) -> Path:
        return self.paths.user_agent_dir(self.user_id, self.validate_employee_id(employee_id))

    def _office_path(self, employee_id: str) -> Path:
        return self._agent_dir(employee_id) / "office.yaml"

    def _thread_ids_for(self, employee_id: str, *, is_pm: bool) -> tuple[str, ...]:
        kinds: tuple[ConversationKind, ...] = ("group",) if is_pm else ("private", "group")
        return tuple(
            employee_thread_id(self.user_id, employee_id, kind, is_pm=is_pm)
            for kind in kinds
        )

    async def _thread_meta_exists(self, thread_id: str) -> bool:
        return await self.thread_store.get(thread_id, user_id=self.user_id) is not None

    async def _ensure_thread_meta(
        self,
        thread_id: str,
        employee_id: str,
        *,
        kind: str,
        display_name: str,
        is_pm: bool,
    ) -> bool:
        """Create the thread ownership row if it is missing; never overwrite one.

        This row is ownership, not history: every run start is gated on
        ``thread_store.check_access``, so a missing row 404s the thread forever.
        It is therefore the one piece of thread state that must be repairable
        without touching the conversation itself.
        """
        if await self._thread_meta_exists(thread_id):
            return False
        await self.thread_store.create(
            thread_id,
            assistant_id=employee_id,
            user_id=self.user_id,
            display_name=display_name,
            metadata={
                "office_employee_id": employee_id,
                "office_conversation_kind": kind,
                "office_is_pm": is_pm,
            },
        )
        return True

    async def _ensure_base_checkpoint(self, thread_id: str, employee_id: str, *, kind: str) -> bool:
        """Write the empty base checkpoint if the thread has none; never overwrite history."""
        if await self._capture_checkpoint(thread_id) is not None:
            return False
        await self.checkpointer.aput(
            self._checkpoint_config(thread_id),
            empty_checkpoint(),
            {
                "step": -1,
                "source": "input",
                "writes": None,
                "parents": {},
                "employee_id": employee_id,
                "office_conversation_kind": kind,
            },
            {},
        )
        return True

    async def _ensure_threads_unlocked(
        self,
        employee_id: str,
        display_name: str,
        *,
        is_pm: bool,
    ) -> list[str]:
        """Provision every thread the employee owns. Idempotent and additive only.

        Returns the thread IDs whose ownership row had to be created, which is what
        ``_create_employee_unlocked`` needs for its rollback path.
        """
        created: list[str] = []
        for thread_id in self._thread_ids_for(employee_id, is_pm=is_pm):
            kind = thread_id.rsplit("__", 1)[-1]
            if await self._ensure_thread_meta(
                thread_id,
                employee_id,
                kind=kind,
                display_name=display_name,
                is_pm=is_pm,
            ):
                created.append(thread_id)
            await self._ensure_base_checkpoint(thread_id, employee_id, kind=kind)
            self.paths.ensure_thread_dirs(thread_id, user_id=self.user_id)
        return created

    def _next_workstation_id(self) -> str:
        occupied = {
            str(self._read_office(employee_id).get("workstation_id") or "").strip()
            for employee_id in self._persisted_employee_ids()
        }
        for workstation_id in self.workstation_ids:
            if workstation_id not in occupied:
                return workstation_id
        raise RuntimeError("No office workstation is available")

    def _validate_model_profile(
        self,
        model_profile_id: str | None,
        *,
        required: bool,
    ) -> str | None:
        normalized = str(model_profile_id or "").strip()
        if not normalized:
            if required:
                raise ValueError("modelProfileId must be a non-empty string")
            return None
        if self.model_profile_exists is not None and not self.model_profile_exists(normalized):
            raise ValueError(f"Unknown model profile: {normalized}")
        return normalized

    def set_default_model_profile_id(self, model_profile_id: str | None) -> None:
        """Set the initial model used when creating an employee."""

        self.default_model_profile_id = self._validate_model_profile(
            model_profile_id,
            required=True,
        )

    def migrate_global_model_bindings(self, previous_default: str) -> None:
        """Pin previously inherited models once; saved Run contracts stay untouched."""
        root = self.paths.user_agents_dir(self.user_id)
        if not root.is_dir():
            return
        for directory in root.iterdir():
            if not (directory / 'office.yaml').is_file():
                continue
            with employee_office_lock(self.user_id, directory.name):
                config = self._read_config(directory.name)
                office = self._read_office(directory.name)
                selected = config.get('model') or office.get('model_profile_id') or previous_default
                config['model'] = selected
                office['model_profile_id'] = selected
                self._write_config(directory.name, config)
                self._save_office(directory.name, office)

    def _template_runtime_contract(self, template: EmployeeTemplate) -> dict[str, Any]:
        permissions = sorted(
            {
                permission
                for group in template.tool_groups
                if (permission := _TOOL_GROUP_PERMISSIONS.get(group)) is not None
            }
        )
        return {
            "version": 4,
            "template_id": template.template_id,
            "category": template.category,
            "tags": list(template.tags),
            "specialties": list(template.specialties),
            "skills": list(template.skills),
            "permissions": permissions,
            "disabled_capabilities": [],
            "disabled_tools": [],
            "disabled_permissions": [],
            "authority": {k: v for k, v in template.authority.to_dict().items() if k != "allowed_configured_tool_names"},
        }

    @classmethod
    def _normalize_projection_patch(cls, patch: dict[str, Any]) -> dict[str, Any]:
        unknown = set(patch) - PROJECTION_FIELDS
        if unknown:
            raise ValueError(f"Unsupported employee projection fields: {sorted(unknown)}")

        normalized: dict[str, Any] = {}
        if "state" in patch:
            state = str(patch["state"] or "").strip().lower()
            if state not in PROJECTION_STATES:
                raise ValueError(f"Unsupported employee state: {state}")
            normalized["state"] = state
        for field in ("statusDetail", "lastReport", "disabledReason"):
            if field in patch:
                normalized[field] = str(patch[field] or "")
        if "currentTaskId" in patch:
            task_id = patch["currentTaskId"]
            normalized["currentTaskId"] = None if task_id is None else str(task_id)
        if "enabled" in patch:
            if not isinstance(patch["enabled"], bool):
                raise ValueError("enabled must be a boolean")
            normalized["enabled"] = patch["enabled"]
        return normalized

    async def create_employee(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        employee_id = self.validate_employee_id(str(payload.get("id") or ""))
        async with self._employee_lifecycle(employee_id):
            return await self._create_employee_unlocked(payload, allow_pm=False)

    async def ensure_builtin_pm(
        self,
        *,
        default_model_profile_id: str | None = None,
    ) -> dict[str, Any]:
        employee_id = "pm"
        async with self._employee_lifecycle(employee_id):
            if self._agent_dir(employee_id).is_dir():
                office = self._read_office(employee_id)
                if office.get("template_id") != "pm":
                    raise RuntimeError("Reserved PM employee ID is occupied by another template")
                if not resolve_employee_model(self._read_config(employee_id)) and default_model_profile_id:
                    model_profile_id = self._validate_model_profile(
                        default_model_profile_id,
                        required=True,
                    )
                    office["model_profile_id"] = model_profile_id
                    config = self._read_config(employee_id)
                    config["model"] = model_profile_id
                    self._write_config(employee_id, config)
                    self._save_office(employee_id, office)
                # An existing agent directory says nothing about the thread ownership
                # rows: a partial reset can strip them while leaving every directory in
                # place, which 404s each run start on the thread with no way back.
                await self._ensure_threads_unlocked(
                    employee_id,
                    str(office.get("display_name") or employee_id),
                    is_pm=True,
                )
                return self.get_employee_summary(employee_id)
            return await self._create_employee_unlocked(
                {
                    "id": employee_id,
                    "templateId": "pm",
                    "displayName": "项目经理",
                    "avatar": "adam",
                    "modelProfileId": default_model_profile_id,
                },
                allow_pm=True,
            )

    async def repair_threads(self) -> list[dict[str, str]]:
        """Re-provision missing thread rows and base checkpoints for every employee.

        Additive only: existing conversation history is never overwritten and no
        ownership row is ever deleted, so this is safe on every startup. Returns one
        entry per thread that had to be recreated.
        """
        repaired: list[dict[str, str]] = []
        for summary in await self.list_employees():
            employee_id = self.validate_employee_id(str(summary.get("id") or ""))
            async with self._employee_lifecycle(employee_id):
                created = await self._ensure_threads_unlocked(
                    employee_id,
                    str(summary.get("displayName") or employee_id),
                    is_pm=bool(summary.get("isPm")),
                )
            repaired.extend(
                {"employeeId": employee_id, "threadId": thread_id}
                for thread_id in created
            )
        return repaired

    async def reset_conversation_context(self, employee_id: str, *, kind: ConversationKind) -> str:
        """Drop one conversation's history while keeping the thread itself.

        The ownership row is deliberately preserved — it is identity, not history,
        and removing it would 404 every future run on this thread. Returns the
        thread ID so callers can clear the matching Runs and Run events.
        """
        normalized = self.validate_employee_id(employee_id)
        is_pm = self._read_office(normalized).get("template_id") == "pm"
        thread_id = employee_thread_id(self.user_id, normalized, kind, is_pm=is_pm)
        async with self._employee_lifecycle(normalized):
            if not await self._thread_meta_exists(thread_id):
                return thread_id
            if hasattr(self.checkpointer, "adelete_thread"):
                await self.checkpointer.adelete_thread(thread_id)
            self.paths.ensure_thread_dirs(thread_id, user_id=self.user_id)
            await self._ensure_base_checkpoint(thread_id, normalized, kind=kind)
        return thread_id

    async def _create_employee_unlocked(
        self,
        payload: dict[str, Any],
        *,
        allow_pm: bool,
    ) -> dict[str, Any]:
        unknown = set(payload) - CREATE_EMPLOYEE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported employee creation fields: {sorted(unknown)}")
        employee_id = self.validate_employee_id(str(payload.get("id") or ""))
        template = get_employee_template(str(payload.get("templateId") or ""))
        if template.is_pm and not allow_pm:
            raise ValueError("PM is created by the system")
        if template.is_pm and employee_id != "pm":
            raise ValueError("Builtin PM must use the reserved employee ID: pm")
        agent_dir = self._agent_dir(employee_id)
        thread_ids = self._thread_ids_for(employee_id, is_pm=template.is_pm)
        thread_exists = False
        for thread_id in thread_ids:
            if await self.thread_store.get(thread_id, user_id=self.user_id):
                thread_exists = True
                break
        if agent_dir.exists() or thread_exists:
            raise FileExistsError(f"Employee already exists: {employee_id}")

        now = _now_ms()
        display_name = str(payload.get("displayName") or "").strip()
        if not display_name:
            raise ValueError("displayName must be a non-empty string")
        avatar = str(payload.get("avatar") or "").strip()
        if not avatar:
            raise ValueError("avatar must be a non-empty string")
        requested_model_profile_id = payload.get("modelProfileId")
        following_default = not str(requested_model_profile_id or '').strip()
        if following_default:
            requested_model_profile_id = self.default_model_profile_id
        model_profile_id = self._validate_model_profile(
            requested_model_profile_id,
            required=not template.is_pm,
        )
        workstation_id = self._next_workstation_id()
        prompt = payload.get("prompt", template.prompt)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        requested_skills = payload.get("skills", list(template.skills))
        if not isinstance(requested_skills, list) or any(
            not isinstance(item, str) or not item.strip() for item in requested_skills
        ):
            raise ValueError("skills must be a list of non-empty strings")
        effective_skills = list(
            dict.fromkeys((*template.skills, *(item.strip() for item in requested_skills)))
        )
        resolved_soul = prompt.strip() + "\n"
        runtime_contract = self._template_runtime_contract(template)
        runtime_contract["skills"] = effective_skills
        office = {
            "version": 1,
            "employee_id": employee_id,
            "template_id": template.template_id,
            "display_name": display_name,
            "avatar": avatar,
            "model_profile_id": model_profile_id,
            "workstation_id": workstation_id,
            "projection": {
                "state": "idle",
                "status_detail": "等待新任务",
                "last_report": "暂无报告",
                "current_task_id": None,
                "enabled": True,
                "disabled_reason": "",
            },
            "created_at": now,
            "updated_at": now,
        }
        config = {
            "name": employee_id,
            "description": display_name,
            "model": model_profile_id,
            "skills": effective_skills,
            "runtime_contract": runtime_contract,
        }
        staging = agent_dir.with_name(
            f".{employee_id}.staging-{uuid.uuid4().hex}"
        )
        created_threads: list[str] = []
        try:
            staging.mkdir(parents=True, exist_ok=False)
            from deerflow.office.tool_contract import default_tool_manifest
            from deerflow.office.tool_manifest import save_tool_manifest
            manifest = default_tool_manifest(template.tool_groups, template.authority, default_tools=template.default_tools)
            save_tool_manifest(staging, manifest, expected_revision='')
            (staging / '.office-library-tool-v1').write_text('1\n', encoding='ascii')
            _write_yaml(staging / "config.yaml", config)
            (staging / "SOUL.md").write_text(resolved_soul, encoding="utf-8")
            _write_json(staging / "memory.json", create_empty_memory())
            _write_yaml(staging / "office.yaml", office)
            agent_dir.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(agent_dir)
            created_threads.extend(
                await self._ensure_threads_unlocked(
                    employee_id,
                    display_name,
                    is_pm=template.is_pm,
                )
            )
            return self.get_employee_summary(employee_id)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(agent_dir, ignore_errors=True)
            for thread_id in thread_ids:
                self.paths.delete_thread_dir(thread_id, user_id=self.user_id)
                if hasattr(self.checkpointer, "adelete_thread"):
                    await self.checkpointer.adelete_thread(thread_id)
                if thread_id in created_threads:
                    await self.thread_store.delete(thread_id, user_id=self.user_id)
            raise

    def _persisted_employee_ids(self) -> list[str]:
        root = self.paths.user_agents_dir(self.user_id)
        if not root.exists():
            return []
        return [
            child.name
            for child in sorted(root.iterdir())
            if child.is_dir()
            and (child / "office.yaml").is_file()
            and EMPLOYEE_ID_PATTERN.fullmatch(child.name)
        ]

    async def list_employees(self) -> list[dict[str, Any]]:
        return [
            self.get_employee_summary(employee_id)
            for employee_id in self._persisted_employee_ids()
        ]

    def _read_office(self, employee_id: str) -> dict[str, Any]:
        path = self._office_path(employee_id)
        if not path.exists():
            raise FileNotFoundError(f"Employee not found: {employee_id}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _read_config(self, employee_id: str) -> dict[str, Any]:
        config_path = self._agent_dir(employee_id) / "config.yaml"
        if not config_path.is_file():
            raise FileNotFoundError(f"Agent config not found: {employee_id}")
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    def _write_config(self, employee_id: str, config: dict[str, Any]) -> None:
        config_path = self._agent_dir(employee_id) / "config.yaml"
        _write_yaml(config_path, config)

    @staticmethod
    def _read_effective_contract(config: dict[str, Any]) -> dict[str, Any]:
        contract = config.get("runtime_contract") or {}
        authority = normalize_authority(contract.get("authority"))
        return {
            "skills": sorted(contract.get("skills") or []),
            "capabilities": sorted(contract.get("capabilities") or []),
            "permissions": sorted(contract.get("permissions") or []),
            "disabled_capabilities": sorted(contract.get("disabled_capabilities") or []),
            "disabled_tools": sorted(contract.get("disabled_tools") or []),
            "disabled_permissions": sorted(contract.get("disabled_permissions") or []),
            "authority": authority.to_dict(),
        }

    def _upsert_config_list(self, employee_id: str, field: str, value, id_keys: tuple[str, ...]) -> dict[str, Any]:
        config = self._read_config(employee_id)
        items = list(config.get(field) or [])
        if isinstance(value, str):
            if value not in items:
                items.append(value)
            entry = value
        else:
            entry = dict(value)
            entry["enabled"] = bool(entry.get("enabled", True))
            if id_keys:
                manifest_id = _manifest_id(value, *id_keys)
                items = [item for item in items if (isinstance(item, dict) and _manifest_id(item, *id_keys)) != manifest_id]
            items.append(entry)
        config[field] = sorted(items) if all(isinstance(item, str) for item in items) else items
        self._write_config(employee_id, config)
        return entry if isinstance(entry, dict) else {field: entry}

    def _remove_from_config_list(self, employee_id: str, field: str, item_id: str, id_keys: tuple[str, ...]) -> dict[str, Any]:
        config = self._read_config(employee_id)
        items = list(config.get(field) or [])
        if id_keys and all(isinstance(item, dict) for item in items):
            removed = next((item for item in items if _manifest_id(item, *id_keys) == item_id), None)
            if removed is None:
                raise FileNotFoundError(f"Item not found in {field}: {item_id}")
            config[field] = [item for item in items if _manifest_id(item, *id_keys) != item_id]
        else:
            if item_id not in items:
                raise FileNotFoundError(f"Item not found in {field}: {item_id}")
            config[field] = [item for item in items if item != item_id]
            removed = item_id
        self._write_config(employee_id, config)
        return removed if isinstance(removed, dict) else {field: removed}

    def _set_config_list_item_enabled(self, employee_id: str, field: str, item_id: str, enabled: bool, id_keys: tuple[str, ...]) -> dict[str, Any]:
        config = self._read_config(employee_id)
        items = list(config.get(field) or [])
        found = None
        for item in items:
            mid = _manifest_id(item, *id_keys) if id_keys else (item if isinstance(item, str) else "")
            if mid == item_id:
                if isinstance(item, dict):
                    item["enabled"] = bool(enabled)
                found = item
                break
        if found is None:
            raise FileNotFoundError(f"Item not found in {field}: {item_id}")
        config[field] = items
        self._write_config(employee_id, config)
        return dict(found) if isinstance(found, dict) else {field: found}

    def get_employee_summary(self, employee_id: str) -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        office = self._read_office(employee_id)
        projection = office.get("projection") or {}
        enabled = bool(projection.get("enabled", True))
        config = self._read_config(employee_id)
        authority = normalize_authority((config.get("runtime_contract") or {}).get("authority"))
        template = get_employee_template(str(office.get("template_id") or ""))
        return {
            "id": employee_id,
            "templateId": template.template_id,
            "role": template.role,
            "displayName": str(office.get("display_name") or employee_id),
            "avatar": str(office.get("avatar") or ""),
            "modelProfileId": resolve_employee_model(config),
            "workstationId": str(office.get("workstation_id") or ""),
            "isPm": template.is_pm,
            "state": str(projection.get("state") or "idle"),
            "statusDetail": str(
                projection.get("status_detail") or "等待新任务"
            ),
            "lastReport": str(projection.get("last_report") or "暂无报告"),
            "currentTaskId": projection.get("current_task_id"),
            "enabled": enabled,
            "disabledReason": str(projection.get("disabled_reason") or ""),
            "createdAt": int(office.get("created_at") or 0),
            "updatedAt": int(office.get("updated_at") or 0),
            "canDelegate": authority.can_delegate,
        }

    def _save_office(
        self,
        employee_id: str,
        office: dict[str, Any],
        *,
        preserve_runtime_projection: bool = True,
    ) -> None:
        with employee_office_lock(self.user_id, employee_id):
            office_path = self._office_path(employee_id)
            if preserve_runtime_projection and office_path.is_file():
                current = yaml.safe_load(office_path.read_text(encoding="utf-8")) or {}
                current_projection = current.get("projection")
                if isinstance(current_projection, dict):
                    office["projection"] = copy.deepcopy(current_projection)
            office["updated_at"] = _now_ms()
            _write_yaml(office_path, office)

    async def is_employee_working(self, employee_id: str) -> bool:
        employee_id = self.validate_employee_id(employee_id)
        office = self._read_office(employee_id)
        projection = office.get("projection") or {}
        if projection.get("state") == "working":
            return True
        if self.run_manager is None:
            return False
        is_pm = office.get("template_id") == "pm"
        for thread_id in self._thread_ids_for(employee_id, is_pm=is_pm):
            if not hasattr(self.run_manager, "list_by_thread"):
                if await self.run_manager.has_inflight(
                    thread_id,
                    user_id=self.user_id,
                ):
                    return True
                continue
            records = await self.run_manager.list_by_thread(
                thread_id,
                user_id=self.user_id,
                limit=100,
            )
            if any(
                getattr(record.status, "value", record.status)
                in {"pending", "running"}
                or bool(getattr(record, "finalizing", False))
                for record in records
            ):
                return True
        return False

    async def _require_employee_editable(self, employee_id: str) -> None:
        if await self.is_employee_working(employee_id):
            raise OfficeBusyError("员工正在工作暂时不能修改个人信息", code='errors.employee_is_working_personal_information_cannot_be_edited_yet')
        if self.get_employee_summary(employee_id)["state"] != "idle":
            raise OfficeBusyError("只有空闲员工可以修改个人信息", code='errors.only_idle_employees_can_edit_personal_information')

    async def save_personal_info(
        self,
        employee_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        allowed = {"displayName", "prompt", "skills", "modelProfileId"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported employee personal information fields: {sorted(unknown)}")
        async with self._employee_lifecycle(employee_id):
            await self._require_employee_editable(employee_id)
            office = copy.deepcopy(self._read_office(employee_id))
            config = copy.deepcopy(self._read_config(employee_id))
            prompt = None

            if "modelProfileId" in payload:
                selected = payload['modelProfileId']
                if not isinstance(selected, str) or not selected.strip():
                    raise OfficeInputError('请选择有效的员工模型', code='errors.select_a_valid_employee_model')
                selected = self._validate_model_profile(selected, required=True)
                if selected != config.get('model') and self.model_profile_selectable is not None and not self.model_profile_selectable(selected):
                    raise OfficeInputError('模型不存在或不属于当前办公室', code='errors.model_not_found_in_this_office')
                config['model'] = selected
                office['model_profile_id'] = selected

            if "displayName" in payload:
                display_name = str(payload.get("displayName") or "").strip()
                if not display_name:
                    raise ValueError("displayName must be a non-empty string")
                office["display_name"] = display_name
                config["description"] = display_name
            if "prompt" in payload:
                prompt = payload["prompt"]
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("prompt must be a non-empty string")
            if "skills" in payload:
                skills = payload.get("skills")
                if not isinstance(skills, list) or any(
                    not isinstance(item, str) or not item.strip() for item in skills
                ):
                    raise ValueError("skills must be a list of non-empty strings")
                requested = [item.strip() for item in skills]
                effective_skills = list(dict.fromkeys(requested))
                config["skills"] = effective_skills
                config.setdefault("runtime_contract", {})["skills"] = effective_skills

            return self._commit_personal_info(
                employee_id,
                office,
                config=config,
                prompt=prompt,
            )

    def _commit_personal_info(
        self,
        employee_id: str,
        office: dict[str, Any],
        *,
        config: dict[str, Any],
        prompt: str | None = None,
    ) -> dict[str, Any]:
        office_path = self._office_path(employee_id)
        config_path = self._agent_dir(employee_id) / 'config.yaml'
        soul_path = self._agent_dir(employee_id) / "SOUL.md"
        file_state: dict[Path, bytes | None] = {
            path: path.read_bytes() if path.is_file() else None
            for path in (office_path, config_path, soul_path)
        }
        try:
            self._write_config(employee_id, config)
            if prompt is not None:
                soul_path.write_text(prompt.strip() + "\n", encoding="utf-8")
            self._save_office(employee_id, office)
            return {"employee": self.get_employee_summary(employee_id)}
        except Exception as error:
            rollback_errors: list[tuple[Path, Exception]] = []
            for path, previous in file_state.items():
                try:
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(previous)
                except Exception as rollback_error:
                    rollback_errors.append((path, rollback_error))
            if rollback_errors:
                raise RuntimeError(
                    'Employee personal information save failed and rollback was incomplete: '
                    f'{rollback_errors}'
                ) from error
            raise

    def load_agent_config(self, employee_id: str) -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        config_path = self._agent_dir(employee_id) / "config.yaml"
        if not config_path.is_file():
            raise FileNotFoundError(f"Agent config not found: {employee_id}")
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    def disable_employee(self, employee_id: str, reason: str = "") -> dict[str, Any]:
        if self.get_employee_summary(employee_id)["isPm"]:
            raise ValueError("Builtin PM cannot be disabled")
        return self.update_runtime_projection(
            employee_id,
            {"enabled": False, "disabledReason": reason, "state": "disabled"},
        )

    def enable_employee(self, employee_id: str) -> dict[str, Any]:
        return self.update_runtime_projection(
            employee_id,
            {"enabled": True, "disabledReason": "", "state": "idle"},
        )

    def get_capability_index(self, employee_id: str, *, model_name: str | None = None, execution_kind: str = 'direct') -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        office = self._read_office(employee_id)
        config = self._read_config(employee_id)
        contract = self._read_effective_contract(config)
        from deerflow.office.tool_contract import employee_tool_contract, build_tool_catalog
        resolved = employee_tool_contract(self._agent_dir(employee_id), config, model_name=model_name, execution_kind=execution_kind)
        catalog = build_tool_catalog(model_name=model_name or resolve_employee_model(config))
        members = {}
        authorized_groups = set()
        for identity in set(resolved.authorized_ids) | set(resolved.available_ids):
            entry = catalog.get(identity)
            if entry and entry.group:
                authorized_groups.add(entry.group)
                if identity in resolved.available_ids:
                    members.setdefault(entry.group, []).append(entry.name)
        inventory = {
            'authorized_tool_groups': sorted(authorized_groups),
            'available_tool_groups': list(resolved.derived_groups),
            'available_tool_group_members': members,
            'available_tool_names': list(resolved.runtime_names),
            'unavailable_tool_groups': sorted(authorized_groups - set(resolved.derived_groups)),
            'unavailable_reasons': resolved.unavailable_reasons,
        }
        template = get_employee_template(str(office.get("template_id") or ""))
        unavailable_groups = set(inventory['unavailable_tool_groups'])
        unavailable_capabilities: set[str] = set()
        unavailable_permissions: set[str] = set()
        return {
            'employeeId': employee_id,
            'authorizedCapabilities': contract['capabilities'],
            'authorizedToolGroups': inventory['authorized_tool_groups'],
            'authorizedPermissions': contract['permissions'],
            'effectiveCapabilities': sorted(
                set(contract['capabilities']) - unavailable_capabilities
            ),
            'effectiveTools': inventory['available_tool_groups'],
            'toolGroupMembers': inventory['available_tool_group_members'],
            'effectivePermissions': sorted(
                set(contract['permissions']) - unavailable_permissions
            ),
            'runtimeAvailableTools': inventory['available_tool_names'],
            'unavailableAuthorizedToolGroups': inventory['unavailable_tool_groups'],
            'toolAvailabilityReasons': inventory['unavailable_reasons'],
            'disabledCapabilities': contract['disabled_capabilities'],
            'disabledTools': contract['disabled_tools'],
            'disabledPermissions': contract['disabled_permissions'],
            'installedSkills': [{"skillId": s, "enabled": True} for s in contract.get('skills', [])],
            'authorizedTools': [{"toolId": t, "enabled": True} for t in resolved.authorized_ids],
            'toolContract': resolved.snapshot(),
            'templateId': template.template_id,
            'category': template.category,
            'tags': list(template.tags),
            'specialties': list(template.specialties),
            'authority': dict(contract.get('authority') or {}),
        }

    def get_employee_tools(self, employee_id: str) -> dict[str, Any]:
        from deerflow.office.tool_contract import employee_tool_contract, required_tool_names, build_tool_catalog, employee_tool_choices
        employee_id = self.validate_employee_id(employee_id)
        self._read_office(employee_id)
        config = self._read_config(employee_id)
        resolved = employee_tool_contract(self._agent_dir(employee_id), config)
        authority = normalize_authority((config.get('runtime_contract') or {}).get('authority'))
        required = required_tool_names(authority)
        from deerflow.config import get_app_config
        if get_app_config().tool_search.enabled and any(t.startswith('mcp/') for t in resolved.authorized_ids):
            required.add('tool_search')
        return {'tools': list(resolved.authorized_ids), 'revision': resolved.revision,
                'requiredTools': sorted(required),
                'choices': employee_tool_choices(build_tool_catalog(model_name=resolve_employee_model(config)), authority),
                'availableTools': list(resolved.available_ids), 'unavailableTools': resolved.unavailable_reasons}

    async def save_employee_tools(self, employee_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, save_tool_manifest
        from deerflow.office.tool_contract import build_tool_catalog, resolve_employee_tools, validate_tool_discovery_selection, with_delivery_capability, employee_tool_choices
        from deerflow.config import get_app_config
        employee_id = self.validate_employee_id(employee_id)
        if set(payload) != {'tools', 'expectedRevision'} or not isinstance(payload['tools'], list) or not isinstance(payload['expectedRevision'], str):
            raise ValueError('Expected tools list and expectedRevision string')
        manifest = EmployeeToolManifest(1, tuple(payload['tools']))
        manifest = EmployeeToolManifest(1, tuple(sorted(with_delivery_capability(manifest.tools))))
        async with self._employee_lifecycle(employee_id):
            await self._require_employee_editable(employee_id)
            config = self._read_config(employee_id)
            catalog = build_tool_catalog(model_name=resolve_employee_model(config))
            authority = normalize_authority((config.get('runtime_contract') or {}).get('authority'))
            required_members = {name for choice in employee_tool_choices(catalog, authority)
                                if choice['required'] for name in choice['members']}
            manifest = EmployeeToolManifest(1, tuple(sorted(set(manifest.tools) | required_members)))
            current = set(load_tool_manifest(self._agent_dir(employee_id)).tools)
            if unknown := set(manifest.tools) - current - catalog.keys():
                raise ValueError(f'Unknown tools: {sorted(unknown)}')
            deferred_enabled = get_app_config().tool_search.enabled
            validate_tool_discovery_selection(manifest, deferred_enabled=deferred_enabled)
            resolve_employee_tools(manifest, catalog=catalog, deferred_enabled=deferred_enabled,
                authority=authority)
            with employee_office_lock(self.user_id, employee_id):
                save_tool_manifest(self._agent_dir(employee_id), manifest, expected_revision=payload['expectedRevision'])
        return self.get_employee_tools(employee_id)

    def get_employee_detail(self, employee_id: str) -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        office = self._read_office(employee_id)
        index = self.get_capability_index(employee_id)
        config = self._read_config(employee_id)
        authority = normalize_authority((config.get("runtime_contract") or {}).get("authority"))
        return {
            "employee": self.get_employee_summary(employee_id),
            "personalInfo": {
                "displayName": office["display_name"],
                "prompt": (self._agent_dir(employee_id) / "SOUL.md").read_text(encoding="utf-8"),
                "skills": list(config.get("skills") or []),
                "modelProfileId": resolve_employee_model(config),
            },
            "effectivePackage": {
                "agentId": employee_id,
                "displayName": office.get("display_name", employee_id),
                "agentKind": "employee",
                "canDelegate": authority.can_delegate,
                "capabilities": index["effectiveCapabilities"],
                "tools": index["effectiveTools"],
                "toolGroupMembers": index["toolGroupMembers"],
                "permissions": index["effectivePermissions"],
                "ownedDeliverables": [],
            },
            "installedSkills": index["installedSkills"],
            "authorizedTools": index["authorizedTools"],
            "capabilityIndex": index,
        }

    def _write_projection(self, employee_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        with employee_office_lock(self.user_id, employee_id):
            office = self._read_office(employee_id)
            projection = office.setdefault("projection", {})
            normalized = self._normalize_projection_patch(dict(patch))
            field_map = {
                "state": "state",
                "statusDetail": "status_detail",
                "currentTaskId": "current_task_id",
                "lastReport": "last_report",
                "enabled": "enabled",
                "disabledReason": "disabled_reason",
            }
            for source, target in field_map.items():
                if source in normalized:
                    projection[target] = normalized[source]
            self._save_office(
                employee_id,
                office,
                preserve_runtime_projection=False,
            )
        return self.get_employee_summary(employee_id)

    def update_runtime_projection(self, employee_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Update server-owned state derived from the DeerFlow Run lifecycle."""

        return self._write_projection(employee_id, patch)

    @staticmethod
    def _checkpoint_config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    async def _capture_checkpoint(self, thread_id: str) -> Any | None:
        getter = getattr(self.checkpointer, "aget_tuple", None)
        if not callable(getter):
            return None
        return await getter(self._checkpoint_config(thread_id))

    async def _restore_checkpoint(self, thread_id: str, snapshot: Any | None) -> None:
        if snapshot is None:
            return
        checkpoint = dict(snapshot.checkpoint)
        marker = empty_checkpoint()
        checkpoint["id"] = marker["id"]
        checkpoint["ts"] = marker["ts"]
        versions = checkpoint.get("channel_versions")
        restored_config = await self.checkpointer.aput(
            self._checkpoint_config(thread_id),
            checkpoint,
            dict(snapshot.metadata or {}),
            dict(versions) if isinstance(versions, dict) else {},
        )
        pending_writes = list(snapshot.pending_writes or [])
        if not pending_writes or not hasattr(self.checkpointer, "aput_writes"):
            return
        writes_by_task: dict[str, list[tuple[str, Any]]] = defaultdict(list)
        for task_id, channel, value in pending_writes:
            writes_by_task[str(task_id)].append((str(channel), value))
        for task_id, writes in writes_by_task.items():
            await self.checkpointer.aput_writes(restored_config, writes, task_id=task_id)

    async def _restore_thread_meta(self, thread_id: str, snapshot: dict[str, Any] | None) -> None:
        if snapshot is None or await self.thread_store.get(thread_id, user_id=self.user_id):
            return
        await self.thread_store.create(
            thread_id,
            assistant_id=snapshot.get("assistant_id"),
            user_id=snapshot.get("user_id") or self.user_id,
            display_name=snapshot.get("display_name"),
            metadata=dict(snapshot.get("metadata") or {}),
        )
        status = snapshot.get("status")
        if status:
            await self.thread_store.update_status(thread_id, str(status), user_id=self.user_id)

    @staticmethod
    def _write_delete_archive(archive_path: Path, user_dir: Path, staged_paths: list[Path]) -> None:
        with tarfile.open(archive_path, mode="x") as archive:
            for staged_path in staged_paths:
                archive.add(staged_path, arcname=staged_path.relative_to(user_dir).as_posix())

    @staticmethod
    def _restore_delete_archive(archive_path: Path, user_dir: Path) -> None:
        with tarfile.open(archive_path, mode="r") as archive:
            archive.extractall(path=user_dir, filter="data")

    async def _delete_employee(self, employee_id: str, *, allow_active_run: bool) -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        summary = self.get_employee_summary(employee_id)
        if summary["isPm"]:
            raise ValueError("Builtin PM cannot be deleted")
        agent_dir = self._agent_dir(employee_id)
        thread_ids = self._thread_ids_for(employee_id, is_pm=False)
        if not allow_active_run and self.run_manager is not None:
            for thread_id in thread_ids:
                if await self.run_manager.has_inflight(thread_id, user_id=self.user_id):
                    raise RuntimeError(f"Employee has an active run: {employee_id}")

        user_dir = self.paths.user_dir(self.user_id)
        suffix = uuid.uuid4().hex
        staged_agent = agent_dir.with_name(f".delete-agent-{suffix}")
        thread_dirs = {
            thread_id: self.paths.thread_dir(thread_id, user_id=self.user_id)
            for thread_id in thread_ids
        }
        staged_threads = {
            thread_id: thread_dir.with_name(f".delete-thread-{index}-{suffix}")
            for index, (thread_id, thread_dir) in enumerate(thread_dirs.items())
        }
        archive_path = user_dir / f".employee-delete-rollback-{suffix}.tar"
        thread_meta = {
            thread_id: await self.thread_store.get(thread_id, user_id=self.user_id)
            for thread_id in thread_ids
        }
        checkpoints = {
            thread_id: await self._capture_checkpoint(thread_id)
            for thread_id in thread_ids
        }
        archive_ready = False
        staged_thread_ids: list[str] = []
        filesystem_purge_started = False
        deleted_meta_ids: list[str] = []
        deleted_checkpoint_ids: list[str] = []
        try:
            agent_dir.rename(staged_agent)
            staged_paths = [staged_agent]
            for thread_id, thread_dir in thread_dirs.items():
                if thread_dir.exists():
                    staged = staged_threads[thread_id]
                    thread_dir.rename(staged)
                    staged_paths.append(staged)
                    staged_thread_ids.append(thread_id)
            self._write_delete_archive(archive_path, user_dir, staged_paths)
            archive_ready = True

            for thread_id in thread_ids:
                await self.thread_store.delete(thread_id, user_id=self.user_id)
                deleted_meta_ids.append(thread_id)
                if hasattr(self.checkpointer, "adelete_thread"):
                    await self.checkpointer.adelete_thread(thread_id)
                    deleted_checkpoint_ids.append(thread_id)

            filesystem_purge_started = True
            shutil.rmtree(staged_agent)
            for thread_id in staged_thread_ids:
                shutil.rmtree(staged_threads[thread_id])
            archive_path.unlink()
            archive_ready = False
            return summary
        except Exception as error:
            rollback_errors: list[str] = []

            if filesystem_purge_started and archive_ready and archive_path.exists():
                try:
                    self._restore_delete_archive(archive_path, user_dir)
                except Exception as rollback_error:
                    rollback_errors.append(f"filesystem archive restore: {rollback_error}")

            try:
                if not agent_dir.exists():
                    if not staged_agent.exists():
                        raise RuntimeError("agent directory is unavailable for rollback")
                    staged_agent.rename(agent_dir)
                elif staged_agent.exists():
                    raise RuntimeError("agent directory and staged directory both exist")
            except Exception as rollback_error:
                rollback_errors.append(f"agent directory restore: {rollback_error}")

            for thread_id in staged_thread_ids:
                try:
                    thread_dir = thread_dirs[thread_id]
                    staged_thread = staged_threads[thread_id]
                    if not thread_dir.exists():
                        if not staged_thread.exists():
                            raise RuntimeError("thread directory is unavailable for rollback")
                        staged_thread.rename(thread_dir)
                    elif staged_thread.exists():
                        raise RuntimeError("thread directory and staged directory both exist")
                except Exception as rollback_error:
                    rollback_errors.append(f"thread directory restore: {rollback_error}")

            for thread_id in deleted_meta_ids:
                try:
                    await self._restore_thread_meta(thread_id, thread_meta[thread_id])
                except Exception as rollback_error:
                    rollback_errors.append(f"thread metadata restore {thread_id}: {rollback_error}")
            for thread_id in deleted_checkpoint_ids:
                try:
                    await self._restore_checkpoint(thread_id, checkpoints[thread_id])
                except Exception as rollback_error:
                    rollback_errors.append(f"checkpoint restore {thread_id}: {rollback_error}")

            if not rollback_errors and archive_path.exists():
                try:
                    archive_path.unlink()
                except Exception as rollback_error:
                    rollback_errors.append(f"rollback archive cleanup: {rollback_error}")

            if rollback_errors:
                if archive_path.exists():
                    rollback_errors.append(f"rollback archive retained at: {archive_path}")
                details = "; ".join(rollback_errors)
                raise RuntimeError(f"Employee deletion failed and rollback was incomplete: {details}") from error
            raise

    async def delete_employee(self, employee_id: str) -> dict[str, Any]:
        employee_id = self.validate_employee_id(employee_id)
        async with self._employee_lifecycle(employee_id):
            return await self._delete_employee(employee_id, allow_active_run=False)
