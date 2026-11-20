"""Stage-independent execution identities; Run/Checkpoint remain the owners of execution.

No scheduler, completion policy or duplicate copy of human questions lives here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from typing import Any


def _required(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


@dataclass(frozen=True, slots=True)
class WorkIdentity:
    user_id: str
    project_id: str | None
    thread_id: str
    work_id: str

    def __post_init__(self) -> None:
        for name in ("user_id", "thread_id", "work_id"):
            _required(getattr(self, name), name)
        if self.project_id is not None:
            _required(self.project_id, "project_id")

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> WorkIdentity:
        if not isinstance(value, Mapping):
            raise ValueError("Work identity must be a mapping")
        return cls(**{key: value.get(key) for key in ("user_id", "project_id", "thread_id", "work_id")})


@dataclass(frozen=True, slots=True)
class DelegationIdentity:
    """One persisted AI tool call, independent of the Run that replays it.

    A new model call is a new delegation. Text similarity is never deduplication.
    """

    work: WorkIdentity
    ai_message_id: str
    tool_call_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.work, WorkIdentity):
            raise ValueError("Work identity is required")
        _required(self.ai_message_id, "ai_message_id")
        _required(self.tool_call_id, "tool_call_id")

    @property
    def key(self) -> str:
        raw = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DelegationReceipt:
    receipt_key: str
    employee_id: str
    request_hash: str
    parent_run_id: str
    native_task_id: str | None
    input_ref: dict
    status: str
    result_ref: dict | None
    evidence: list
    updated_at: int = 0

    def task_result(self) -> dict:
        from deerflow.office.delegation_memory import delegation_observations

        result = dict(self.result_ref or {})
        result.update(execution_status=self.status, partial=self.status != 'succeeded' or bool(result.get('stop_reason')))
        observations = [item for item in delegation_observations(self.evidence)
                        if not str(item['tool']).startswith('powershell_') and item['tool'] != 'publish_task_result']
        if observations:
            result['tool_observations'] = observations[-6:]
            result['omitted_tool_observations'] = len(observations) - len(result['tool_observations'])
        return result


class ExecutionReceiptStore:
    """Durable tool-call receipts, not a task planner or scheduler."""

    TERMINAL = frozenset({'succeeded', 'failed', 'interrupted', 'timeout', 'unknown'})
    FILENAME = 'office-execution-receipts.v1.sqlite3'

    def __init__(self, *, paths, user_id: str):
        _required(user_id, 'user_id')
        self.user_id = user_id
        self.paths = paths
        self.path = paths.user_dir(user_id) / self.FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS delegation_receipts (
                receipt_key TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT,
                thread_id TEXT NOT NULL, work_id TEXT NOT NULL, ai_message_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL, employee_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                parent_run_id TEXT NOT NULL, native_task_id TEXT, input_ref_json TEXT NOT NULL,
                status TEXT NOT NULL, result_ref_json TEXT, evidence_json TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS receipt_work ON delegation_receipts(user_id, project_id, work_id);''')
            db.executescript('''CREATE TABLE IF NOT EXISTS execution_workspaces (
                workspace_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                state_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspace_uses (
                use_key TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
                project_id TEXT NOT NULL, thread_id TEXT NOT NULL, work_id TEXT NOT NULL,
                employee_id TEXT NOT NULL, owner_key TEXT NOT NULL, selection_json TEXT NOT NULL,
                run_id TEXT);
                CREATE INDEX IF NOT EXISTS workspace_use_work ON workspace_uses(user_id, project_id, work_id);''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _check(self, identity):
        if identity.work.user_id != self.user_id:
            raise ValueError('Receipt user mismatch')

    def recent_employee_receipts(self, *, employee_id: str, project_id: str | None) -> list[DelegationReceipt]:
        _required(employee_id, 'employee_id')
        with self._connect() as db:
            rows = db.execute(
                'SELECT * FROM delegation_receipts WHERE user_id=? AND employee_id=? '
                'AND project_id IS ? ORDER BY updated_at DESC, receipt_key DESC LIMIT 3',
                (self.user_id, employee_id, project_id),
            ).fetchall()
        return [self._receipt(row) for row in rows]

    @classmethod
    def work_log_receipts(cls, paths, user_id, employee_id, day=None):
        path = paths.user_dir(user_id) / cls.FILENAME
        if not path.is_file():
            return []
        local_day = "date(updated_at / 1000, 'unixepoch', 'localtime')"
        fields = ('receipt_key, thread_id, parent_run_id, native_task_id, status, updated_at, '
                  "json_extract(input_ref_json, '$.task') AS task, result_ref_json, evidence_json"
                  if day else f'{local_day} AS day, count(*) AS count')
        sql = (f'SELECT {fields} FROM delegation_receipts WHERE user_id=? AND employee_id=? '
               "AND status IN ('succeeded','failed','timeout','interrupted')")
        params = [user_id, employee_id]
        if day:
            sql += f' AND {local_day}=?'
            params.append(day.isoformat())
        else:
            sql += ' GROUP BY day'
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, params)]

    @staticmethod
    def _receipt(row):
        return DelegationReceipt(row['receipt_key'], row['employee_id'], row['request_hash'], row['parent_run_id'],
                                 row['native_task_id'], json.loads(row['input_ref_json']), row['status'],
                                 json.loads(row['result_ref_json']) if row['result_ref_json'] else None,
                                 json.loads(row['evidence_json']), row['updated_at'])

    def get(self, identity):
        self._check(identity)
        with self._connect() as db:
            row = db.execute('SELECT * FROM delegation_receipts WHERE receipt_key=? AND user_id=?', (identity.key, self.user_id)).fetchone()
            return self._receipt(row) if row else None

    def terminal_call_receipts(self, *, project_id, thread_id, run_ids):
        if not run_ids:
            return {}
        placeholders = ','.join('?' for _ in run_ids)
        with self._connect() as db:
            rows = db.execute(
                f'SELECT * FROM delegation_receipts '
                f'WHERE user_id=? AND project_id IS ? AND thread_id=? AND parent_run_id IN ({placeholders})',
                (self.user_id, project_id, thread_id, *run_ids),
            ).fetchall()
        return {(row['parent_run_id'], row['tool_call_id']): self._receipt(row) for row in rows if row['status'] in self.TERMINAL}

    def claim(self, identity, *, employee_id, request_hash, parent_run_id, input_ref):
        self._check(identity)
        for name, value in [('employee_id', employee_id), ('request_hash', request_hash), ('parent_run_id', parent_run_id)]:
            _required(value, name)
        if not isinstance(input_ref, dict):
            raise ValueError('Receipt input_ref must be an object')
        now = int(time.time() * 1000)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('INSERT OR IGNORE INTO delegation_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (identity.key, self.user_id, identity.work.project_id, identity.work.thread_id, identity.work.work_id,
                                identity.ai_message_id, identity.tool_call_id, employee_id, request_hash, parent_run_id, None,
                                json.dumps(input_ref, ensure_ascii=False), 'claimed', None, '[]', now, now))
            row = db.execute('SELECT * FROM delegation_receipts WHERE receipt_key=?', (identity.key,)).fetchone()
            if row['employee_id'] != employee_id or row['request_hash'] != request_hash:
                raise ValueError('Delegation identity already belongs to a different request')
            return self._receipt(row), cursor.rowcount == 1

    def observe(self, identity, *, parent_run_id, native_task_id, status, result_ref, evidence):
        self._check(identity)
        _required(parent_run_id, 'parent_run_id')
        _required(native_task_id, 'native_task_id')
        if not isinstance(evidence, list) or (result_ref is not None and not isinstance(result_ref, dict)):
            raise ValueError('Receipt result/evidence must be structured executor data')
        if status not in self.TERMINAL | {'running'}:
            raise ValueError('Invalid receipt status')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM delegation_receipts WHERE receipt_key=?', (identity.key,)).fetchone()
            if row is None or row['parent_run_id'] != parent_run_id or row['native_task_id'] not in (None, native_task_id):
                raise ValueError('Only the original delegation executor may observe this receipt')
            receipt = self._receipt(row)
            if receipt.status in self.TERMINAL:
                if (receipt.status, receipt.result_ref, receipt.evidence) != (status, result_ref, evidence):
                    raise ValueError('Conflicting terminal delegation result')
                return receipt
            db.execute('UPDATE delegation_receipts SET native_task_id=?,status=?,result_ref_json=?,evidence_json=?,updated_at=? WHERE receipt_key=?',
                       (native_task_id, status, json.dumps(result_ref, ensure_ascii=False) if result_ref is not None else None,
                        json.dumps(evidence, ensure_ascii=False), int(time.time() * 1000), identity.key))
            return self._receipt(db.execute('SELECT * FROM delegation_receipts WHERE receipt_key=?', (identity.key,)).fetchone())

    def mark_unresolved_unknown(self):
        """Startup only, before admission: an absent native handle is not permission to replay."""
        with self._connect() as db:
            db.execute("UPDATE delegation_receipts SET status='unknown',updated_at=? WHERE user_id=? AND status IN ('claimed','running')",
                       (int(time.time() * 1000), self.user_id))


@dataclass(frozen=True, slots=True)
class PendingAsk:
    """Projection of a validated native interrupt, never a persisted ask protocol."""

    work: WorkIdentity
    employee_id: str
    source_run_id: str
    request_id: str

    @classmethod
    def from_interrupt(cls, *, work: WorkIdentity, record: Any, payload: Mapping[str, Any]) -> PendingAsk:
        if record.user_id != work.user_id or record.thread_id != work.thread_id or record.status != "interrupted":
            raise ValueError("Pending ask does not belong to this interrupted work")
        if not isinstance(payload, Mapping) or payload.get("kind") != "human_input_request" or payload.get("source") != "ask_clarification":
            raise ValueError("A native ask interrupt is required")
        for name, value in (("request_id", payload.get("request_id")), ("run_id", record.run_id), ("employee_id", record.assistant_id)):
            _required(value, name)
        return cls(work, record.assistant_id, record.run_id, payload["request_id"])
