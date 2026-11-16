"""Run with the bundled interpreter after prepare.py; no developer dependencies."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / 'output/packaging/resources'
sys.path[:0] = [str(RESOURCES / 'backend'), str(RESOURCES / 'backend/packages/harness')]
spec = importlib.util.spec_from_file_location('office_installation', RESOURCES / 'initialize.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def desktop_environment(resources):
    env = {**os.environ, 'OFFICE_PACKAGED_RESOURCES': str(resources)}
    result = subprocess.run(
            [str(RESOURCES / ('node/node.exe' if sys.platform == 'win32' else 'node/bin/node')), '-e',
             "process.stdout.write(JSON.stringify(require('./desktop/gateway-env.cjs').gatewayEnv('127.0.0.1', '8001')))"],
            cwd=ROOT, env=env, capture_output=True, encoding='utf-8', check=True,
    )
    return json.loads(result.stdout)


class ReleaseTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows installation paths')
    def test_long_installation_path_can_load_database_migrations(self):
        # Use extended paths only to create the fixture; the application gets the
        # normal installation path, just as Electron does on a mapped drive.
        parent = '\\\\?\\' + str(ROOT / 'output/packaging')
        with tempfile.TemporaryDirectory(prefix='long-install-', dir=parent) as directory:
            long_resources = Path(directory) / ('安装目录' * 30)
            harness = long_resources / 'backend/packages/harness'
            shutil.copytree(RESOURCES / 'backend/packages/harness', harness,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            normal_resources = str(long_resources).removeprefix('\\\\?\\')
            migration = Path(normal_resources) / 'backend/packages/harness/deerflow/persistence/migrations/versions/0001_baseline.py'
            self.assertGreater(len(str(migration)), 260)
            env = desktop_environment(normal_resources)
            # Relocate backend files only; use the actual bundled interpreter.
            env['PYTHONHOME'] = '\\\\?\\' + str(RESOURCES / 'python')
            result = subprocess.run(
                [sys.executable, '-c', 'from deerflow.persistence.bootstrap import _get_head_revision; print(_get_head_revision())'],
                cwd=ROOT / 'output', env=env,
                capture_output=True, encoding='utf-8',
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('0008_scheduled_task_model_profile', result.stdout)

    def test_standalone_initializer_uses_desktop_environment(self):
        with tempfile.TemporaryDirectory(prefix='office-script-test-') as directory:
            subprocess.run([sys.executable, str(RESOURCES / 'initialize.py'), str(RESOURCES), directory],
                           cwd=directory, env=desktop_environment(RESOURCES), capture_output=True, check=True)
            self.assertTrue((Path(directory) / 'config.yaml').exists())
            self.assertFalse((Path(directory) / 'state').exists())

    def test_first_install_and_existing_settings_are_preserved(self):
        from office_library.service import LibraryService
        import yaml
        with tempfile.TemporaryDirectory(prefix='office-install-test-') as directory:
            data = Path(directory)
            installer.initialize(RESOURCES, data)
            config = yaml.safe_load((data / 'config.yaml').read_text(encoding='utf-8'))
            self.assertEqual(config['models'], [])
            self.assertFalse(config['token_budget']['enabled'])
            self.assertFalse(config['subagents']['token_budget']['enabled'])
            self.assertEqual(config['summarization']['trigger'], [{'type': 'tokens', 'value': 257000}])
            self.assertNotIn('office_model_records', config)
            self.assertFalse((data / 'state/users/default/office-bootstrap.yaml').exists())
            library = LibraryService(data / 'state/users/default/office-library')
            if sys.platform == 'darwin':
                from keyring.backends.macOS import Keyring
                account = hashlib.sha256(str(library.root).encode()).hexdigest()
                def remove_test_key():
                    keychain = Keyring()
                    if keychain.get_password('small-office.library', account) is not None:
                        keychain.delete_password('small-office.library', account)
                self.addCleanup(remove_test_key)
            for kind in ('embedding', 'rerank'):
                self.assertFalse(library.settings()[f'{kind}ApiKeyConfigured'])
            self.assertFalse((RESOURCES / 'defaults/library.json').exists())
            with library.db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 0)
            library.save_settings({'embedding_api_key': 'test-user-embedding', 'rerank_api_key': 'test-user-rerank'})
            (data / 'config.yaml').write_text('models: []\n# user-owned\n', encoding='utf-8')
            installer.initialize(RESOURCES, data)
            self.assertEqual(library._config()['embedding_api_key'], 'test-user-embedding')
            self.assertEqual(library._config()['rerank_api_key'], 'test-user-rerank')
            self.assertIn('# user-owned', (data / 'config.yaml').read_text(encoding='utf-8'))

    def test_search_is_unconfigured_and_release_contains_no_shared_keys(self):
        import yaml
        staged = yaml.safe_load((RESOURCES / 'defaults/config.yaml').read_text(encoding='utf-8'))
        search = next(tool for tool in staged['tools'] if tool['name'] == 'web_search')
        self.assertNotIn('api_key', search)
        self.assertEqual(search['fallback'], 'ddgs')
        self.assertEqual(staged['models'], [])
        manifest = json.loads((RESOURCES / 'release-manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(manifest['search_shared_key_included'])
        self.assertFalse(manifest['rag_shared_key_included'])

    def test_pm_template_and_skills_use_current_source(self):
        from deerflow.office.templates import get_employee_template
        pm = get_employee_template('pm')
        relative = Path('backend/packages/harness/deerflow/office/templates.py')
        self.assertEqual((ROOT / relative).read_bytes(), (RESOURCES / relative).read_bytes())
        for name in pm.skills:
            self.assertTrue((RESOURCES / 'skills/public' / name / 'SKILL.md').is_file())

    def test_staged_application_matches_current_sources(self):
        for relative in ('backend/app', 'backend/office_library', 'backend/packages/harness/deerflow',
                         'skills/public', 'contracts', 'client/dist'):
            destination = ROOT / 'output/packaging/app' if relative.startswith(('contracts', 'client/')) else RESOURCES
            for source in (ROOT / relative).rglob('*'):
                if not source.is_file() or '__pycache__' in source.parts or source.suffix == '.pyc':
                    continue
                if source.name in ('_skillhub_meta.json', '.env') or source.name.startswith('.env.'):
                    continue
                target = destination / source.relative_to(ROOT)
                self.assertTrue(target.is_file(), str(target))
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),
                                 hashlib.sha256(target.read_bytes()).digest(), str(source))
        for staged in (ROOT / 'output/packaging/app/desktop').glob('*.cjs'):
            self.assertEqual(staged.read_bytes(), (ROOT / 'desktop' / staged.name).read_bytes(), staged.name)

    def test_runtime_defaults_match_tracked_template(self):
        self.assertEqual((RESOURCES / 'defaults/config.yaml').read_bytes(),
                         (ROOT / 'desktop/packaging/defaults/config.yaml').read_bytes())

    def test_no_private_state_is_in_release_resources(self):
        for base in (RESOURCES / 'backend', RESOURCES / 'skills'):
            for item in base.rglob('*'):
                self.assertNotIn(item.name, {'.deer-flow', '.env', '.git', 'office-bootstrap.yaml'})
                self.assertNotIn(item.suffix.lower(), {'.sqlite', '.db', '.log'})
        manifest = json.loads((RESOURCES / 'release-manifest.json').read_text(encoding='utf-8'))
        for filename, expected in manifest['source_files'].items():
            self.assertEqual(hashlib.sha256((ROOT / filename).read_bytes()).hexdigest(), expected,
                             f'Source changed since staging: {filename}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
