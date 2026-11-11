"""Legacy history parsing and explicit retirement. No stage execution or creation."""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from deerflow.config.paths import Paths


def normalize_file_scope(values) -> tuple[str, ...]:
    """Normalize approved project-relative files/directories in historical records."""
    result: list[str] = []
    for value in values or ():
        item = str(value or '').strip().replace('\\', '/')
        while item.startswith('./'):
            item = item[2:]
        item = item.strip('/')
        if not item or item == '.' or item.startswith('../') or '/..' in item or ':' in item:
            raise ValueError(f"invalid project file scope: {value!r}")
        if item not in result:
            result.append(item)
    return tuple(result)


class WorkflowStatus(StrEnum):
    ACTIVE = "active"
    WAITING_FOR_USER = "waiting_for_user"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStage(StrEnum):
    REQUIREMENT_ANALYSIS = "requirement_analysis"
    WAIT_REQUIREMENT_CONFIRMATION = "wait_requirement_confirmation"
    ARCHITECTURE_DESIGN = "architecture_design"
    WAIT_ARCHITECTURE_CONFIRMATION = "wait_architecture_confirmation"
    TASK_ASSIGNMENT = "task_assignment"
    WAIT_TASK_CONFIRMATION = "wait_task_confirmation"
    EXECUTING_EMPLOYEES = "executing_employees"
    QUALITY_CHECKING = "quality_checking"
    DOCUMENTING = "documenting"
    WAIT_DELIVERY_CONFIRMATION = "wait_delivery_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class WorkflowTask:
    task_id: str
    employee_id: str
    task: str
    dependencies: tuple[str, ...] = ()
    parallel: bool = False
    status: str = "proposed"
    run_id: str | None = None
    acceptance_criteria: tuple[str, ...] = ()
    # Historical scope only; current execution never uses this as write authority.
    file_scope: tuple[str, ...] = ()
    # Durable evidence captured from the native run. Empty means this historical
    # task cannot be reused as completed work.
    evidence: tuple[dict[str, Any], ...] = ()
    kind: str = 'implementation'

    def __post_init__(self) -> None:
        if self.kind not in {'implementation', 'test', 'review', 'documentation'}:
            raise ValueError('Invalid workflow task kind')
        for name in ("task_id", "employee_id", "task"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if type(self.parallel) is not bool:
            raise ValueError("parallel must be a boolean")
        for values in (self.dependencies, self.acceptance_criteria, self.file_scope):
            if not isinstance(values, tuple) or any(not isinstance(x, str) or not x.strip() for x in values):
                raise ValueError("task lists must contain non-empty strings")
        normalize_file_scope(self.file_scope)
        if not isinstance(self.evidence, tuple) or any(not isinstance(item, dict) for item in self.evidence):
            raise ValueError("task evidence must be dictionaries")


@dataclass(frozen=True, slots=True)
class WorkflowRunLink:
    task_id: str
    employee_id: str
    run_id: str
    status: str = "pending"
    native_task_id: str | None = None


@dataclass(frozen=True, slots=True)
class OfficeWorkflow:
    workflow_id: str
    user_id: str
    project_id: str
    pm_employee_id: str = "pm"
    current_stage: WorkflowStage = WorkflowStage.REQUIREMENT_ANALYSIS
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    requirement: dict[str, Any] = field(default_factory=dict)
    architecture: dict[str, Any] = field(default_factory=dict)
    task_plan: tuple[WorkflowTask, ...] = ()
    preselected_employee_ids: tuple[str, ...] = ()
    run_links: tuple[WorkflowRunLink, ...] = ()
    quality: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    knowledge_facts: tuple[dict[str, Any], ...] = ()
    last_decision: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0
    revision: int = 0
    phase_runs: tuple[dict[str, Any], ...] = ()
    execution_epoch: int = 0
    execution_error: str = ''
    paused_status: str = ''

    def __post_init__(self) -> None:
        for name in ("workflow_id", "user_id", "project_id", "pm_employee_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if any(type(x) is not int or x < 0 for x in (self.created_at, self.updated_at, self.revision)):
            raise ValueError("workflow timestamps must be non-negative")
        if not isinstance(self.current_stage, WorkflowStage) or not isinstance(self.status, WorkflowStatus):
            raise ValueError("Invalid workflow stage/status")
        if not isinstance(self.preselected_employee_ids, tuple) or any(not isinstance(item, str) or not item.strip() for item in self.preselected_employee_ids):
            raise ValueError("Invalid preselected employees")
        if not isinstance(self.knowledge_facts, tuple) or any(not isinstance(item, dict) for item in self.knowledge_facts):
            raise ValueError("Invalid workflow knowledge facts")

    def to_record(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "pm_employee_id": self.pm_employee_id,
            "current_stage": self.current_stage.value,
            "status": self.status.value,
            "requirement": self.requirement,
            "architecture": self.architecture,
            "task_plan": [asdict(item) for item in self.task_plan],
            "preselected_employee_ids": list(self.preselected_employee_ids),
            "run_links": [asdict(item) for item in self.run_links],
            "quality": self.quality,
            "artifacts": list(self.artifacts),
            "knowledge_facts": list(self.knowledge_facts),
            "last_decision": self.last_decision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "phase_runs": list(self.phase_runs),
            "execution_epoch": self.execution_epoch,
            "execution_error": self.execution_error,
            "paused_status": self.paused_status,
        }


def _task_from(value: Any) -> WorkflowTask:
    if not isinstance(value, dict):
        raise ValueError("workflow task is invalid")
    return WorkflowTask(
        task_id=str(value.get("task_id", "")).strip(),
        employee_id=str(value.get("employee_id", "")).strip(),
        task=str(value.get("task", "")).strip(),
        dependencies=tuple(str(item).strip() for item in value.get("dependencies", ())),
        parallel=value.get("parallel", False),
        status=str(value.get("status", "proposed")),
        run_id=str(value["run_id"]).strip() if value.get("run_id") else None,
        acceptance_criteria=tuple(str(item).strip() for item in value.get("acceptance_criteria", ())),
        file_scope=tuple(str(item).strip() for item in value.get("file_scope", ())),
        evidence=tuple(dict(item) for item in value.get('evidence', ()) if isinstance(item, dict)),
        kind=value.get('kind', 'implementation'),
    )


def _workflow_from_payload(payload: dict[str, Any]) -> OfficeWorkflow:
    return OfficeWorkflow(
        workflow_id=str(payload["workflow_id"]),
        user_id=str(payload["user_id"]),
        project_id=str(payload["project_id"]),
        pm_employee_id=str(payload.get("pm_employee_id", "pm")),
        current_stage=WorkflowStage(payload.get("current_stage", WorkflowStage.REQUIREMENT_ANALYSIS.value)),
        status=WorkflowStatus(payload.get("status", WorkflowStatus.ACTIVE.value)),
        requirement=dict(payload.get("requirement") or {}),
        architecture=dict(payload.get("architecture") or {}),
        task_plan=tuple(_task_from(item) for item in payload.get("task_plan", ())),
        preselected_employee_ids=tuple(payload.get("preselected_employee_ids", ())),
        run_links=tuple(WorkflowRunLink(**item) for item in payload.get("run_links", ())),
        quality=dict(payload.get("quality") or {}),
        artifacts=tuple(dict(item) for item in payload.get("artifacts", ())),
        knowledge_facts=tuple(dict(item) for item in payload.get("knowledge_facts", ())),
        last_decision=dict(payload.get("last_decision") or {}),
        created_at=int(payload.get("created_at", 0)),
        updated_at=int(payload.get("updated_at", 0)),
        revision=payload.get("revision", 0),
        phase_runs=tuple(payload.get('phase_runs', ())),
        execution_epoch=payload.get('execution_epoch', 0),
        execution_error=payload.get('execution_error', ''),
        paused_status=payload.get('paused_status', ''),
    )


class WorkflowStore:
    def __init__(self, *, paths: Paths, user_id: str, now: Callable[[], int] | None = None) -> None:
        self.user_id = str(user_id or "").strip()
        if not self.user_id:
            raise ValueError("user_id is required")
        self.path = paths.user_dir(self.user_id) / "office-workflows.v1.sqlite3"
        self._now = now or (lambda: int(time.time() * 1000))

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        mode = "rw" if write else "ro"
        connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode={mode}", uri=True, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def get(self, workflow_id: str, *, user_id: str | None = None, project_id: str | None = None) -> OfficeWorkflow | None:
        if user_id is not None and user_id != self.user_id:
            return None
        if not self.path.exists():
            return None
        owner = self.user_id
        query = "SELECT payload_json FROM workflows WHERE workflow_id = ? AND user_id = ?"
        params: list[Any] = [workflow_id, owner]
        if project_id is not None:
            query += " AND project_id = ?"
            params.append(str(project_id).strip())
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return _workflow_from_payload(json.loads(row["payload_json"])) if row else None

    def list(self, *, project_id: str | None = None, include_terminal: bool = True) -> tuple[OfficeWorkflow, ...]:
        if not self.path.exists():
            return ()
        query = "SELECT payload_json FROM workflows WHERE user_id = ?"
        params: list[Any] = [self.user_id]
        if project_id is not None:
            query += " AND project_id = ?"
            params.append(str(project_id).strip())
        if not include_terminal:
            query += " AND json_extract(payload_json, '$.status') NOT IN (?, ?, ?)"
            params.extend([WorkflowStatus.COMPLETED.value, WorkflowStatus.FAILED.value, WorkflowStatus.CANCELLED.value])
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_workflow_from_payload(json.loads(row["payload_json"])) for row in rows)

    def current(self, *, project_id: str) -> OfficeWorkflow | None:
        return next(iter(self.list(project_id=project_id, include_terminal=False)), None)

    def clear(self, *, project_id: str) -> int:
        """Delete every workflow and recorded result receipt for one project.

        Legacy result receipts belong to the same project history. Returns
        the number of workflows deleted.
        """
        normalized = str(project_id or "").strip()
        if not normalized:
            raise ValueError("project_id is required")
        if not self.path.exists():
            return 0
        with self._connect(write=True) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM workflow_results WHERE workflow_id IN (SELECT workflow_id FROM workflows WHERE user_id = ? AND project_id = ?)",
                    (self.user_id, normalized),
                )
                deleted = connection.execute(
                    "DELETE FROM workflows WHERE user_id = ? AND project_id = ?",
                    (self.user_id, normalized),
                ).rowcount
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return int(deleted or 0)

    def retire(self, workflow_id: str, *, project_id: str) -> OfficeWorkflow:
        """Explicitly retire legacy work, preserving the original payload and sources."""
        if not self.path.exists():
            raise ValueError("Workflow not found in current user/project")
        with self._connect(write=True) as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT payload_json FROM workflows WHERE workflow_id=? AND user_id=? AND project_id=?',
                (workflow_id, self.user_id, project_id),
            ).fetchone()
            if row is None:
                raise ValueError('Workflow not found in current user/project')
            payload = json.loads(row['payload_json'])
            if payload.get('status') not in {'cancelled', 'completed', 'failed'}:
                payload.update(status='cancelled', current_stage='cancelled', paused_status='', execution_error='',
                               updated_at=max(int(self._now()), int(payload.get('updated_at', 0)) + 1))
                for task in payload.get('task_plan', []):
                    if task.get('status') in {'queued', 'running'}:
                        task['status'] = 'interrupted'
                connection.execute('UPDATE workflows SET payload_json=?, updated_at=? WHERE workflow_id=? AND user_id=? AND project_id=?',
                                   (json.dumps(payload, ensure_ascii=False), payload['updated_at'], workflow_id, self.user_id, project_id))
            retired = _workflow_from_payload(payload)
            connection.commit()
            return retired
