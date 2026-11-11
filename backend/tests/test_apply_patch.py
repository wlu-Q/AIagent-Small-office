import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.sandbox import patch as engine, tools
from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.sandbox.patch_tool import apply_patch_tool
from patch_helpers import add_patch, update_patch


def execute(root, patch, **kwargs):
    return engine.apply_edits(engine.parse_patch(patch), resolve=lambda p: root / p.removeprefix('/'), **kwargs)


def test_create_update_multiple_hunks_delete_move(tmp_path):
    assert execute(tmp_path, add_patch('/目录/示例.txt', 'first\nold\nlast\n'))['status'] == 'success'
    patch = ('*** Begin Patch\n*** Update File: /目录/示例.txt\n*** Move to: /新目录/中文.txt\n'
             '@@\n first\n-old\n+changed\n@@\n-last\n+end\n*** End of File\n'
             '*** Add File: /empty.txt\n*** End Patch')
    result = execute(tmp_path, patch)
    assert result['status'] == 'success'
    assert (tmp_path / '新目录/中文.txt').read_text() == 'first\nchanged\nend\n'
    assert not (tmp_path / '目录/示例.txt').exists()
    assert (tmp_path / 'empty.txt').read_bytes() == b''
    assert execute(tmp_path, '*** Begin Patch\n*** Delete File: /empty.txt\n*** End Patch')['applied'] == [{'action': 'deleted', 'path': '/empty.txt'}]


@pytest.mark.parametrize('raw', [b'old\r\nkeep\r\n', b'old\nkeep\n', b'\xef\xbb\xbfold\r\nkeep', b'old\r\nkeep\n', b'old'])
def test_encoding_line_endings_bom_and_no_final_newline(tmp_path, raw):
    path = tmp_path / 'a'
    path.write_bytes(raw)
    result = execute(tmp_path, update_patch('/a', 'old', 'new'))
    assert result['status'] == 'success'
    assert path.read_bytes() == raw.replace(b'old', b'new')


@pytest.mark.parametrize('body', ['bad', '*** Update File: /a\n@@\nmissing prefix',
    '*** Add File: /../a\n+x', '*** Add File: /x/.git/config\n+x', '*** Add File: /x/NUL.txt\n+x',
    '*** Add File: /a:stream\n+x', '*** Add File: /a.\n+x', '*** Add File: relative\n+x',
    '*** Add File: /A\n+x\n*** Delete File: /a', '*** Delete File: /a\n+invalid'])
def test_invalid_patch_and_paths(body):
    with pytest.raises(ValueError):
        engine.parse_patch('*** Begin Patch\n' + body + '\n*** End Patch')


def test_preflight_all_files_no_changes_on_missing_context_or_destination(tmp_path):
    (tmp_path / 'a').write_text('keep\n')
    patch = add_patch('/new', 'new').removesuffix('*** End Patch') + '*** Update File: /a\n@@\n-wrong\n+new\n*** End Patch'
    assert execute(tmp_path, patch)['status'] == 'error'
    assert not (tmp_path / 'new').exists()
    assert (tmp_path / 'a').read_text() == 'keep\n'
    assert execute(tmp_path, add_patch('/a', 'overwrite'))['status'] == 'error'
    (tmp_path / 'b').write_text('existing')
    move = '*** Begin Patch\n*** Update File: /a\n*** Move to: /b\n*** End Patch'
    assert execute(tmp_path, move)['status'] == 'error'
    assert (tmp_path / 'b').read_text() == 'existing'


def test_ambiguous_context_refused_anchor_and_eof_disambiguate(tmp_path):
    (tmp_path / 'a').write_text('old\nanchor\nold\n')
    patch = update_patch('/a', 'old', 'new')
    assert execute(tmp_path, patch)['status'] == 'error'
    assert execute(tmp_path, patch.replace('@@\n', '@@ anchor\n'))['status'] == 'success'
    assert (tmp_path / 'a').read_text() == 'old\nanchor\nnew\n'


def test_partial_failure_and_cancel_report_actual_files(tmp_path, monkeypatch):
    original = engine._write
    def fail_second(path, *args, **kwargs):
        if path.name == 'b':
            raise PermissionError('simulated disk failure')
        original(path, *args, **kwargs)
    monkeypatch.setattr(engine, '_write', fail_second)
    patch = add_patch('/a', 'A').replace('*** End Patch', '') + add_patch('/b', 'B').replace('*** Begin Patch\n', '')
    result = execute(tmp_path, patch)
    assert result['status'] == 'partial' and result['applied'] == [{'action': 'created', 'path': '/a'}]
    assert (tmp_path / 'a').read_bytes() == b'A\n' and not (tmp_path / 'b').exists()
    result = execute(tmp_path, add_patch('/c', 'C'), cancelled=lambda: True)
    assert result['status'] == 'error' and not (tmp_path / 'c').exists()


def test_move_failure_truthfully_records_created_destination(tmp_path, monkeypatch):
    (tmp_path / 'a').write_text('keep\n')
    original = Path.unlink
    def fail_source(path, *args, **kwargs):
        if path == tmp_path / 'a':
            raise PermissionError('cannot remove source')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_source)
    result = execute(tmp_path, '*** Begin Patch\n*** Update File: /a\n*** Move to: /b\n*** End Patch')
    assert result['status'] == 'partial'
    assert result['applied'] == [{'action': 'created', 'path': '/b'}]
    assert (tmp_path / 'a').read_bytes() == (tmp_path / 'b').read_bytes()


def test_stale_during_preflight_never_overwrites(tmp_path):
    (tmp_path / 'a').write_text('old\n')
    def verify(_):
        (tmp_path / 'a').write_text('concurrent\n')
    result = execute(tmp_path, update_patch('/a', 'old', 'new'), verify=verify)
    assert result['status'] == 'error' and (tmp_path / 'a').read_text() == 'concurrent\n'


@pytest.mark.parametrize('raw', [b'\xff\xfeold', b'abc\x00def', b'x' * (engine.MAX_FILE_BYTES + 1)], ids=['encoding', 'binary', 'oversize'])
def test_non_utf8_binary_and_size_rejected(tmp_path, raw):
    (tmp_path / 'a').write_bytes(raw)
    result = execute(tmp_path, '*** Begin Patch\n*** Delete File: /a\n*** End Patch')
    assert result['status'] == 'error' and (tmp_path / 'a').read_bytes() == raw
    with pytest.raises(ValueError, match='split'):
        engine.parse_patch(add_patch('/b', 'x' * engine.MAX_PATCH_BYTES))


@pytest.fixture
def tool_runtime(tmp_path, monkeypatch):
    sandbox = LocalSandbox('patch-test')
    runtime = SimpleNamespace(context={}, state={'thread_data': {'workspace_path': str(tmp_path),
                'uploads_path': str(tmp_path / 'uploads'), 'outputs_path': str(tmp_path / 'outputs')}})
    monkeypatch.setattr(tools, 'ensure_sandbox_initialized', lambda _: sandbox)
    monkeypatch.setattr(tools, 'is_local_sandbox', lambda _: True)
    monkeypatch.setattr(tools, 'ensure_thread_directories_exist', lambda _: None)
    return runtime, sandbox


@pytest.mark.parametrize('path', ['/mnt/skills/x', '/mnt/acp-workspace/x', '/etc/x', '/mnt/user-data/../../x'])
def test_tool_reuses_path_protection(tool_runtime, path):
    runtime, _ = tool_runtime
    result = json.loads(apply_patch_tool.func(runtime, add_patch(path, 'x')).content)
    assert result['status'] == 'error' and not result['applied']


def test_tool_actual_results_and_read_only_mount(tool_runtime, tmp_path):
    runtime, sandbox = tool_runtime
    sandbox.path_mappings = [PathMapping('/protected', str(tmp_path), read_only=True)]
    result = json.loads(apply_patch_tool.func(runtime, add_patch('/mnt/user-data/workspace/a', 'hello')).content)
    assert result['status'] == 'error'


def test_tool_error_masks_host_path_and_bounds_details(tool_runtime, tmp_path, monkeypatch):
    runtime, _ = tool_runtime
    def fail(*args, **kwargs):
        raise OSError(str(tmp_path / 'file.txt') + ': ' + 'x' * 5000)
    monkeypatch.setattr(engine, '_write', fail)
    result = json.loads(apply_patch_tool.func(runtime, add_patch('/mnt/user-data/workspace/a', 'hello')).content)
    assert result['status'] == 'error'
    assert str(tmp_path) not in result['error'] and len(result['error']) <= 2000


def test_schema_is_normal_tool_and_no_old_editors():
    assert set(apply_patch_tool.tool_call_schema.model_fields) == {'patch', 'target'}
    assert not apply_patch_tool.tool_call_schema.model_fields['target'].is_required()
    assert not hasattr(tools, 'write_file_tool') and not hasattr(tools, 'str_replace_tool')


def test_result_status_is_structured_even_for_partial_failure(tool_runtime, monkeypatch):
    runtime, _ = tool_runtime
    runtime.tool_call_id = 'actual-call-id'
    original = engine._write
    def fail_second(path, *args, **kwargs):
        if path.name == 'b':
            raise OSError('disk failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(engine, '_write', fail_second)
    patch = add_patch('/mnt/user-data/workspace/a', 'A').replace('*** End Patch', '') + add_patch('/mnt/user-data/workspace/b', 'B').replace('*** Begin Patch\n', '')
    result = apply_patch_tool.func(runtime, patch)
    assert result.status == 'error' and result.tool_call_id == 'actual-call-id'
    assert result.additional_kwargs['deerflow_tool_meta']['status'] == 'partial_success'
    assert json.loads(result.content)['applied'] == [{'action': 'created', 'path': '/mnt/user-data/workspace/a'}]


def test_empty_file_insertion_and_context_without_final_newline(tmp_path):
    (tmp_path / 'a').write_bytes(b'')
    assert execute(tmp_path, update_patch('/a', '', 'hello'))['status'] == 'success'
    (tmp_path / 'a').write_bytes(b'hello')
    patch = '*** Begin Patch\n*** Update File: /a\n@@\n hello\n+world\n*** End Patch'
    assert execute(tmp_path, patch)['status'] == 'success'
    assert (tmp_path / 'a').read_bytes() == b'hello\nworld'


def test_overlapping_paths_fail_before_first_write(tmp_path):
    patch = add_patch('/a', 'parent').replace('*** End Patch', '') + add_patch('/a/b', 'child').replace('*** Begin Patch\n', '')
    assert execute(tmp_path, patch)['status'] == 'error'
    assert not (tmp_path / 'a').exists()


def test_published_write_followed_by_cleanup_failure_reports_actual_change(tmp_path, monkeypatch):
    original = engine._write
    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('cleanup failed')
    monkeypatch.setattr(engine, '_write', write_then_fail)
    result = execute(tmp_path, add_patch('/a', 'saved'))
    assert result['status'] == 'partial' and result['applied'] == [{'action': 'created', 'path': '/a'}]
    assert (tmp_path / 'a').read_bytes() == b'saved\n'


def test_symlink_escape_rejected(tool_runtime, tmp_path):
    runtime, _ = tool_runtime
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    try:
        try:
            (tmp_path / 'escape').symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip('Host does not allow symlink creation')
        result = json.loads(apply_patch_tool.func(runtime, add_patch('/mnt/user-data/workspace/escape/file', 'no')).content)
        assert result['status'] == 'error' and not (outside / 'file').exists()
    finally:
        outside.rmdir()
