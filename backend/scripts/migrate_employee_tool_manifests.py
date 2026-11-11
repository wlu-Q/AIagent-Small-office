"""Explicit offline migration. Dry-run by default; never used by runtime loaders."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

from deerflow.office.authority import normalize_authority
from deerflow.office.tool_contract import build_tool_catalog, default_tool_manifest, resolve_employee_tools
from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, save_tool_manifest


def migrate_employee(agent_dir: Path, *, apply: bool = False, app_config=None):
    config_path = agent_dir / 'config.yaml'
    office = yaml.safe_load((agent_dir / 'office.yaml').read_text(encoding='utf-8'))
    if (agent_dir / 'tools.yaml').exists():
        return {'employee': agent_dir.name, 'status': 'already_migrated', 'tools': list(load_tool_manifest(agent_dir).tools)}
    if (office.get('projection') or {}).get('state') == 'working':
        raise RuntimeError(f'{agent_dir.name}: active/interrupted work must finish before migration')
    original = config_path.read_bytes()
    config = yaml.safe_load(original)
    contract = config.get('runtime_contract') or {}
    authority = normalize_authority(contract.get('authority'))
    groups = config.get('tool_groups')
    if not isinstance(groups, list):
        raise ValueError(f'{agent_dir.name}: explicit legacy tool_groups required')
    duplicate = contract.get('tool_groups')
    if duplicate is not None and set(duplicate) != set(groups):
        raise ValueError(f'{agent_dir.name}: conflicting legacy tool groups')
    manifest = default_tool_manifest(groups, authority, app_config=app_config)
    catalog = build_tool_catalog(app_config, model_name=config.get('model'))
    selected = set(manifest.tools)
    for name in contract.get('authorized_tool_names') or []:
        matches = [e.id for e in catalog.values() if e.name == name]
        if len(matches) != 1:
            raise ValueError(f'{agent_dir.name}: ambiguous/unavailable external tool {name}')
        selected.add(matches[0])
    if contract.get('disabled_tools'):
        raise ValueError(f'{agent_dir.name}: disabled_tools needs explicit reconciliation')
    manifest = EmployeeToolManifest(1, tuple(sorted(selected)))
    resolved = resolve_employee_tools(manifest, catalog=catalog, authority=authority)
    result = {'employee': agent_dir.name, 'status': 'migrated' if apply else 'dry_run',
              'legacyGroups': groups, 'tools': list(manifest.tools),
              'available': list(resolved.runtime_names), 'unavailable': resolved.unavailable_reasons,
              'note': 'Skill filters are intentionally excluded; review restored PM and framework tools.'}
    if not apply:
        return result
    backup = agent_dir / 'config.before-tools-migration.yaml'
    if backup.exists() and backup.read_bytes() != original:
        raise RuntimeError('Migration backup differs; recover explicitly before retry')
    backup.write_bytes(original)
    config.pop('tool_groups', None)
    for key in ('tool_groups', 'authorized_tool_names', 'runtime_tool_names', 'unavailable_tool_groups', 'tool_availability_reasons', 'disabled_tools'):
        contract.pop(key, None)
    contract.get('authority', {}).pop('allowed_configured_tool_names', None)
    config['runtime_contract'] = contract
    temporary = agent_dir / '.config-tools-migration.tmp'
    try:
        save_tool_manifest(agent_dir, manifest, expected_revision='')
        temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
        os.replace(temporary, config_path)
    except Exception:
        config_path.write_bytes(original)
        (agent_dir / 'tools.yaml').unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--maintenance', action='store_true', help='Gateway has been stopped and active work has been checked')
    args = parser.parse_args()
    if args.apply and not args.maintenance:
        parser.error('--apply requires --maintenance after stopping Gateway and checking active Runs')
    failures = []
    for path in sorted(args.data_dir.glob('users/*/agents/*/office.yaml')):
        try:
            print(json.dumps(migrate_employee(path.parent, apply=args.apply), ensure_ascii=False))
        except (ValueError, RuntimeError, OSError) as exc:
            failures.append(str(exc))
    if failures:
        print(json.dumps({'errors': failures}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
