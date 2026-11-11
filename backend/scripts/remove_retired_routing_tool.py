"""Remove retired resolve_employee_routing selections offline. Default: dry-run."""
import argparse
from pathlib import Path

from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, manifest_revision, save_tool_manifest


def migrate(data_dir: Path, *, apply=False):
    removed = []
    for path in sorted(data_dir.glob('users/*/agents/*/tools.yaml')):
        manifest = load_tool_manifest(path.parent)
        if 'resolve_employee_routing' not in manifest.tools:
            continue
        removed.append(str(path.relative_to(data_dir)))
        if apply:
            backup = path.with_name('tools.before-routing-removal.yaml')
            if not backup.exists():
                with backup.open('xb') as stream:
                    stream.write(path.read_bytes())
            updated = EmployeeToolManifest(manifest.version, tuple(name for name in manifest.tools if name != 'resolve_employee_routing'))
            save_tool_manifest(path.parent, updated, expected_revision=manifest_revision(manifest))
    return removed


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
