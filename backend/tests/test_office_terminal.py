import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config.paths import Paths
from deerflow.office import terminal
from deerflow.office.execution_facts import WorkIdentity
from deerflow.office.execution_workspace import ExecutionWorkspaceService, _git

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows process ownership')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    project = tmp_path / '项目'
    project.mkdir()
    _git(project, 'init')
    (project / 'README.md').write_text('原文\n', encoding='utf-8')
    _git(project, 'add', '.')
    _git(project, 'commit', '-m', 'baseline')
    paths = Paths(tmp_path / 'data')
    monkeypatch.setattr('deerflow.office.execution_workspace.get_paths', lambda: paths)
    service = ExecutionWorkspaceService(paths=paths, user_id='alice')
    work = WorkIdentity('alice', 'p', 'thread', 'work')
    ref = service.allocate(work, employee_id='writer', project_path=str(project), owner_key='direct:writer')
    workspace = service.prepare(ref['workspace_id'], work=work)
    manager = terminal.manager_for(service.store)
    context = dict(user_id='alice', office_employee_id='writer', run_id='run', office_work=work.to_record(), office_project_id='p',
                   office_project_path=str(project), office_execution_workspace=ref, abort_event=threading.Event())
    monkeypatch.setattr(terminal, 'authorize_execution', lambda *a: 'full_access')
    from deerflow.sandbox.local.local_sandbox import LocalSandbox
    monkeypatch.setattr('deerflow.sandbox.tools.ensure_sandbox_initialized', lambda runtime: LocalSandbox('terminal-test'))
    yield manager, workspace, context, service, work, project
    terminal.close_terminals()


def run(setup, command, call='call', **kwargs):
    manager, workspace, context, *_ = setup
    return manager.run(workspace=workspace, context=context, tool_call_id=call, command=command, yield_ms=kwargs.pop('yield_ms', 10000), **kwargs)


def wait(setup, execution, timeout=15):
    manager, workspace, *_ = setup
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = manager.read(execution['execution_id'], workspace['workspace_id'], wait_ms=0)
        if result['status'] not in terminal.ACTIVE:
            return result
        time.sleep(.05)
    pytest.fail('Process did not finish')


def test_success_output_data_is_not_status_and_workspace(setup):
    result = run(setup, "Write-Output 'Error: 失败只是内容'; [Console]::Error.WriteLine('warning'); Set-Content -LiteralPath '新增.txt' -Value '中文' -Encoding utf8")
    assert result['status'] == 'succeeded' and result['exit_code'] == 0
    assert 'Error: 失败只是内容' in result['stdout'] and 'warning' in result['stderr']
    assert (Path(setup[1]['root_path']) / '新增.txt').read_text(encoding='utf-8').strip() == '中文'
    assert not (setup[-1] / '新增.txt').exists()
    assert setup[0].read(result['execution_id'], setup[1]['workspace_id'], result['output_cursor'])['stdout'] == ''
    # Same persisted tool call cannot append again.
    again = run(setup, "Write-Output 'Error: 失败只是内容'; [Console]::Error.WriteLine('warning'); Set-Content -LiteralPath '新增.txt' -Value '中文' -Encoding utf8")
    assert again['execution_id'] == result['execution_id']
    with pytest.raises(ValueError, match='different'):
        run(setup, 'Write-Output changed')


def test_terminal_supplies_current_user_skill_directory(setup):
    result = run(setup, 'Write-Output $env:OFFICE_SKILLS_DIR')
    assert result['status'] == 'succeeded'
    expected = setup[3].store.paths.user_custom_skills_dir('alice').resolve()
    assert result['stdout'].strip() == str(expected)
    assert expected.is_dir()


def test_java_encoding_is_scoped_to_child_and_preserves_options(setup, monkeypatch):
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '-Doffice.probe=kept')
    result = run(setup, 'Write-Output $env:JAVA_TOOL_OPTIONS')
    assert result['status'] == 'succeeded'
    assert result['stdout'].strip() == '-Doffice.probe=kept -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8'
    assert os.environ['JAVA_TOOL_OPTIONS'] == '-Doffice.probe=kept'


@pytest.mark.parametrize('compiled', [False, True])
def test_java_stdout_stderr_chinese_through_terminal(setup, monkeypatch, compiled):
    executable = os.environ.get('OFFICE_TEST_JAVA') or shutil.which('java')
    if not executable:
        pytest.skip('Java source launcher is not installed')
    source = Path(setup[1]['root_path']) / 'EncodingProbe.java'
    source.write_text('class EncodingProbe { public static void main(String[] args) {'
                      'System.out.println("正确密码：登录成功"); System.err.println("错误密码：登录失败");'
                      'System.out.println(System.getProperty("office.probe")); }}', encoding='utf-8')
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '-Doffice.probe=kept')
    java = "& '" + executable.replace("'", "''") + "' '-Dfile.encoding=UTF-8'"
    # Source-file mode must read the UTF-8 fixture independently of the host code page.
    command = f"{java} EncodingProbe.java"
    if compiled:
        compiler = str(Path(executable).with_name('javac.exe' if os.name == 'nt' else 'javac')).replace("'", "''")
        command = f"& '{compiler}' -encoding UTF-8 EncodingProbe.java; {java} EncodingProbe"
    result = run(setup, command, yield_ms=30000)
    assert result['status'] == 'succeeded', result
    assert '正确密码：登录成功' in result['stdout']
    assert '错误密码：登录失败' in result['stderr']
    assert 'kept' in result['stdout']
    assert '\ufffd' not in result['stdout'] + result['stderr']


@pytest.mark.parametrize('state', ['rejected', 'cancelled', 'unknown', 'permission_error', 'failed', 'timeout'])
def test_terminal_tool_never_recommends_retry_after_denial_or_unknown(setup, monkeypatch, state):
    from deerflow.sandbox.powershell_tools import powershell_run
    from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY
    def execute(**kwargs):
        if state == 'permission_error':
            raise PermissionError('Approval expired; request was not executed')
        return {'status': state}
    monkeypatch.setattr(setup[0], 'run', execute)
    result = powershell_run.func(SimpleNamespace(context=setup[2], tool_call_id='denied', state={}), command='Write-Output test')
    meta = result.additional_kwargs[TOOL_META_KEY]
    assert result.status == 'error'
    retryable = state in {'failed', 'timeout'}
    assert meta['recoverable_by_model'] == retryable
    assert meta['recommended_next_action'] == ('try_alternative' if retryable else 'stop')


@pytest.mark.parametrize('command', ["Write-Error 'bad'; Write-Output 'hidden'", "cmd /c exit 9 && Write-Output 'hidden'", "Read-Host 'password'"])
def test_errors_are_not_hidden_by_later_success(setup, command):
    result = run(setup, command)
    assert result['status'] == 'failed' and result['exit_code'] != 0
    assert 'hidden' not in result['stdout']


@pytest.mark.parametrize('command, exit_code, error_fragment', [
    ("throw 'terminal probe error'", 1, 'terminal probe error'),
    ('cmd /c exit 9', 9, ''),
    ('exit 7', 7, ''),
])
def test_tool_returns_actual_exit_code_not_always_one(setup, command, exit_code, error_fragment):
    from deerflow.sandbox.powershell_tools import powershell_run

    runtime = SimpleNamespace(context=setup[2], tool_call_id='exit-code', state={})
    message = powershell_run.func(runtime, command=command, target='project', yield_ms=10000)
    result = json.loads(message.content)
    assert message.status == 'error'
    assert result['status'] == 'failed' and result['exit_code'] == exit_code
    assert result['execution_id']
    assert error_fragment in result['stderr']


@pytest.mark.parametrize('pipeline', ['', ' 2>&1 | Select-Object -Last 8', ' 2>&1 | Out-String'])
def test_native_failure_preserves_buffered_diagnostics_and_exit_code(setup, pipeline):
    from deerflow.sandbox.powershell_tools import powershell_run

    runtime = SimpleNamespace(context=setup[2], tool_call_id='native-failure', state={})
    command = "node -e 'console.log(\"ASSERTION_DETAIL\"); console.error(\"FAILURE_DETAIL\"); process.exit(7)'" + pipeline
    message = powershell_run.func(runtime, command=command, yield_ms=10000)
    result = json.loads(message.content)
    assert message.status == 'error'
    assert result['status'] == 'failed' and result['exit_code'] == 7
    assert 'ASSERTION_DETAIL' in result['stdout']
    assert 'FAILURE_DETAIL' in result['stdout'] + result['stderr']


def test_script_can_handle_native_exit_and_continue(setup):
    result = run(setup, "cmd /c exit 9; if ($LASTEXITCODE -eq 9) { Write-Output 'handled'; exit 0 }")
    assert result['status'] == 'succeeded' and result['exit_code'] == 0
    assert 'handled' in result['stdout']


def test_parser_error_is_plain_text(setup):
    result = run(setup, 'if (')
    assert result['status'] == 'failed' and result['exit_code'] != 0
    assert 'ParserError' in result['stderr']
    assert 'CLIXML' not in result['stderr'] and '_x000D_' not in result['stderr']


def test_invalid_directory_returns_error_without_creating_execution(setup):
    from deerflow.sandbox.powershell_tools import powershell_run

    runtime = SimpleNamespace(context=setup[2], tool_call_id='invalid-cwd', state={})
    message = powershell_run.func(runtime, command='Get-Location', cwd='missing-directory', target='project')
    result = json.loads(message.content)
    assert message.status == 'error' and result['status'] == 'error'
    assert 'cwd does not exist' in result['error']
    assert 'execution_id' not in result
    assert setup[0].list(f'project:{setup[4].project_id}') == []


@pytest.mark.parametrize('output_size', [0, 270000])
def test_terminal_tools_preserve_native_paths_and_redact_secrets(setup, monkeypatch, output_size):
    from deerflow.sandbox.powershell_tools import powershell_list, powershell_read, powershell_run, powershell_stop
    from deerflow.sandbox.tools import mask_local_paths_in_output

    project = str(setup[-1])
    thread_data = {'workspace_path': project}
    assert mask_local_paths_in_output(project, thread_data) == '/mnt/user-data/workspace'
    secret = 'terminal-path-probe-secret-92841'
    monkeypatch.setattr('deerflow.sandbox.tools.read_active_secrets', lambda context: {'probe': secret})
    runtime = SimpleNamespace(context=setup[2], tool_call_id='native-paths', state={'thread_data': thread_data})
    quoted_project = project.replace("'", "''")
    command = (f"Write-Output '{quoted_project}'; Write-Output $env:OFFICE_SKILLS_DIR; "
               f"[Console]::Out.Write(('x' * {output_size}) + '{quoted_project} {secret}'); "
               f"[Console]::Error.Write(('y' * {output_size}) + '{quoted_project} {secret}')")
    message = powershell_run.func(runtime, command=command, target='project', yield_ms=10000)
    result = json.loads(message.content)
    assert result['status'] == 'succeeded'
    assert project in result['stdout']
    assert str(setup[3].store.paths.user_custom_skills_dir('alice').resolve()) in result['stdout']
    assert secret not in message.content
    if output_size:
        for tail in result['output_tail'].values():
            assert project in tail['text'] and secret not in tail['text']
    else:
        assert project in result['stderr']
    stored = setup[0]._get(result['execution_id'], f'project:{setup[4].project_id}')
    assert project in stored['command'] and secret not in stored['command']
    for tool in (powershell_read, powershell_stop):
        response = json.loads(tool.func(runtime, execution_id=result['execution_id']).content)
        assert project in response['stdout']
        assert secret not in json.dumps(response)
    listed = json.loads(powershell_list.func(runtime, target='project').content)['executions']
    assert project in listed[0]['command'] and secret not in listed[0]['command']


def test_terminal_tool_error_preserves_native_path_and_redacts_secret(setup, monkeypatch):
    from deerflow.sandbox.powershell_tools import powershell_run

    project = str(setup[-1])
    secret = 'terminal-error-secret-92841'
    monkeypatch.setattr('deerflow.sandbox.tools.read_active_secrets', lambda context: {'probe': secret})
    def fail(**kwargs):
        raise ValueError(f'Cannot inspect {project}: {secret}')
    monkeypatch.setattr(setup[0], 'run', fail)
    runtime = SimpleNamespace(context=setup[2], tool_call_id='native-error', state={'thread_data': {'workspace_path': project}})
    result = json.loads(powershell_run.func(runtime, command='Get-Location', target='project').content)
    assert result['status'] == 'error'
    assert project in result['error'] and secret not in result['error']


@pytest.mark.parametrize('output_size', [24, 300000])
def test_tool_only_returns_tail_for_omitted_terminal_output(setup, output_size):
    from deerflow.sandbox.powershell_tools import powershell_read, powershell_run

    runtime = SimpleNamespace(context=setup[2], tool_call_id='bounded-log', state={})
    command = f"[Console]::Out.Write(('x' * {output_size}) + 'END_STDOUT'); [Console]::Error.Write(('y' * {output_size}) + 'END_STDERR')"
    message = powershell_run.func(runtime, command=command, yield_ms=10000)
    result = json.loads(message.content)
    assert result['status'] == 'succeeded' and result['exit_code'] == 0
    assert len(result['stdout']) <= 16000 and len(result['stderr']) <= 16000
    if output_size == 24:
        assert 'output_tail' not in result
        assert result['stdout'].endswith('END_STDOUT') and result['stderr'].endswith('END_STDERR')
    else:
        assert result['output_tail']['stdout']['text'].endswith('END_STDOUT')
        assert result['output_tail']['stderr']['text'].endswith('END_STDERR')
        assert result['output_tail']['stdout']['omitted_characters'] > 0
        assert result['output_tail']['stderr']['omitted_characters'] > 0
    following = powershell_read.func(runtime, execution_id=result['execution_id'], cursor=result['output_cursor'], wait_ms=0)
    next_result = json.loads(following.content)
    assert next_result['execution_id'] == result['execution_id']
    assert next_result.get('output_tail') == result.get('output_tail')
    if output_size == 24:
        assert next_result['stdout'] == next_result['stderr'] == ''


def test_preview_survives_task_end_but_not_execution_deadline(setup):
    from deerflow.sandbox.powershell_tools import powershell_read, powershell_run

    runtime = SimpleNamespace(context=setup[2], tool_call_id='preview-deadline', state={})
    started = powershell_run.func(runtime, command='Start-Sleep 60', purpose='preview_service', timeout_seconds=2, yield_ms=0)
    result = json.loads(started.content)
    assert result['status'] == 'running'
    setup[0].finish_run(setup[2]['run_id'])
    completed = powershell_read.func(runtime, execution_id=result['execution_id'], wait_ms=10000)
    assert completed.status == 'success'
    assert json.loads(completed.content)['status'] == 'timeout'


def test_background_continue_http_preview_and_stop(setup):
    import urllib.request
    root = Path(setup[1]['root_path'])
    (root / 'package.json').write_text(json.dumps({'scripts': {'dev': 'node server.cjs'}}))
    (root / 'server.cjs').write_text("require('node:http').createServer((req,res)=>res.end(require('node:fs').readFileSync('README.md'))).listen(Number(process.env.OFFICE_PREVIEW_PORT),'127.0.0.1')")
    command = 'npm.cmd run dev'
    started = time.monotonic()
    server = run(setup, command, purpose='preview_service', yield_ms=50)
    assert time.monotonic() - started < 3 and server['status'] == 'running'
    assert 'ready' not in server
    entry = next(item for item in setup[0].list(setup[1]['workspace_id']) if item['execution_id'] == server['execution_id'])
    assert entry['resolved_cwd'] == str(root)
    assert entry['command'] == command
    assert entry['preview_port'] == server['preview_port']
    assert entry['status'] == 'running' and 'ready' not in entry
    assert run(setup, "Write-Output 'continued'", call='next')['status'] == 'succeeded'
    from deerflow.office.delegation_memory import execution_evidence
    summary = execution_evidence(setup[3].store, setup[1], work=setup[4], employee_id='writer')
    preview = summary['executions'][0]
    assert preview['execution_id'] == server['execution_id']
    assert preview['preview_port'] == server['preview_port']
    assert preview['resolved_cwd'] == str(root)
    assert 'command' not in preview and 'ready' not in preview
    port = server['preview_port']
    deadline = time.monotonic() + 10
    while True:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/README.md', timeout=1) as response:
                assert '原文' in response.read().decode('utf-8')
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)
    (root / 'README.md').write_text('原文\n实时更新\n', encoding='utf-8')
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/README.md', timeout=1) as response:
        assert '实时更新' in response.read().decode('utf-8')
    assert setup[0].read(server['execution_id'], setup[1]['workspace_id'])['status'] == 'running'
    setup[0].finish_run(setup[2]['run_id'])
    assert 'result_commit' not in setup[3].get(setup[1]['workspace_id'])
    assert setup[0].read(server['execution_id'], setup[1]['workspace_id'])['status'] == 'running'
    from deerflow.office.workspace_delivery import publish
    (setup[3].paths.user_dir('alice') / 'office-bootstrap.yaml').write_text('terminal_access_mode: full_access')
    delivered = publish(setup[3], setup[4], setup[1]['workspace_id'], context=setup[2])
    assert delivered['status'] == 'written'
    assert setup[0].read(server['execution_id'], setup[1]['workspace_id'])['status'] == 'running'
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/README.md', timeout=1) as response:
        assert '原文' in response.read().decode('utf-8')
    stopped = setup[0].stop(server['execution_id'], setup[1]['workspace_id'])
    assert stopped['status'] == 'cancelled'
    assert setup[0].stop(server['execution_id'], setup[1]['workspace_id'])['status'] == 'cancelled'
    with pytest.raises(OSError):
        urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1)


def test_timeout_abort_and_finish_stop_owned_tree(setup):
    result = run(setup, 'Start-Sleep 60', timeout_seconds=.15)
    assert result['status'] == 'timeout'
    active = run(setup, 'Start-Sleep 60', call='abort', yield_ms=0)
    setup[2]['abort_event'].set()
    assert wait(setup, active)['status'] == 'cancelled'
    setup[2]['abort_event'].clear()
    active = run(setup, 'Start-Sleep 60', call='finish', yield_ms=0)
    setup[0].finish_run(setup[2]['run_id'])
    assert wait(setup, active)['status'] == 'cancelled'


def test_retry_limit_atomic_latest_failure(setup):
    last = run(setup, 'exit 2')
    root = last['execution_id']
    for attempt in range(1, 6):
        last = run(setup, f'exit {attempt+2}', call=f'retry-{attempt}', retry_of=last['execution_id'])
        assert last['attempt_index'] == attempt and last['retries_remaining'] == 5-attempt
        assert last['retry_root_id'] == root
    with pytest.raises(ValueError, match='five retries'):
        run(setup, 'exit 1', call='exhausted', retry_of=last['execution_id'])
    with pytest.raises(ValueError, match='latest failure'):
        run(setup, 'exit 1', call='old', retry_of=root)
    first = run(setup, 'exit 2', call='other')
    def attempt(i):
        try:
            return run(setup, 'Start-Sleep 1; exit 3', call=f'concurrent-{i}', retry_of=first['execution_id'], yield_ms=0)
        except ValueError:
            return None
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(attempt, range(2)))
    assert sum(r is not None for r in replies) == 1


def test_ownership_paths_and_closed_production_gate(setup, monkeypatch):
    result = run(setup, 'exit 0')
    with pytest.raises(PermissionError):
        setup[0].read(result['execution_id'], 'other')
    for path in ('..', 'C:/Windows', '../other', '.git', 'missing'):
        with pytest.raises(ValueError):
            run(setup, 'exit 0', call=path, cwd=path)
    with pytest.raises(ValueError):
        run(setup, 'exit 0', call='badretry', retry_of=result['execution_id'])
    from deerflow.sandbox.powershell_tools import powershell_run
    monkeypatch.setattr(terminal, 'authorize_execution', lambda *a: (_ for _ in ()).throw(PermissionError('approval pending')))
    message = powershell_run.func(SimpleNamespace(context=setup[2], tool_call_id='denied', state={}), 'exit 0')
    assert message.status == 'error' and 'approval pending' in message.content


def test_log_bounds_secret_environment_and_restart_unknown(setup, monkeypatch):
    monkeypatch.setenv('FAKE_PLATFORM_API_KEY', 'must-not-inherit')
    result = run(setup, "Write-Output ('x' * 400000); Write-Output $env:FAKE_PLATFORM_API_KEY")
    assert len(result['stdout']) <= 16000 and 'must-not-inherit' not in result['stdout']
    all_output, cursor = result['stdout'], result['output_cursor']
    for _ in range(25):
        page = setup[0].read(result['execution_id'], setup[1]['workspace_id'], cursor)
        all_output += page['stdout']; cursor = page['output_cursor']
    assert len(all_output) < 270000 and 'truncated' in all_output
    # A crash can leave the persisted row running. Reopening records unknown,
    # with the same call receipt retained; no recovered PID is ever killed.
    setup[0].close()
    with setup[0].store._connect() as db:
        value = setup[0]._get(result['execution_id'], setup[1]['workspace_id'])
        value['status'] = 'running'
        setup[0]._save(db, value)
    recovered = terminal.TerminalManager(setup[0].store)
    assert recovered.read(result['execution_id'], setup[1]['workspace_id'])['status'] == 'unknown'
    recovered.close()


def test_production_authorizer_has_no_context_bypass():
    # Separate from fixture replacement; preserve the real callable itself.
    with pytest.raises(PermissionError, match='authorized employee'):
        terminal.authorize_execution({'terminal_access_mode': 'full_access', 'approved': True}, 'exit 0', '.')


def test_terminal_facts_are_not_publication_qualifications(setup):
    from deerflow.office.workspace_delivery import publish
    manager, workspace, context, svc, work, project = setup
    (svc.paths.user_dir('alice') / 'office-bootstrap.yaml').write_text('terminal_access_mode: full_access')
    root = Path(workspace['root_path'])
    (root / 'README.md').write_text('modified')
    passed = run(setup, "if ((Get-Content README.md -Raw) -ne 'modified') { throw 'wrong' }")
    assert passed['status'] == 'succeeded'
    assert 'verified_tree' not in manager._get(passed['execution_id'], workspace['workspace_id'])
    (root / 'README.md').write_text('changed after validation')
    failed = run(setup, "throw 'semantic regression'", call='bad-test')
    assert failed['status'] == 'failed'
    assert (project / 'README.md').read_text(encoding='utf-8') == '原文\n'
    result = publish(svc, work, workspace['workspace_id'], context=context)
    assert result['status'] == 'written'
    assert 'validation_refs' not in result
    assert manager._get(failed['execution_id'], workspace['workspace_id'])['status'] == 'failed'
    (root / 'README.md').write_text('later unverified edit')
    changed = svc.get(workspace['workspace_id'], work=work)
    assert 'integration_status' not in changed
    assert _git(root, 'rev-parse', 'HEAD').decode().strip() == result['result_commit']
    assert (project / 'README.md').read_text() == 'changed after validation'


def test_tool_redacts_secret_before_capture_limit_in_both_streams(setup, monkeypatch):
    from deerflow.sandbox.powershell_tools import powershell_run

    secret = 'acceptance-secret-ONLY-FAKE-1234567890'
    monkeypatch.setattr('deerflow.sandbox.tools.read_active_secrets', lambda context: {'TEST_KEY': secret})
    runtime = SimpleNamespace(context=setup[2], tool_call_id='secret-boundary', state={})
    command = f"$s=('x' * {256 * 1024 - 20}) + '{secret}' + ('y' * 200); Write-Output $s; [Console]::Error.WriteLine($s)"
    message = powershell_run.func(runtime, command=command, yield_ms=10000)
    assert message.status == 'success'
    result = json.loads(message.content)
    # Read the durable record too: masking only the returned ToolMessage would
    # leave the secret in the execution database and in subsequent reads.
    stored = setup[0]._get(result['execution_id'], setup[1]['workspace_id'])
    assert secret not in stored['command'] and '[redacted]' in stored['command']
    for stream in ('stdout', 'stderr'):
        assert secret[:20] not in stored[stream]
        assert '[redacted]' in stored[stream] and 'truncated' in stored[stream]
    cursor = result['output_cursor']
    collected = {stream: result[stream] for stream in ('stdout', 'stderr')}
    for _ in range(20):
        page = setup[0].read(result['execution_id'], setup[1]['workspace_id'], cursor)
        for stream in collected:
            collected[stream] += page[stream]
        cursor = page['output_cursor']
    assert all(collected[stream] == stored[stream] for stream in collected)


def test_live_truncated_log_cursor_and_unicode_are_stable(setup):
    result = run(setup, f"Write-Output (('x' * {256 * 1024 - 1}) + '中文'); Start-Sleep 2; Write-Output ('z' * 90000); Start-Sleep 2",
                 call='live-boundary', yield_ms=1000)
    collected, cursor = result['stdout'], result['output_cursor']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        page = setup[0].read(result['execution_id'], setup[1]['workspace_id'], cursor)
        collected += page['stdout']
        cursor = page['output_cursor']
        if page['status'] not in terminal.ACTIVE and not page['stdout']:
            break
        time.sleep(.03)
    assert page['status'] == 'succeeded'
    stored = setup[0]._get(result['execution_id'], setup[1]['workspace_id'])['stdout']
    assert collected == stored and collected.count('truncated') == 1
    assert '\ufffd' not in collected


def test_legacy_managed_command_redacts_before_retaining(setup, monkeypatch):
    from deerflow.sandbox.local import managed_command
    from deerflow.sandbox.local.local_sandbox import LocalSandbox
    original = managed_command.ManagedProcess

    def small_capture(*args, **kwargs):
        return original(*args, **{**kwargs, 'output_limit': 64})

    monkeypatch.setattr(managed_command, 'ManagedProcess', small_capture)
    sandbox = LocalSandbox('legacy-output-test')
    monkeypatch.setattr(sandbox, '_get_shell', lambda: terminal.powershell_executable()[0])
    secret = 'legacy-secret-ONLY-FAKE-1234567890'
    output = managed_command.execute_managed_command(sandbox,
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); Write-Output (('x' * 44) + $env:TEST_KEY + ('中' * 100))",
        env={'TEST_KEY': secret}, timeout=10)
    assert secret[:20] not in output and '[redacted]' in output
    assert 'truncated' in output and '\ufffd' not in output


def test_spawn_failure_has_receipt_and_retry_identity(setup, monkeypatch):
    original = terminal.ManagedProcess
    def unavailable(*args, **kwargs):
        raise OSError('Process creation failed')
    monkeypatch.setattr(terminal, 'ManagedProcess', unavailable)
    failed = run(setup, 'exit 0', call='spawn-failed')
    assert failed['status'] == 'failed' and failed['execution_id'] == failed['retry_root_id']
    assert setup[0].sessions == {}
    monkeypatch.setattr(terminal, 'ManagedProcess', original)
    retried = run(setup, 'exit 0', call='spawn-retry', retry_of=failed['execution_id'])
    assert retried['status'] == 'succeeded' and retried['attempt_index'] == 1


def test_abrupt_owner_exit_kills_live_service(tmp_path):
    import subprocess
    import sys
    import socket
    script = tmp_path / 'owner.py'
    marker = tmp_path / 'port.txt'
    script.write_text('''import os, socket, sys, time
from pathlib import Path
from deerflow.sandbox.local.managed_command import ManagedProcess
from deerflow.sandbox.env_policy import build_sandbox_env
with socket.socket() as candidate:
    candidate.bind(('127.0.0.1', 0))
    port = candidate.getsockname()[1]
process = ManagedProcess([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'], cwd=sys.argv[1], env=build_sandbox_env())
for _ in range(100):
    try:
        connection = socket.create_connection(('127.0.0.1', port), timeout=.1)
        connection.close()
        Path(sys.argv[2]).write_text(str(port))
        os._exit(0)  # no finally/atexit; owner handle closure must kill descendants
    except OSError:
        time.sleep(.05)
os._exit(2)
''', encoding='utf-8')
    completed = subprocess.run([sys.executable, str(script), str(tmp_path), str(marker)], timeout=15,
        capture_output=True, env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'packages/harness')})
    assert completed.returncode == 0, completed.stderr
    port = int(marker.read_text())
    until = time.monotonic() + 5
    while time.monotonic() < until:
        try:
            connection = socket.create_connection(('127.0.0.1', port), timeout=.1)
            connection.close()
            time.sleep(.05)
        except OSError:
            return
    pytest.fail('Service survived its Job Object owner')


@pytest.mark.anyio
async def test_tools_async_status_and_no_runtime_in_schema(setup):
    from deerflow.sandbox.powershell_tools import powershell_run, powershell_read, powershell_stop, powershell_list
    runtime = SimpleNamespace(context=setup[2], tool_call_id='async', state={})
    for tool in (powershell_run, powershell_read, powershell_stop, powershell_list):
        assert 'runtime' not in tool.args
    started = await powershell_run.coroutine(runtime, command='Start-Sleep 60', yield_ms=0)
    assert started.status == 'success'
    execution_id = json.loads(started.content)['execution_id']
    assert started.artifact == {'terminal_execution': {'execution_id': execution_id, 'status': 'running'}}
    stopped = await powershell_stop.coroutine(runtime, execution_id=execution_id)
    assert stopped.status == 'success' and json.loads(stopped.content)['status'] == 'cancelled'
    assert stopped.artifact['terminal_execution']['status'] == 'cancelled'
    result = await powershell_read.coroutine(runtime, execution_id=execution_id)
    assert result.status == 'success'
    assert result.artifact['terminal_execution']['status'] == 'cancelled'
    runtime.tool_call_id = 'text-status'
    result = await powershell_run.coroutine(runtime, command="Write-Output 'Error: content only'", yield_ms=10000)
    assert result.status == 'success'
    assert json.loads(result.content)['stdout'].strip() == 'Error: content only'
    assert json.loads(result.content)['stderr'] == ''
    assert result.artifact['terminal_execution']['status'] == 'succeeded'
    denied = await powershell_read.coroutine(runtime, execution_id='not-owned')
    assert denied.status == 'error'
    assert denied.artifact == {'terminal_execution': {'execution_id': 'not-owned', 'status': 'error'}}


def test_read_wait_incremental_output_and_finish(setup):
    manager, workspace, *_ = setup
    result = run(setup, "Start-Sleep -Milliseconds 600; Write-Output 'first'; Start-Sleep -Milliseconds 1200; Write-Output 'last'", yield_ms=0)
    started = time.monotonic()
    first = manager.read(result['execution_id'], workspace['workspace_id'], result['output_cursor'], wait_ms=10000)
    assert 1.4 <= time.monotonic() - started < 8
    assert 'first' in first['stdout'] and 'last' in first['stdout']
    assert first['status'] == 'succeeded'
    second = manager.read(result['execution_id'], workspace['workspace_id'], first['output_cursor'], wait_ms=10000)
    assert 'first' not in second['stdout']
    final = wait(setup, result)
    assert final['status'] == 'succeeded'
    started = time.monotonic()
    empty = manager.read(result['execution_id'], workspace['workspace_id'], final['output_cursor'], wait_ms=10000)
    assert time.monotonic() - started < .5 and not empty['stdout']


def test_read_wait_timeout_cancel_and_ownership(setup):
    manager, workspace, context, *_ = setup
    result = run(setup, 'Start-Sleep -Seconds 15', yield_ms=0)
    args = (result['execution_id'], workspace['workspace_id'], result['output_cursor'])
    started = time.monotonic()
    empty = manager.read(*args, wait_ms=200)
    assert .15 <= time.monotonic() - started < 2
    assert empty['stdout'] == '' and empty['status'] == 'running'
    with pytest.raises(PermissionError):
        manager.read(result['execution_id'], 'other', wait_ms=10000)
    with pytest.raises(ValueError):
        manager.read(*args[:2], json.dumps({'execution_id': 'other', 'offsets': [0, 0]}), wait_ms=10000)
    with pytest.raises(ValueError):
        manager.read(*args[:2], json.dumps({'execution_id': result['execution_id'], 'offsets': [100, 0]}), wait_ms=10000)
    for invalid in (-1, 120001, True, 1.5):
        with pytest.raises(ValueError):
            manager.read(*args, wait_ms=invalid)
    abort = threading.Event()
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(manager.read, *args, wait_ms=10000, abort=abort)
        time.sleep(.1)
        abort.set()
        assert pending.result(timeout=1)['status'] == 'running'
    # Read cancellation does not start another process or consume retries.
    assert len(manager.list(workspace['workspace_id'])) == 1
    assert manager.list(workspace['workspace_id'])[0]['attempt_index'] == 0
    manager.stop(result['execution_id'], workspace['workspace_id'])


def test_execution_evidence_distinct_commands_bounded_and_private(setup, monkeypatch):
    from deerflow.office.delegation_memory import execution_evidence, with_execution_memory
    from deerflow.agents.memory.backends.deermem.deermem.core.prompt import _count_tokens, format_conversation_for_update
    from langchain_core.messages import HumanMessage, AIMessage
    manager, workspace, context, service, work, _ = setup
    ok = run(setup, "Write-Output 'all tests failed: this is just data'", call='one')
    bad = run(setup, 'exit 2', call='two')
    assert bad['status'] == 'failed' and bad['exit_code'] == 2
    for _ in range(100):
        manager.read(ok['execution_id'], workspace['workspace_id'], ok['output_cursor'])
    summary = execution_evidence(service.store, workspace, work=work, employee_id='writer')
    assert summary['total_executions'] == 2
    assert summary['status_counts'] == {'failed': 1, 'succeeded': 1}
    assert summary['executions'][0]['exit_code'] == 2
    assert all('version_validation_recorded' not in item for item in summary['executions'])
    assert 'all tests failed' not in json.dumps(summary)
    assert execution_evidence(service.store, workspace, work=work, employee_id='other')['total_executions'] == 0
    with pytest.raises(ValueError):
        execution_evidence(service.store, workspace, work=WorkIdentity('bob', 'p', 'thread', 'work'), employee_id='writer')
    messages = [HumanMessage(content='执行检查'), AIMessage(content='没有运行过测试')]
    monkeypatch.setattr('deerflow.office.execution_workspace.workspace_for_context', lambda ctx: (service, work, workspace))
    enriched = with_execution_memory(messages, context, employee_id='writer', user_id='alice')
    assert enriched is not messages and not messages[-1].additional_kwargs
    formatted = format_conversation_for_update(enriched)
    assert 'Recorded execution facts' in formatted and 'succeeded' in formatted and 'failed' in formatted
    assert len(enriched) == len(messages)
    assert with_execution_memory(messages, {**context, 'office_native_task_id': 'delegation'}, employee_id='writer', user_id='alice') is messages
    assert with_execution_memory(messages, context, employee_id='writer', user_id='bob') is messages
    with service.store._connect() as db:
        for i in range(300):
            value = manager._get(ok['execution_id'], workspace['workspace_id'])
            value.update(execution_id=f'exec-{i}', status='unknown' if i == 0 else 'succeeded', stdout='SECRET', stderr='SECRET', command='SECRET')
            db.execute('INSERT INTO command_executions VALUES (?,?,?,?)', (value['execution_id'], workspace['workspace_id'], f'key-{i}', json.dumps(value)))
    summary = execution_evidence(service.store, workspace, work=work, employee_id='writer')
    assert summary['total_executions'] == 302 and summary['status_counts']['unknown'] == 1
    assert len(summary['executions']) <= 6
    assert summary['omitted_executions'] == 302-len(summary['executions'])
    assert _count_tokens(json.dumps(summary, ensure_ascii=False)) <= 800
    assert 'SECRET' not in json.dumps(summary)


@pytest.mark.parametrize('close', [False, True])
def test_read_wait_wakes_on_final_without_output_or_shutdown(setup, close):
    manager, workspace, *_ = setup
    command = 'Start-Sleep -Seconds 20' if close else 'Start-Sleep -Milliseconds 400; exit 3'
    result = run(setup, command, yield_ms=0)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(manager.read, result['execution_id'], workspace['workspace_id'], result['output_cursor'], wait_ms=10000)
        if close:
            manager.close()
        final = pending.result(timeout=4)
    if not close:
        assert final['status'] == 'failed' and final['exit_code'] == 3
    else:
        assert manager.closed
    assert not final['stdout']


def test_powershell_object_output_is_flushed_before_wrapper_exit(setup):
    result = run(setup, 'Get-Location; node -v; Get-ChildItem -Name | Select-Object -First 5')
    assert result['status'] == 'succeeded'
    assert 'README.md' in result['stdout'] and 'Path' in result['stdout']
    formatted = run(setup, 'Get-ChildItem | Select-Object Name,Length | Format-Table -AutoSize', call='table')
    assert 'README.md' in formatted['stdout'] and 'Length' in formatted['stdout']


@pytest.mark.parametrize('target_kind', ['default', 'project', 'worktree'])
def test_preview_directory_is_explicit_and_survives_run_return(setup, target_kind):
    import urllib.request
    from deerflow.office.workspace_delivery import publish
    from deerflow.sandbox.powershell_tools import powershell_run, powershell_read, powershell_stop
    manager, workspace, context, service, work, project = setup
    (Path(workspace['root_path']) / 'README.md').write_text('worktree version')
    (service.paths.user_dir('alice') / 'office-bootstrap.yaml').write_text('terminal_access_mode: full_access')
    delivered = publish(service, work, workspace['workspace_id'], context=context)
    assert delivered['status'] == 'written'
    assert (project / 'README.md').read_text() == 'worktree version'
    runtime = SimpleNamespace(context=context, tool_call_id='preview', state={})
    target = {} if target_kind == 'default' else {'target': workspace['workspace_id'] if target_kind == 'worktree' else 'project'}
    expected_root = Path(workspace['root_path']) if target_kind == 'worktree' else project
    message = powershell_run.func(runtime, **target, purpose='preview_service', yield_ms=0,
        command="node -e \"require('http').createServer((req,res)=>res.end(require('fs').readFileSync('README.md'))).listen(Number(process.env.OFFICE_PREVIEW_PORT),'127.0.0.1')\"")
    assert message.status == 'success'
    preview = json.loads(message.content)
    assert Path(preview['resolved_cwd']) == expected_root
    deadline = time.monotonic() + 10
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{preview['preview_port']}", timeout=1) as response:
                assert response.read() == (expected_root / 'README.md').read_bytes()
            break
        except OSError:
            assert time.monotonic() < deadline
            time.sleep(.05)
    manager.finish_run(context['run_id'])
    runtime.context = {**context, 'run_id': 'another-run', 'agent_name': 'second'}
    runtime.context.pop('office_execution_workspace')
    runtime.tool_call_id = 'direct-edit'
    edited = powershell_run.func(runtime, command="Set-Content -LiteralPath 'README.md' -Value 'updated bound project'", yield_ms=10000)
    assert json.loads(edited.content)['status'] == 'succeeded'
    with urllib.request.urlopen(f"http://127.0.0.1:{preview['preview_port']}", timeout=1) as response:
        assert response.read() == (expected_root / 'README.md').read_bytes()
    inspected = powershell_read.func(runtime, execution_id=preview['execution_id'], wait_ms=0)
    assert json.loads(inspected.content)['status'] == 'running'
    assert context['office_execution_workspace']['workspace_id'] == workspace['workspace_id']
    assert powershell_stop.func(runtime, execution_id=preview['execution_id']).status == 'success'


@pytest.mark.parametrize('target_kind', ['default', 'project'])
def test_empty_terminal_list_identifies_queried_directory(setup, target_kind):
    from deerflow.sandbox.powershell_tools import powershell_list

    runtime = SimpleNamespace(context=setup[2], tool_call_id='list-empty', state={})
    target = {} if target_kind == 'default' else {'target': 'project'}
    result = json.loads(powershell_list.func(runtime, **target).content)
    assert result['executions'] == []
    expected_root = setup[1]['root_path'] if target_kind == 'default' else setup[-1]
    assert Path(result['resolved_cwd']) == Path(expected_root)


def test_preview_missing_project_directory_does_not_use_worktree(setup):
    from deerflow.sandbox.powershell_tools import powershell_run

    (Path(setup[1]['root_path']) / 'worktree-only').mkdir()
    runtime = SimpleNamespace(context=setup[2], tool_call_id='wrong-preview-root', state={})
    result = json.loads(powershell_run.func(runtime, command='Get-Location', purpose='preview_service', cwd='worktree-only').content)
    assert result['status'] == 'error' and 'cwd does not exist' in result['error']
    assert setup[0].list('project:p') == setup[0].list(setup[1]['workspace_id']) == []


def test_direct_execution_and_cross_run_actor_do_not_allocate_workspaces(setup):
    from deerflow.sandbox.powershell_tools import powershell_run, powershell_read
    manager, workspace, context, service, work, project = setup
    direct = {key: value for key, value in context.items() if key != 'office_execution_workspace'}
    runtime = SimpleNamespace(context=direct, tool_call_id='same-id', state={})
    first = json.loads(powershell_run.func(runtime, command="Set-Content 'direct.txt' 'one'", yield_ms=10000).content)
    assert first['status'] == 'succeeded'
    assert (project / 'direct.txt').read_text().strip() == 'one'
    assert manager._get(first['execution_id'], 'project:p')['workspace_id'] is None
    runtime.context = {**direct, 'run_id': 'new-run', 'agent_name': 'second'}
    second = json.loads(powershell_run.func(runtime, command="Set-Content 'direct.txt' 'two'", yield_ms=10000).content)
    assert second['status'] == 'succeeded' and second['execution_id'] != first['execution_id']
    assert manager._get(second['execution_id'], 'project:p')['employee_id'] == 'second'
    assert json.loads(powershell_read.func(runtime, execution_id=first['execution_id']).content)['exit_code'] == 0
    with service.store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM execution_workspaces').fetchone()[0] == 1
    runtime.context = {**direct, 'office_project_id': 'another', 'office_work': {**work.to_record(), 'project_id': 'another'}}
    assert powershell_read.func(runtime, execution_id=first['execution_id']).status == 'error'


def test_pm_can_query_employee_execution_without_authorizing_commands(setup, monkeypatch):
    from deerflow.sandbox.powershell_tools import powershell_run, powershell_read, powershell_list
    manager, workspace, context, service, work, project = setup
    direct = {key: value for key, value in context.items() if key != 'office_execution_workspace'}
    runtime = SimpleNamespace(context=direct, tool_call_id='employee-command', state={})
    command = json.loads(powershell_run.func(runtime, command="Write-Output 'employee-result'", yield_ms=10000).content)
    runtime.context = {**direct, 'office_employee_id': 'pm', 'agent_name': 'pm', 'run_id': 'pm-run'}

    def deny_execution(*args, **kwargs):
        raise PermissionError('PM cannot execute commands')

    monkeypatch.setattr(terminal, 'authorize_execution', deny_execution)
    listed = powershell_list.func(runtime, target='project')
    assert listed.status == 'success'
    assert command['execution_id'] in listed.content
    output = powershell_read.func(runtime, execution_id=command['execution_id'])
    assert output.status == 'success'
    assert json.loads(output.content)['exit_code'] == 0
    assert 'employee-result' in json.loads(output.content)['stdout']
    assert powershell_run.func(runtime, command='Write-Output forbidden').status == 'error'
    runtime.context = {**runtime.context, 'office_project_id': 'another', 'office_work': {**work.to_record(), 'project_id': 'another'}}
    assert powershell_read.func(runtime, execution_id=command['execution_id']).status == 'error'


def test_finish_run_does_not_stop_other_employee_in_same_workspace(setup):
    manager, workspace, context, *_ = setup
    first = run(setup, 'Start-Sleep 60', yield_ms=0)
    second_context = {**context, 'agent_name': 'second', 'office_native_task_id': 'task-2'}
    second = manager.run(workspace=workspace, context=second_context, tool_call_id='call', command='Start-Sleep 60', yield_ms=0)
    assert first['execution_id'] != second['execution_id']
    manager.finish_run(context['run_id'])
    assert manager.read(first['execution_id'], workspace['workspace_id'])['status'] == 'cancelled'
    assert manager.read(second['execution_id'], workspace['workspace_id'])['status'] == 'running'
    manager.finish_run(context['run_id'], 'task-2')
    assert manager.read(second['execution_id'], workspace['workspace_id'])['status'] == 'cancelled'


def test_reused_worktree_approval_names_current_employee(setup, monkeypatch):
    from deerflow.sandbox.powershell_tools import powershell_run
    manager, workspace, context, *_ = setup
    monkeypatch.setattr(terminal, 'authorize_execution', lambda *args: 'approval')
    runtime = SimpleNamespace(context={**context, 'agent_name': 'second', 'run_id': 'new-run'}, tool_call_id='approval', state={})
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(powershell_run.func, runtime, command='Write-Output accepted', yield_ms=10000)
        deadline = time.monotonic() + 10
        while not manager.approvals.pending():
            assert time.monotonic() < deadline
            time.sleep(.02)
        approval = manager.approvals.pending()[0]
        assert approval['employee_id'] == 'second'
        assert approval['run_id'] == 'new-run'
        assert approval['resolved_cwd'] == workspace['root_path']
        manager.approvals.decide(approval['approval_id'], approval['request_hash'], 'approve')
        assert pending.result(10).status == 'success'
