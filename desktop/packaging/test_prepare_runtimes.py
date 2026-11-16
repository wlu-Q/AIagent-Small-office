import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('prepare_runtimes', Path(__file__).with_name('prepare-runtimes.py'))
runtimes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtimes)


class RuntimePreparationTests(unittest.TestCase):
    def test_selects_native_platform_and_rejects_unsupported_architecture(self):
        windows = runtimes.runtime_spec('win32', 'AMD64')
        mac = runtimes.runtime_spec('darwin', 'arm64')
        self.assertIn('win-x64', windows['node']['url'])
        self.assertIn('darwin-arm64', mac['node']['url'])
        self.assertIn('macOS-arm64', mac['git']['url'])
        self.assertIn('osx-arm64', mac['powershell']['url'])
        for spec in (*windows.values(), *mac.values()):
            self.assertRegex(spec['sha256'], r'^[a-f0-9]{64}$')
            self.assertTrue(spec['url'].startswith('https://'))
        for system, machine in [('darwin', 'x86_64'), ('win32', 'ARM64'), ('linux', 'aarch64')]:
            with self.assertRaisesRegex(RuntimeError, 'native macOS arm64'):
                runtimes.runtime_spec(system, machine)

    def test_tar_strips_node_root_and_preserves_executable_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'node.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                entry = tarfile.TarInfo('node-version/bin/node')
                entry.mode = 0o755
                entry.size = 4
                bundle.addfile(entry, io.BytesIO(b'node'))
            runtimes.extract_tar(archive, root / 'runtime', strip_root=True)
            executable = root / 'runtime/bin/node'
            self.assertEqual(executable.read_bytes(), b'node')
            if os.name != 'nt':
                self.assertTrue(os.access(executable, os.X_OK))

    def test_tar_rejects_paths_and_links_outside_destination(self):
        for name, link in [('../escape', ''), ('/absolute', ''), ('link', '../../escape'), ('link', '/escape')]:
            with self.subTest(name=name, link=link), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as bundle:
                    entry = tarfile.TarInfo(name)
                    if link:
                        entry.type = tarfile.SYMTYPE
                        entry.linkname = link
                    bundle.addfile(entry)
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    runtimes.extract_tar(archive, root / 'runtime')
                self.assertFalse((root / 'escape').exists())

    def test_windows_zip_layout_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'node.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('node-version/node.exe', b'node')
            runtimes.extract_zip(archive, root / 'runtime', strip_root=True)
            self.assertEqual((root / 'runtime/node.exe').read_bytes(), b'node')

    def test_invalid_download_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtimes, 'CACHE', Path(directory)), \
                patch.object(runtimes, 'request', return_value=io.BytesIO(b'bad')):
            with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
                runtimes.download('https://example.test/runtime', 'runtime.tar.gz', '0' * 64)
            self.assertFalse((Path(directory) / 'runtime.tar.gz').exists())

    def test_native_prepare_uses_locked_dependencies_and_platform_python(self):
        for system, machine, suffix, executable in [
            ('win32', 'AMD64', 'windows-x86_64-none', 'python.exe'),
            ('darwin', 'arm64', 'macos-aarch64-none', 'bin/python3'),
        ]:
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'backend').mkdir()
                (root / 'backend/.python-version').write_text('3.12.13')
                (root / 'backend/pyproject.toml').write_text('[tool.uv]\nrequired-version = "==0.11.28"\n')
                (root / 'backend/uv.lock').write_text('test-lock')
                build = root / 'build'
                cache = build / 'cache'
                resources = build / 'resources'
                python_source = cache / 'python' / f'cpython-3.12.13-{suffix}'
                def run_command(*args, **kwargs):
                    if args[:3] == ('uv', 'python', 'install'):
                        target = python_source / executable
                        target.parent.mkdir(parents=True)
                        target.write_text('python')
                with patch.multiple(runtimes, ROOT=root, BUILD=build, CACHE=cache, RESOURCES=resources), \
                        patch.object(runtimes, 'sys', SimpleNamespace(platform=system)), \
                        patch.object(runtimes.platform, 'machine', return_value=machine), \
                        patch.object(runtimes.subprocess, 'check_output', return_value='uv 0.11.28'), \
                        patch.object(runtimes.subprocess, 'run') as export, \
                        patch.object(runtimes, 'run', side_effect=run_command) as run, \
                        patch.object(runtimes, 'require_version') as version, \
                        patch.object(runtimes, 'install_runtime', side_effect=lambda name, specs: resources / name):
                    runtimes.prepare()
                    python_call = run.call_args_list[0].args
                    self.assertEqual(python_call[3], f'cpython-3.12.13-{suffix}')
                    self.assertEqual('--no-registry' in python_call, system == 'win32')
                    pip_call = next(call.args for call in run.call_args_list if call.args[:3] == ('uv', 'pip', 'sync'))
                    self.assertEqual(pip_call[4], resources / 'python' / executable)
                    self.assertIn('--require-hashes', pip_call)
                    self.assertIn('--locked', export.call_args.args[0])
                    self.assertEqual(version.call_args_list[0].args[0], resources / 'python' / executable)
                    self.assertTrue((resources / 'python' / executable).is_file())


if __name__ == '__main__':
    unittest.main()
