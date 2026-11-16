import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('prepare_release', Path(__file__).with_name('prepare.py'))
prepare_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_release)


class PreparationTests(unittest.TestCase):
    def test_stages_same_application_and_only_native_runtime_entries(self):
        for system in ('win32', 'darwin'):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                build = root / 'output/packaging'
                resources = build / 'resources'

                def write(relative, text='fixture'):
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding='utf-8')
                    target.chmod(0o755)

                write('package.json', json.dumps({'name': 'test-office', 'version': '1.0.0', 'description': 'test'}))
                write('node_modules/i18next/package.json', '{"version":"1.0.0"}')
                for relative in (
                    'node_modules/i18next/dist/umd/i18next.js', 'node_modules/i18next/LICENSE',
                    'contracts/test.json', 'client/dist/index.html', 'client/src/app.ts',
                    'backend/app/app.py', 'backend/office_library/service.py',
                    'backend/packages/harness/deerflow/__init__.py', 'backend/app/.env',
                    'skills/public/example/SKILL.md', 'desktop/packaging/defaults/config.yaml',
                    'desktop/packaging/initialize.py', 'LICENSE', 'backend/DEERFLOW_LICENSE',
                ):
                    write(relative)
                for name in ('main', 'preload', 'gateway-env', 'runtime-paths', 'process-lifecycle',
                             'dev-startup', 'packaged-entry', 'localization'):
                    write(f'desktop/{name}.cjs')
                entries = ('python/python.exe', 'node/node.exe', 'git/cmd/git.exe', 'powershell/pwsh.exe') if system == 'win32' else (
                    'python/bin/python3', 'node/bin/node', 'git/bin/git', 'powershell/pwsh')
                for relative in entries:
                    write(f'output/packaging/resources/{relative}')
                with patch.multiple(prepare_release, ROOT=root, BUILD=build, RESOURCES=resources), \
                        patch.object(prepare_release, 'sys', SimpleNamespace(platform=system)):
                    prepare_release.prepare()
                    self.assertTrue((build / 'app/desktop/runtime-paths.cjs').is_file())
                    self.assertTrue((resources / 'backend/app/app.py').is_file())
                    self.assertFalse((resources / 'backend/app/.env').exists())
                    manifest = json.loads((resources / 'release-manifest.json').read_text(encoding='utf-8'))
                    self.assertEqual(manifest['platform'], system)
                    if system == 'darwin':
                        with patch.object(prepare_release.os, 'access', return_value=False):
                            with self.assertRaisesRegex(RuntimeError, 'not executable'):
                                prepare_release.prepare()
                    (resources / entries[-1]).unlink()
                    with self.assertRaisesRegex(RuntimeError, 'Missing bundled runtime'):
                        prepare_release.prepare()


if __name__ == '__main__':
    unittest.main()
