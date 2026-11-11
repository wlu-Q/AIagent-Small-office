"""Human decisions for concrete execution requests; no queue or Agent loop."""
import hashlib
import json
import threading
import time
import uuid

import yaml

from deerflow.agents.middlewares.office_round_budget import pause_round_activity


def access_mode(store):
    path = store.paths.user_dir(store.user_id) / 'office-bootstrap.yaml'
    settings = yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else {}
    mode = (settings or {}).get('terminal_access_mode', 'approval')
    if mode not in {'approval', 'full_access'}:
        raise ValueError('Invalid office terminal access mode')
    return mode


class ExecutionApprovals:
    def __init__(self, store):
        self.store = store
        self.instance = uuid.uuid4().hex
        self.closed = threading.Event()
        self.waits = {}
        self.lock = threading.RLock()
        with store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS execution_approvals (
                approval_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL UNIQUE,
                state_json TEXT NOT NULL)''')
            for row in db.execute('SELECT state_json FROM execution_approvals').fetchall():
                value = json.loads(row[0])
                if value['status'] in {'pending', 'approved'}:
                    value.update(status='expired', reason='Gateway restarted; request was not replayed')
                    self._save(db, value)

    @staticmethod
    def _save(db, value):
        db.execute('UPDATE execution_approvals SET state_json=? WHERE approval_id=?',
                   (json.dumps(value, ensure_ascii=False), value['approval_id']))

    def pending(self):
        with self.store._connect() as db:
            values = [json.loads(r[0]) for r in db.execute('SELECT state_json FROM execution_approvals')]
        return sorted((v for v in values if v['status'] == 'pending' and v['expires_at'] > time.time()),
                      key=lambda v: (v['created_at'], v['approval_id']))

    def decide(self, approval_id, request_hash, decision):
        if not isinstance(decision, str) or decision not in {'approve', 'reject'} or not isinstance(request_hash, str):
            raise ValueError('Decision must be approve or reject')
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state_json FROM execution_approvals WHERE approval_id=?', (approval_id,)).fetchone()
            if row is None:
                raise ValueError('Approval does not belong to this user')
            value = json.loads(row[0])
            if value['request_hash'] != request_hash or value['owner_instance'] != self.instance:
                raise ValueError('Approval request changed or expired; refresh the request')
            if value['status'] != 'pending' or value['expires_at'] <= time.time():
                raise ValueError('Approval is no longer pending')
            value.update(status='approved' if decision == 'approve' else 'rejected',
                         decision=decision, decided_at=time.time())
            self._save(db, value)
        return value

    def wait(self, *, execution_id, context, employee_id, command, cwd, request_hash, cancelled):
        # Never reevaluate the mode while waiting: switching to full access must
        # not silently approve an already displayed request.
        identity = hashlib.sha256(f'{self.store.user_id}:{execution_id}'.encode()).hexdigest()
        value = dict(approval_id=identity, execution_id=execution_id, request_hash=request_hash,
                     employee_id=employee_id, command=command, resolved_cwd=cwd,
                     run_id=context['run_id'], native_task_id=context.get('office_native_task_id'),
                     owner_instance=self.instance, created_at=time.time(), expires_at=time.time() + 86400,
                     status='pending', decision=None, decided_at=None, wait_seconds=0)
        with self.store._connect() as db:
            db.execute('INSERT OR IGNORE INTO execution_approvals VALUES (?,?,?)',
                       (identity, execution_id, json.dumps(value, ensure_ascii=False)))
            row = db.execute('SELECT state_json FROM execution_approvals WHERE approval_id=?', (identity,)).fetchone()
            value = json.loads(row[0])
        if value['request_hash'] != request_hash or value['owner_instance'] != self.instance:
            raise PermissionError('Prior approval cannot authorize this execution')
        started = time.monotonic()
        with self.lock:
            self.waits[identity] = started
        try:
            with pause_round_activity():
                self._await_decision(value, identity, cancelled)
        finally:
            with self.lock:
                value['wait_seconds'] = time.monotonic() - started
                with self.store._connect() as db:
                    self._save(db, value)
                self.waits.pop(identity, None)

    def _await_decision(self, value, identity, cancelled):
        while True:
            with self.store._connect() as db:
                value.update(json.loads(db.execute('SELECT state_json FROM execution_approvals WHERE approval_id=?', (identity,)).fetchone()[0]))
            if self.closed.is_set() or cancelled():
                value.update(status='cancelled')
                raise PermissionError('Work cancelled; request was not executed')
            if value['expires_at'] <= time.time():
                value.update(status='expired')
                raise PermissionError('Approval expired; request was not executed')
            if value['status'] == 'approved':
                value.update(status='consumed')
                return
            if value['status'] != 'pending':
                raise PermissionError('User rejected or revoked this request; do not retry it automatically')
            self.closed.wait(.05)

    def waited_seconds(self, run_id, native_task_id):
        with self.lock, self.store._connect() as db:
            values = [json.loads(r[0]) for r in db.execute('SELECT state_json FROM execution_approvals')]
            # One employee executes its tools within its existing native task.
            # Union overlapping waits so parallel tool calls cannot double the budget.
            spans = []
            now = time.monotonic()
            for value in values:
                if value['run_id'] != run_id or value.get('native_task_id') != native_task_id:
                    continue
                end = value['created_at'] + value.get('wait_seconds', 0)
                if value['approval_id'] in self.waits:
                    end = value['created_at'] + now - self.waits[value['approval_id']]
                spans.append((value['created_at'], end))
            total, last = 0, float('-inf')
            for start, end in sorted(spans):
                total += max(0, end - max(last, start))
                last = max(last, end)
            return total
