"""Offline, explicit migration from old AI editors to apply_patch. Default: dry-run."""
import argparse
import json
import os
import tempfile
from pathlib import Path

import yaml

from deerflow.office.authority import normalize_authority, validate_tool_authority
from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, manifest_revision, save_tool_manifest

OLD = {'write_file', 'str_replace'}


def backup(path, original):
    target = path.with_name(path.name + '.before-apply-patch')
    if target.exists():
        if target.read_bytes() != original:
            raise RuntimeError(f'Backup differs: {target.name}; recover explicitly')
    else:
        with target.open('xb') as stream:
            stream.write(original)


def replace_bytes(path, data):
    fd, name = tempfile.mkstemp(prefix='.patch-migration-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def employee_plan(agent_dir):
    manifest = load_tool_manifest(agent_dir)
    config_path = agent_dir / 'config.yaml'
    original = config_path.read_bytes()
    config = yaml.safe_load(original)
    authority_data = (config.get('runtime_contract') or {}).get('authority') or {}
    old = set(manifest.tools) & OLD
    selection = (set(manifest.tools) - OLD) | ({'apply_patch'} if old else set())
    if old:
        authority = normalize_authority(authority_data)
        validate_tool_authority(authority, tool_name='apply_patch', tool_group='file:write', permissions=())
        allowed = authority.allowed_configured_tool_names
        if allowed is not None and not old.issubset(allowed):
            raise ValueError(f'{agent_dir.name}: old editor conflicts with exact Authority allowlist')
    allowed = authority_data.get('allowed_configured_tool_names')
    if allowed is not None and set(allowed) & OLD:
        authority_data['allowed_configured_tool_names'] = sorted((set(allowed) - OLD) | {'apply_patch'})
    updated = EmployeeToolManifest(1, tuple(sorted(selection)))
    return {'agent_dir': agent_dir, 'manifest': manifest, 'updated': updated, 'original': original,
            'config_bytes': yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode('utf-8'),
            'config_changed': allowed is not None and bool(set(allowed) & OLD),
            'report': {'employee': agent_dir.name, 'revision': manifest_revision(manifest),
                       'removed': sorted(old), 'added': sorted(selection - set(manifest.tools)),
                       'authority_updated': allowed is not None and bool(set(allowed) & OLD)}}


def apply_employee(plan):
    root = plan['agent_dir']
    if manifest_revision(load_tool_manifest(root)) != plan['report']['revision'] or (root / 'config.yaml').read_bytes() != plan['original']:
        raise RuntimeError('Employee changed after preflight; rerun dry-run')
    if not plan['report']['removed'] and not plan['config_changed']:
        return
    backup(root / 'tools.yaml', (root / 'tools.yaml').read_bytes())
    backup(root / 'config.yaml', plan['original'])
    try:
        if plan['config_changed']:
            replace_bytes(root / 'config.yaml', plan['config_bytes'])
        save_tool_manifest(root, plan['updated'], expected_revision=plan['report']['revision'])
    except Exception:
        if plan['config_changed'] and (root / 'config.yaml').read_bytes() == plan['config_bytes']:
            replace_bytes(root / 'config.yaml', plan['original'])
        raise


def migrate(data_dir, config_path, *, apply=False):
    raw = config_path.read_bytes()
    config = yaml.safe_load(raw)
    entries = config.get('tools', [])
    legacy = [entry for entry in entries if entry.get('name') in OLD]
    if legacy:
        for entry in legacy:
            if entry.get('use') != f"deerflow.sandbox.tools:{entry['name']}_tool" or entry.get('group') != 'file:write' or entry.get('permissions'):
                raise ValueError('Customized editor registration requires explicit policy reconciliation')
        if any(entry.get('name') == 'apply_patch' for entry in entries):
            raise ValueError('Both old and new editors registered; reconcile before migration')
        config['tools'] = [entry for entry in entries if entry.get('name') not in OLD] + [
            {'name': 'apply_patch', 'group': 'file:write', 'use': 'deerflow.sandbox.patch_tool:apply_patch_tool'}]
    elif not any(entry.get('name') == 'apply_patch' for entry in entries):
        raise ValueError('No apply_patch tool registration; configure it before migrating employee choices')
    plans = [employee_plan(p.parent) for p in sorted(data_dir.glob('users/*/agents/*/office.yaml'))]
    if apply:
        if config_path.read_bytes() != raw:
            raise RuntimeError('Host config changed after preflight')
        if legacy:
            backup(config_path, raw)
            replace_bytes(config_path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode('utf-8'))
        for plan in plans:
            apply_employee(plan)
    return {'mode': 'applied' if apply else 'dry_run', 'host_removed': [e['name'] for e in legacy],
            'employees': [p['report'] for p in plans]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--maintenance', action='store_true', help='Gateway stopped; active and interrupted Runs checked')
    args = parser.parse_args()
    if args.apply and not args.maintenance:
        parser.error('--apply requires --maintenance; stop Gateway and check active/ask Runs first')
    print(json.dumps(migrate(args.data_dir, args.config, apply=args.apply), ensure_ascii=False))


if __name__ == '__main__':
    main()
