"""One-time additive tool migration. Run with Gateway stopped, never re-grant opt-outs."""
import argparse
from pathlib import Path
import yaml
from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, manifest_revision, save_tool_manifest


def migrate(root: Path):
    changed = []
    for office in root.glob('users/*/agents/*/office.yaml'):
        directory = office.parent
        marker = directory / '.office-library-tool-v1'
        if marker.exists():
            continue
        state = yaml.safe_load(office.read_text(encoding='utf-8')) or {}
        if state.get('projection', {}).get('state') == 'working':
            raise RuntimeError(f'{directory.name}: employee is working; finish/cancel work before migration')
        current = load_tool_manifest(directory)
        tools = tuple(sorted(set(current.tools) | {'search_office_library'}))
        save_tool_manifest(directory, EmployeeToolManifest(1, tools), expected_revision=manifest_revision(current))
        marker.write_text('1\n', encoding='ascii')
        changed.append(directory.name)
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--maintenance', action='store_true', required=True)
    args = parser.parse_args()
    print(migrate(args.data_dir))
