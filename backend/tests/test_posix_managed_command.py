import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_office_terminal import setup as setup

from deerflow.sandbox.local import managed_command, posix_command


def test_posix_launcher_uses_private_pipe_and_session(tmp_path, monkeypatch):
    child = SimpleNamespace(stdin=io.BytesIO(), wait=Mock(), poll=Mock(return_value=0))
    spawn = Mock(return_value=child)
    monkeypatch.setattr(managed_command.subprocess, 'Popen', spawn)
    monkeypatch.setattr(managed_command.PosixManagedProcess, '_capture_output', Mock())
    process = managed_command.PosixManagedProcess(['pwsh', '-Command', 'exit 23'], cwd=tmp_path, env={'KEY': 'value'})
    args, kwargs = spawn.call_args
    assert args[0][1].endswith('posix_command.py')
    assert kwargs['start_new_session'] is True
    assert kwargs['stdin'] == subprocess.PIPE
    assert kwargs['env'] == {'KEY': 'value'}
    assert 'creationflags' not in kwargs
    assert process.done()
    process.close()
    process.close()
    assert child.stdin.closed


@pytest.mark.parametrize('owner_closed', [False, True])
def test_supervisor_preserves_exit_or_kills_only_its_group(monkeypatch, owner_closed):
    child = SimpleNamespace(pid=1234, returncode=23, poll=Mock(return_value=23), wait=Mock())
    spawn = Mock(return_value=child)
    kills = []
    monkeypatch.setattr(posix_command.subprocess, 'Popen', spawn)
    monkeypatch.setattr(posix_command.signal, 'signal', Mock())
    monkeypatch.setattr(posix_command.signal, 'SIGKILL', 9, raising=False)
    monkeypatch.setattr(posix_command, 'group_exists', lambda group: owner_closed)
    monkeypatch.setattr(posix_command.os, 'killpg', lambda group, sig: kills.append((group, sig)), raising=False)
    monkeypatch.setattr(posix_command.os, 'read', lambda *args: b'')
    ready = iter([([], [], []), ([0], [], [])])
    monkeypatch.setattr(posix_command.select, 'select', lambda *args: next(ready))
    assert posix_command.supervise(['pwsh']) == 23
    assert kills == ([(1234, 9)] if owner_closed else [])
    assert spawn.call_args.kwargs == {'stdin': subprocess.DEVNULL, 'start_new_session': True}


def test_supervisor_never_starts_command_after_owner_disappears(monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(posix_command.subprocess, 'Popen', spawn)
    monkeypatch.setattr(posix_command.signal, 'signal', Mock())
    monkeypatch.setattr(posix_command.select, 'select', lambda *args: ([0], [], []))
    monkeypatch.setattr(posix_command.os, 'read', lambda *args: b'')
    assert posix_command.supervise(['pwsh']) == 1
    spawn.assert_not_called()


@pytest.mark.parametrize('system,label,path,absent', [
    ('Windows', 'Windows', '.venv/Scripts/python.exe', '.venv/bin/python'),
    ('Darwin', 'macOS', '.venv/bin/python', '.venv/Scripts/python.exe'),
])
def test_employee_prompt_uses_actual_host(system, label, path, absent, monkeypatch):
    from deerflow.office import work_context
    monkeypatch.setattr(work_context.platform, 'system', lambda: system)
    context = work_context.build_work_context(user_id='user', project_id='project', work_id='work',
                                             thread_id='thread', latest_user_message='Build it')
    text = work_context.build_delegation_prompt(task_prompt='Build it', shared_context=context, project_knowledge={})
    assert f'命令运行宿主：{label}' in text
    assert path in text and absent not in text


posix_only = pytest.mark.skipif(os.name != 'posix', reason='Requires native POSIX process groups')


def finish(process):
    deadline = time.monotonic() + 10
    while not process.done():
        if time.monotonic() >= deadline:
            pytest.fail('Managed command did not finish')
        time.sleep(.02)


@posix_only
@pytest.mark.parametrize('exit_code', [0, 23])
def test_native_exit_output_and_secret_redaction(tmp_path, exit_code):
    process = managed_command.ManagedProcess(
        [sys.executable, '-c', f"import sys; print('中文 native-test-secret'); print('warning', file=sys.stderr); sys.exit({exit_code})"],
        cwd=tmp_path, env=os.environ.copy(), secret_values=['native-test-secret'], tail_bytes=100,
    )
    try:
        finish(process)
    finally:
        process.close()
    assert process.process.returncode == exit_code
    assert '中文' in process.captures[0].read()
    assert 'native-test-secret' not in process.captures[0].read()
    assert '[redacted]' in process.captures[0].read()
    assert 'warning' in process.captures[1].read()


@posix_only
def test_native_close_stops_group_and_is_repeatable(tmp_path):
    process = managed_command.ManagedProcess(
        [sys.executable, '-c', 'import time; time.sleep(60)'], cwd=tmp_path, env=os.environ.copy(),
    )
    process.close()
    process.close()
    assert process.done()


@posix_only
def test_native_signal_exit_is_preserved(tmp_path):
    process = managed_command.ManagedProcess(
        [sys.executable, '-c', 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'],
        cwd=tmp_path, env=os.environ.copy(),
    )
    try:
        finish(process)
    finally:
        process.close()
    assert process.process.returncode == -signal.SIGTERM


@posix_only
def test_native_owner_death_closes_descendant_service(tmp_path):
    import socket
    marker = tmp_path / 'port.txt'
    owner_code = '''import os, sys, time
from deerflow.sandbox.local.managed_command import ManagedProcess
code = "import socket,sys,time; from pathlib import Path; server=socket.socket(); server.bind(('127.0.0.1',0)); server.listen(); Path(sys.argv[1]).write_text(str(server.getsockname()[1])); time.sleep(60)"
process = ManagedProcess([sys.executable, '-c', code, sys.argv[1]], cwd=None, env=os.environ.copy())
from pathlib import Path
for attempt in range(200):
    if Path(sys.argv[1]).exists():
        os._exit(0)
    time.sleep(.02)
os._exit(2)
'''
    completed = subprocess.run([sys.executable, '-c', owner_code, str(marker)], timeout=15,
        capture_output=True, env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'packages/harness')})
    assert completed.returncode == 0, completed.stderr
    port = int(marker.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                pass
        except OSError:
            return
        time.sleep(.02)
    pytest.fail('Service survived Gateway death')


@posix_only
def test_native_four_tools_share_session_and_stop(setup):
    from deerflow.sandbox.powershell_tools import powershell_list, powershell_read, powershell_run, powershell_stop
    runtime = SimpleNamespace(context=setup[2], tool_call_id='posix-tools', state={})
    message = powershell_run.func(runtime, command="Write-Output '开始'; Start-Sleep 60", yield_ms=0)
    started = json.loads(message.content)
    assert started['status'] == 'running'
    execution_id = started['execution_id']
    listed = json.loads(powershell_list.func(runtime).content)
    assert execution_id in [entry['execution_id'] for entry in listed['executions']]
    result = json.loads(powershell_read.func(runtime, execution_id=execution_id, wait_ms=200).content)
    assert result['status'] == 'running'
    stopped = json.loads(powershell_stop.func(runtime, execution_id=execution_id).content)
    assert stopped['status'] == 'cancelled'
    assert json.loads(powershell_read.func(runtime, execution_id=execution_id).content)['status'] == 'cancelled'


@posix_only
@pytest.mark.parametrize('ending', ['timeout', 'abort', 'close'])
def test_native_terminal_lifecycle(setup, ending):
    from test_office_terminal import run, wait
    options = {'timeout_seconds': .2} if ending == 'timeout' else {}
    started = run(setup, 'Start-Sleep 60', yield_ms=0, **options)
    if ending == 'abort':
        setup[2]['abort_event'].set()
    elif ending == 'close':
        setup[0].close()
    result = wait(setup, started)
    assert result['status'] == ('timeout' if ending == 'timeout' else 'cancelled')
