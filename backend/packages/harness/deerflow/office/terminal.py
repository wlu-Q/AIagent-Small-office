"""Owned process sessions in the existing execution database; no Agent loop.

Office mode and concrete approvals are read from server-owned persistent state.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

from deerflow.sandbox.env_policy import build_sandbox_env
from deerflow.sandbox.local.managed_command import ManagedProcess

ACTIVE = {'awaiting_approval', 'starting', 'running'}
RETRYABLE = {'failed', 'timeout'}
_managers = {}
_managers_lock = threading.RLock()


def authorize_execution(context, command, cwd):
    from deerflow.office.execution_workspace import execution_environment
    from deerflow.office.execution_approvals import access_mode
    if not context.get('office_work') or not (context.get('agent_name') or context.get('office_employee_id')):
        raise PermissionError('Terminal requires an authorized employee execution context')
    owned = execution_environment(context)
    if owned[2]['employee_id'] == 'pm':
        raise PermissionError('PM terminal execution is not enabled')
    return access_mode(owned[0].store)


def execution_scope(environment):
    return environment['workspace_id'] or f"project:{environment['project_id']}"


def powershell_executable():
    # Pin an absolute executable for each manager; never use LocalSandbox's sh
    # preference or a project-relative executable. 7.4 has stable native errors.
    raw = os.environ.get('OFFICE_POWERSHELL_EXECUTABLE') or shutil.which('pwsh')
    if not raw or not Path(raw).is_absolute() or not Path(raw).is_file():
        raise RuntimeError('Install PowerShell 7.4+ or set OFFICE_POWERSHELL_EXECUTABLE to its absolute path')
    path = str(Path(raw).resolve())
    result = subprocess.run([path, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', '$PSVersionTable.PSVersion.ToString()'],
        env=build_sandbox_env(), capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or tuple(int(v) for v in result.stdout.strip().split('.')[:2]) < (7, 4):
        raise RuntimeError('Office terminal requires PowerShell 7.4+ for native exit-code handling')
    return path, result.stdout.strip()


def resolve_cwd(root, relative):
    root = Path(root).resolve()
    relative = relative or '.'
    path = Path(relative)
    if path.is_absolute() or path.drive or '..' in path.parts or ':' in relative:
        raise ValueError('cwd must be a relative directory inside this task workspace')
    candidate = root / path
    if not candidate.is_dir() or not candidate.resolve().is_relative_to(root):
        raise ValueError('cwd does not exist or escapes this task workspace')
    if any(p.is_symlink() or p.is_junction() for p in (candidate, *candidate.parents) if p != root.parent):
        raise ValueError('Terminal cwd cannot follow symbolic links or junctions')
    if '.git' in {p.lower() for p in path.parts}:
        raise ValueError('Git metadata is not a command working directory')
    return str(candidate.resolve())


def manager_for(store):
    with _managers_lock:
        key = str(store.path.resolve())
        if key not in _managers:
            _managers[key] = TerminalManager(store)
        return _managers[key]


def close_terminals():
    with _managers_lock:
        managers = list(_managers.values())
    for manager in managers:
        manager.close()
    with _managers_lock:
        _managers.clear()


atexit.register(close_terminals)


class TerminalManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.changed = threading.Condition()
        self.sessions = {}
        self.instance = uuid.uuid4().hex
        self.shell = None
        self.closed = False
        from deerflow.office.execution_approvals import ExecutionApprovals
        self.approvals = ExecutionApprovals(store)
        with store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS command_executions (
                execution_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                call_key TEXT NOT NULL UNIQUE, state_json TEXT NOT NULL)''')
            # Old Job Object owner died. Preserve unknown outcome and old logs;
            # do not kill a persisted PID or replay a possibly executed command.
            for row in db.execute('SELECT * FROM command_executions').fetchall():
                value = json.loads(row['state_json'])
                if value['status'] in ACTIVE:
                    value.update(status='unknown', error='Gateway restarted; execution outcome requires inspection')
                    self._save(db, value)

    @staticmethod
    def _save(db, value):
        db.execute('UPDATE command_executions SET state_json=? WHERE execution_id=?', (json.dumps(value), value['execution_id']))

    def _get(self, execution_id, workspace_id):
        with self.store._connect() as db:
            row = db.execute('SELECT state_json FROM command_executions WHERE execution_id=? AND workspace_id=?',
                             (execution_id, workspace_id)).fetchone()
        if not row:
            raise PermissionError('Execution does not belong to this task')
        return json.loads(row[0])

    def run(self, *, workspace, context, tool_call_id, command, cwd=None, yield_ms=None,
            timeout_seconds=None, purpose='command', retry_of=None, sanitize=lambda s: s, secret_values=()):
        if not isinstance(command, str) or not command.strip() or len(command.encode('utf-8')) > 16000 or '\x00' in command:
            raise ValueError('command must contain 1–16000 UTF-8 bytes without NUL')
        if yield_ms is None:
            yield_ms = 1000 if purpose == 'preview_service' else 30000
        if purpose not in {'command', 'preview_service'} or type(yield_ms) is not int or not 0 <= yield_ms <= 120000:
            raise ValueError('Invalid purpose or yield_ms (0–120000)')
        timeout_seconds = timeout_seconds if timeout_seconds is not None else (3600 if purpose == 'preview_service' else 600)
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 86400:
            raise ValueError('timeout_seconds must be positive and at most 86400')
        if not tool_call_id or not context.get('run_id'):
            raise ValueError('Trusted Run and tool call identity are required')
        wid = execution_scope(workspace)
        cwd = resolve_cwd(workspace['root_path'], cwd)
        mode = authorize_execution(context, command, cwd)
        if mode not in {'approval', 'full_access'}:
            raise PermissionError('No office execution authorization')
        key = hashlib.sha256(f'{wid}:{context["run_id"]}:{context.get("office_native_task_id")}:{tool_call_id}'.encode()).hexdigest()
        request_hash = hashlib.sha256(json.dumps([command, cwd, purpose, timeout_seconds, retry_of]).encode()).hexdigest()
        with self.lock:
            if self.closed:
                raise RuntimeError('Terminal manager is shutting down')
            abort = context.get('abort_event')
            if abort and abort.is_set():
                raise ValueError('Work cancelled; command was not started')
            if self.shell is None:
                self.shell = powershell_executable()
            with self.store._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT state_json FROM command_executions WHERE call_key=?', (key,)).fetchone()
                if row:
                    old = json.loads(row[0])
                    if old['request_hash'] != request_hash:
                        raise ValueError('Existing tool call has different command arguments')
                    return self.read(old['execution_id'], wid, wait_ms=0)
                if sum(not s['done'].is_set() for s in self.sessions.values()) >= 16:
                    raise ValueError('Too many active terminal sessions; inspect or stop existing sessions')
                execution_id = uuid.uuid4().hex
                attempt, root_id = 0, execution_id
                if retry_of:
                    prior = self._get(retry_of, wid)
                    if prior['status'] not in RETRYABLE:
                        raise ValueError('Only a known failed or timed-out execution can be retried')
                    root_id = prior['retry_root_id']
                    chain = [json.loads(r[0]) for r in db.execute('SELECT state_json FROM command_executions WHERE workspace_id=?', (wid,))]
                    chain = [v for v in chain if v['retry_root_id'] == root_id]
                    latest = max(chain, key=lambda v: v['attempt_index'])
                    if latest['execution_id'] != retry_of or latest['attempt_index'] >= 5:
                        raise ValueError('Retry must reference the latest failure; at most five retries are permitted')
                    attempt = latest['attempt_index'] + 1
                value = dict(execution_id=execution_id, workspace_id=workspace['workspace_id'], user_id=self.store.user_id,
                    project_id=workspace['project_id'], scope_id=wid,
                    employee_id=context.get('agent_name') or context['office_employee_id'],
                    work_id=context['office_work']['work_id'], run_id=context['run_id'],
                    native_task_id=context.get('office_native_task_id'), tool_call_id=tool_call_id,
                    request_hash=request_hash, command=sanitize(command), resolved_cwd=cwd, purpose=purpose,
                    status='awaiting_approval' if mode == 'approval' else 'starting', exit_code=None, stdout='', stderr='', retry_root_id=root_id,
                    attempt_index=attempt, retries_remaining=5-attempt, process_identity=None,
                    created_at=time.time(), shell_path=self.shell[0], shell_version=self.shell[1])
                if purpose == 'preview_service':
                    # An ephemeral candidate, not a port reservation or readiness
                    # guarantee. Bind failures must be handled as real failures.
                    with socket.socket() as candidate:
                        candidate.bind(('127.0.0.1', 0))
                        value['preview_port'] = candidate.getsockname()[1]
                db.execute('INSERT INTO command_executions VALUES (?,?,?,?)', (execution_id, wid, key, json.dumps(value)))
            session = dict(value=value, done=threading.Event(), stop=threading.Event(), process=None, sanitize=sanitize)
            self.sessions[execution_id] = session
        # Human waiting owns the existing Run/employee lease, but neither the
        # manager lock nor an execution deadline. Other employees can proceed.
        if mode == 'approval':
            try:
                self.approvals.wait(execution_id=execution_id, context=context, employee_id=workspace['employee_id'],
                    command=value['command'], cwd=cwd, request_hash=request_hash,
                    cancelled=lambda: session['stop'].is_set() or bool(abort and abort.is_set()))
            except PermissionError as exc:
                with self.lock, self.store._connect() as db:
                    value.update(status='cancelled' if session['stop'].is_set() or (abort and abort.is_set()) else 'rejected', error=str(exc))
                    self._save(db, value)
                    session['done'].set()
                    self.sessions.pop(execution_id, None)
                return self.read(execution_id, wid, wait_ms=0)
        with self.lock:
            try:
                if self.closed or session['stop'].is_set() or (abort and abort.is_set()):
                    raise PermissionError('Work cancelled before process start')
                resolve_cwd(workspace['root_path'], str(Path(cwd).relative_to(workspace['root_path'])))
                script = "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$false; "
                script += '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $OutputEncoding=[Console]::OutputEncoding; '
                # Finish PowerShell object formatting before exit. Otherwise
                # e.g. Get-Location/Format-Table can silently lose their output.
                script += "try { & {\n" + command + "\n} | Out-Default; if ($LASTEXITCODE) { exit $LASTEXITCODE }; exit 0 } catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }"
                args = [self.shell[0], '-NoLogo', '-NoProfile', '-NonInteractive', '-OutputFormat', 'Text', '-EncodedCommand', base64.b64encode(script.encode('utf-16-le')).decode()]
                environment = build_sandbox_env()
                environment['JAVA_TOOL_OPTIONS'] = (environment.get('JAVA_TOOL_OPTIONS', '')
                    + ' -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8').strip()
                skills_dir = self.store.paths.user_custom_skills_dir(self.store.user_id)
                skills_dir.mkdir(parents=True, exist_ok=True)
                environment['OFFICE_SKILLS_DIR'] = str(skills_dir.resolve())
                if purpose == 'preview_service':
                    environment['OFFICE_PREVIEW_PORT'] = str(value['preview_port'])
                process = ManagedProcess(args, cwd=cwd, env=environment, secret_values=secret_values, tail_bytes=16000)
                session['process'] = process
                value.update(status='running', process_identity=dict(pid=process.process.pid, job_name=process.name,
                    owner_instance=self.instance, started_at=time.time()))
                with self.store._connect() as db:
                    self._save(db, value)
                threading.Thread(target=self._watch, args=(session, abort, timeout_seconds), daemon=True).start()
            except Exception as exc:
                if session['process']:
                    session['process'].close()
                value.update(status='cancelled' if isinstance(exc, PermissionError) else 'failed', error=sanitize(str(exc)))
                with self.store._connect() as db:
                    self._save(db, value)
                session['done'].set()
                self.sessions.pop(execution_id, None)
        session['done'].wait(yield_ms / 1000)
        return self.read(execution_id, wid, wait_ms=0)

    def _watch(self, session, abort, timeout):
        process, value = session['process'], session['value']
        deadline, last_save = time.monotonic() + timeout, 0
        try:
            while not process.done():
                reason = ('cancelled' if session['stop'].is_set() or (abort and abort.is_set())
                          else 'timeout' if time.monotonic() >= deadline else None)
                if reason:
                    value['status'] = reason
                    break
                if time.monotonic() - last_save >= 0.5:
                    self._capture(session)
                    last_save = time.monotonic()
                time.sleep(0.025)
            if value['status'] == 'running':
                value['exit_code'] = process.process.returncode
                value['status'] = 'succeeded' if value['exit_code'] == 0 else 'failed'
        except Exception as exc:
            value.update(status='unknown', error=session['sanitize'](str(exc)))
        finally:
            try:
                process.close()
                value['exit_code'] = process.process.returncode
                self._capture(session, final=True)
            except Exception as exc:
                value.update(status='unknown', error=session['sanitize'](str(exc)))
                with self.store._connect() as db:
                    self._save(db, value)
            finally:
                session['done'].set()
                with self.lock:
                    self.sessions.pop(value['execution_id'], None)
                with self.changed:
                    self.changed.notify_all()

    def _capture(self, session, final=False):
        value = session['value']
        captures = []
        for capture in session['process'].captures:
            # Secret redaction/UTF-8 decoding already happened before retention.
            # Complete lines also keep subsequent local-path masking stable.
            raw = capture.read(complete_lines=not final)
            captures.append(session['sanitize'](raw))
        value['stdout'], value['stderr'] = captures
        value['output_tail'] = {}
        for name, capture in zip(('stdout', 'stderr'), session['process'].captures, strict=True):
            tail = capture.tail()
            value['output_tail'][name] = {**tail, 'text': session['sanitize'](tail['text'])}
        with self.store._connect() as db:
            self._save(db, value)
        with self.changed:
            self.changed.notify_all()

    def read(self, execution_id, workspace_id, cursor=None, *, wait_ms=0, abort=None):
        if type(wait_ms) is not int or not 0 <= wait_ms <= 120000:
            raise ValueError('wait_ms must be an integer from 0 to 120000')
        value = self._get(execution_id, workspace_id)
        # Cursor is tied to one execution; offsets count returned characters.
        positions = [0, 0]
        if cursor:
            parsed = json.loads(cursor)
            if parsed.get('execution_id') != execution_id:
                raise ValueError('Output cursor belongs to another execution')
            positions = parsed['offsets']
            if len(positions) != 2 or any(type(i) is not int or i < 0 for i in positions):
                raise ValueError('Invalid output cursor')
        if any(positions[i] > len(value[stream]) for i, stream in enumerate(('stdout', 'stderr'))):
            raise ValueError('Output cursor is beyond retained output')
        deadline = time.monotonic() + wait_ms / 1000
        # Recheck under the notification lock to avoid missing an update between
        # reading SQLite and sleeping. Never keep a DB/global session lock asleep.
        with self.changed:
            while wait_ms:
                value = self._get(execution_id, workspace_id)
                if value['status'] not in ACTIVE or self.closed or (abort and abort.is_set()):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.changed.wait(min(remaining, .1))  # also observe cancellation Events
        for i, stream in enumerate(('stdout', 'stderr')):
            offset = positions[i]
            if offset > len(value[stream]):
                raise ValueError('Output cursor is beyond retained output')
            value[stream] = value[stream][offset:offset+16000]
            positions[i] += len(value[stream])
        value['output_cursor'] = json.dumps(dict(execution_id=execution_id, offsets=positions))
        return value

    def list(self, workspace_id):
        with self.store._connect() as db:
            values = [json.loads(r[0]) for r in db.execute('SELECT state_json FROM command_executions WHERE workspace_id=?', (workspace_id,))]
        return [command_summary(value) for value in values]

    def stop(self, execution_id, workspace_id):
        self._get(execution_id, workspace_id)
        session = self.sessions.get(execution_id)
        if session and not session['done'].is_set():
            session['stop'].set()
            if not session['done'].wait(15):
                raise RuntimeError('Process-tree cleanup has not completed')
        return self.read(execution_id, workspace_id, wait_ms=0)

    def finish_run(self, run_id, native_task_id=None):
        with self.lock:
            executions = [session['value'] for session in self.sessions.values()
                          if session['value']['run_id'] == run_id
                          and session['value'].get('native_task_id') == native_task_id]
        for value in executions:
            if value['purpose'] == 'command' and value['status'] in ACTIVE:
                self.stop(value['execution_id'], value['scope_id'])

    def scope_for_execution(self, execution_id, *, context):
        from deerflow.office.execution_workspace import execution_environment
        with self.store._connect() as db:
            row = db.execute('SELECT workspace_id,state_json FROM command_executions WHERE execution_id=?',
                             (execution_id,)).fetchone()
        if row is None:
            raise PermissionError('Unknown execution in this office')
        value = json.loads(row['state_json'])
        _, work, environment = execution_environment(context, target=value['workspace_id'] or 'project')
        if value['user_id'] != work.user_id or execution_scope(environment) != row['workspace_id']:
            raise PermissionError('Execution belongs to another project')
        return row['workspace_id']

    def close(self):
        with self.lock:
            self.closed = True
            sessions = list(self.sessions.values())
            self.approvals.closed.set()
        for session in sessions:
            session['stop'].set()
        with self.changed:
            self.changed.notify_all()
        for session in sessions:
            if not session['done'].wait(15):
                raise RuntimeError('Terminal cleanup incomplete')



def command_summary(value):
    summary = {key: value.get(key) for key in (
        'execution_id', 'status', 'purpose', 'exit_code', 'resolved_cwd', 'retry_root_id', 'attempt_index')}
    summary['command'] = value.get('command', '')[:400]
    if 'preview_port' in value:
        summary['preview_port'] = value['preview_port']
    return summary


def tool_result(value):
    result = {key: value[key] for key in (
        'execution_id', 'status', 'exit_code', 'resolved_cwd', 'purpose', 'stdout', 'stderr',
        'output_cursor', 'retries_remaining', 'preview_port', 'error') if key in value}
    if value.get('status') not in ACTIVE:
        tails = {name: tail for name, tail in value.get('output_tail', {}).items() if tail['omitted_characters']}
        if tails:
            result['output_tail'] = tails
    return result
