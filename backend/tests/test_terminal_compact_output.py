import json
import time
from types import SimpleNamespace

from deerflow.sandbox.powershell_tools import powershell_read, powershell_run
from test_office_terminal import setup as setup


def test_final_tail_survives_prefix_limit_and_redacts_secrets(setup, monkeypatch):
    secret = 'only-fake-terminal-regression-secret'
    monkeypatch.setattr('deerflow.sandbox.tools.read_active_secrets', lambda context: {'TEST_KEY': secret})
    runtime = SimpleNamespace(context=setup[2], tool_call_id='long-output', state={})
    command = "[Console]::WriteLine('x' * 300000); [Console]::WriteLine('" + secret + "'); throw '最后构建失败'"
    message = powershell_run.func(runtime, command=command)
    result = json.loads(message.content)
    assert message.status == 'error' and result['exit_code'] != 0
    assert len(result['stdout']) <= 16000
    assert '最后构建失败' in result['stderr']
    assert '[redacted]' in result['output_tail']['stdout']['text']
    assert result['output_tail']['stdout']['omitted_characters'] > 250000
    assert secret not in message.content
    assert not {'request_hash', 'process_identity', 'command', 'user_id', 'run_id', 'tool_call_id'} & result.keys()
    stored = setup[0]._get(result['execution_id'], setup[1]['workspace_id'])
    assert secret not in json.dumps(stored, ensure_ascii=False)
    assert stored['request_hash'] and stored['process_identity']
    page = powershell_read.func(runtime, execution_id=result['execution_id'], cursor=result['output_cursor'])
    followup = json.loads(page.content)
    assert followup['output_tail'] == result['output_tail']
    assert json.loads(followup['output_cursor'])['offsets'][0] == 32000


def test_default_command_waits_through_progress_once(setup):
    runtime = SimpleNamespace(context=setup[2], tool_call_id='wait-once', state={})
    started = time.monotonic()
    result = json.loads(powershell_run.func(runtime, command="Write-Output start; Start-Sleep -Milliseconds 1500; Write-Output finish").content)
    assert 1.3 <= time.monotonic() - started < 12
    assert result['status'] == 'succeeded'
    assert 'start' in result['stdout'] and 'finish' in result['stdout']


def test_public_read_default_waits_and_zero_does_not(setup):
    runtime = SimpleNamespace(context=setup[2], tool_call_id='read-wait', state={})
    result = json.loads(powershell_run.func(runtime, command="Write-Output start; Start-Sleep 2; Write-Output finish", yield_ms=0).content)
    started = time.monotonic()
    immediate = powershell_read.func(runtime, execution_id=result['execution_id'], wait_ms=0)
    assert time.monotonic() - started < 1
    assert json.loads(immediate.content)['status'] == 'running'
    completed = json.loads(powershell_read.func(runtime, execution_id=result['execution_id']).content)
    assert completed['status'] == 'succeeded' and 'finish' in completed['stdout']


def test_public_wait_schema_matches_runtime_limits():
    for tool, field in ((powershell_run, 'yield_ms'), (powershell_read, 'wait_ms')):
        schema = tool.args[field]
        choices = schema.get('anyOf', [schema])
        assert any(choice.get('maximum') == 120000 for choice in choices)
    assert powershell_run.args['yield_ms']['default'] is None
    assert powershell_read.args['wait_ms']['default'] == 30000
