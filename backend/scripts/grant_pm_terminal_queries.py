"""Grant PM terminal queries offline, without granting command execution. Default: dry-run."""
import argparse
from pathlib import Path

import yaml

from deerflow.office.authority import normalize_authority
from deerflow.office.tool_contract import TERMINAL_QUERIES
from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, manifest_revision, save_tool_manifest


def migrate(data_dir: Path, *, apply=False):
    changed = []
    for path in sorted(data_dir.glob('users/*/agents/*/tools.yaml')):
        config = yaml.safe_load(path.with_name('config.yaml').read_text(encoding='utf-8'))
        authority = normalize_authority((config.get('runtime_contract') or {}).get('authority'))
        if not authority.can_delegate:
            continue
        manifest = load_tool_manifest(path.parent)
        names = set(manifest.tools) | set(TERMINAL_QUERIES)
        if names == set(manifest.tools):
            continue
        changed.append(str(path.relative_to(data_dir)))
        if apply:
            backup = path.with_name('tools.before-terminal-queries.yaml')
            if not backup.exists():
                with backup.open('xb') as stream:
                    stream.write(path.read_bytes())
            save_tool_manifest(path.parent, EmployeeToolManifest(manifest.version, tuple(sorted(names))),
                               expected_revision=manifest_revision(manifest))
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--maintenance', action='store_true', help='Gateway is stopped; start fresh Runs after migration')
    arguments = parser.parse_args()
    if arguments.apply and not arguments.maintenance:
        parser.error('--apply requires --maintenance with the Gateway stopped')
    print({'mode': 'applied' if arguments.apply else 'dry-run',
           'employees': migrate(arguments.data_dir, apply=arguments.apply)})
