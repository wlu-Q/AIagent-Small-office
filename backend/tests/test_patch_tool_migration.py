import importlib.util
from pathlib import Path

import pytest
import yaml

from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, save_tool_manifest

spec = importlib.util.spec_from_file_location('patch_migration', Path(__file__).parents[1] / 'scripts/migrate_patch_tools.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def employee(root, name, tools, authority=None):
    path = root / 'users/alice/agents' / name
    path.mkdir(parents=True)
    (path / 'office.yaml').write_text('employee_id: ' + name)
    (path / 'config.yaml').write_text(yaml.safe_dump({'runtime_contract': {'authority': authority or {}}}))
    save_tool_manifest(path, EmployeeToolManifest(1, tuple(tools)), expected_revision='')
    return path


def host(root):
    path = root / 'config.yaml'
    path.write_text(yaml.safe_dump({'models': [{'name': 'unchanged', 'api_key': 'test-secret'}], 'tools': [
        {'name': n, 'use': f'deerflow.sandbox.tools:{n}_tool', 'group': 'file:write'} for n in ['write_file', 'str_replace']]}))
    return path


def test_dry_run_apply_idempotence_pm_and_disabled_choices(tmp_path):
    config = host(tmp_path)
    writer = employee(tmp_path, 'writer', ['read_file', 'write_file', 'str_replace', 'web_search'])
    pm = employee(tmp_path, 'pm', ['read_file', 'task'], {'can_delegate': True, 'denied_tool_groups': ['file:write']})
    disabled = employee(tmp_path, 'disabled', ['read_file'])
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.yaml')}
    result = migration.migrate(tmp_path, config)
    assert result['mode'] == 'dry_run' and all(p.read_bytes() == b for p, b in before.items())
    migration.migrate(tmp_path, config, apply=True)
    assert load_tool_manifest(writer).tools == ('apply_patch', 'read_file', 'web_search')
    assert 'apply_patch' not in load_tool_manifest(pm).tools and 'apply_patch' not in load_tool_manifest(disabled).tools
    assert yaml.safe_load(config.read_text())['models'][0]['api_key'] == 'test-secret'
    assert (writer / 'tools.yaml.before-apply-patch').read_bytes() == before[writer / 'tools.yaml']
    assert config.with_name('config.yaml.before-apply-patch').read_bytes() == before[config]
    assert migration.migrate(tmp_path, config, apply=True)['host_removed'] == []


def test_authority_conflict_aborts_all_and_revision_conflict_does_not_overwrite(tmp_path):
    config = host(tmp_path)
    path = employee(tmp_path, 'writer', ['write_file', 'read_file'], {'denied_tool_groups': ['file:write']})
    original = config.read_bytes()
    with pytest.raises(ValueError, match='denied'):
        migration.migrate(tmp_path, config, apply=True)
    assert config.read_bytes() == original
    (path / 'config.yaml').write_text('runtime_contract: {}')
    plan = migration.employee_plan(path)
    (path / 'tools.yaml').write_text('version: 1\ntools: [read_file]')
    with pytest.raises(RuntimeError, match='changed'):
        migration.apply_employee(plan)
    assert load_tool_manifest(path).tools == ('read_file',)


def test_exact_allowlist_preserved_and_failed_save_restores_config(tmp_path, monkeypatch):
    path = employee(tmp_path, 'writer', ['write_file', 'read_file'], {'allowed_configured_tool_names': ['write_file', 'read_file']})
    plan = migration.employee_plan(path)
    before = (path / 'config.yaml').read_bytes()
    def failure(*args, **kwargs):
        raise OSError('disk failure')
    monkeypatch.setattr(migration, 'save_tool_manifest', failure)
    with pytest.raises(OSError):
        migration.apply_employee(plan)
    assert (path / 'config.yaml').read_bytes() == before
    assert 'write_file' in load_tool_manifest(path).tools


def test_new_catalog_rejects_old_manifest_without_alias():
    from deerflow.office.tool_contract import resolve_employee_tools, ToolEntry
    from deerflow.office.authority import DEFAULT_AUTHORITY
    catalog = {name: ToolEntry(name, name, 'test') for name in ['read_file', 'report_to_pm', 'apply_patch']}
    with pytest.raises(ValueError, match='Unknown employee tool'):
        resolve_employee_tools(EmployeeToolManifest(1, ('read_file', 'report_to_pm', 'write_file')),
                               catalog=catalog, authority=DEFAULT_AUTHORITY)
