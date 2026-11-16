"""Whitelist release inputs. Never copy the developer's databases or chat keys."""
import hashlib
import json
import shutil
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / 'output' / 'packaging'
RESOURCES = BUILD / 'resources'
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', '.git', '.env', '.env.*',
                               'node_modules', '.venv', '_skillhub_meta.json')


def reset_directory(path):
    resolved = path.resolve()
    if not resolved.is_relative_to(BUILD.resolve()) or resolved == BUILD.resolve():
        raise ValueError('Refusing to reset a directory outside this build')
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def copy(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=IGNORE)


def prepare():
    app = BUILD / 'app'
    reset_directory(app)
    for name in ('backend', 'defaults', 'skills'):
        reset_directory(RESOURCES / name)
    (app / 'desktop').mkdir()
    for name in ('main.cjs', 'preload.cjs', 'gateway-env.cjs', 'runtime-paths.cjs', 'process-lifecycle.cjs', 'dev-startup.cjs', 'packaged-entry.cjs', 'localization.cjs'):
        shutil.copy2(ROOT / 'desktop' / name, app / 'desktop' / name)
    # Ship the same pinned i18next implementation used during development.
    translation_runtime = app / 'node_modules/i18next'
    translation_runtime.mkdir(parents=True)
    runtime_package = json.loads((ROOT / 'node_modules/i18next/package.json').read_text(encoding='utf-8'))
    (translation_runtime / 'package.json').write_text(json.dumps({
        'name': 'i18next', 'version': runtime_package['version'], 'main': 'index.js', 'license': 'MIT',
    }), encoding='utf-8')
    shutil.copy2(ROOT / 'node_modules/i18next/dist/umd/i18next.js', translation_runtime / 'index.js')
    shutil.copy2(ROOT / 'node_modules/i18next/LICENSE', translation_runtime / 'LICENSE')
    copy(ROOT / 'contracts', app / 'contracts')
    copy(ROOT / 'client/dist', app / 'client/dist')
    package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    (app / 'package.json').write_text(json.dumps({
        'name': package['name'], 'version': package['version'], 'description': package['description'],
        'author': 'Small Office', 'license': 'MIT', 'main': 'desktop/packaged-entry.cjs',
        'dependencies': {'i18next': runtime_package['version']},
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    for relative in ('app', 'office_library', 'packages/harness/deerflow'):
        copy(ROOT / 'backend' / relative, RESOURCES / 'backend' / relative)
    copy(ROOT / 'skills/public', RESOURCES / 'skills/public')
    copy(ROOT / 'desktop/packaging/defaults', RESOURCES / 'defaults')
    shutil.copy2(ROOT / 'desktop/packaging/initialize.py', RESOURCES / 'initialize.py')
    shutil.copy2(ROOT / 'LICENSE', RESOURCES / 'LICENSE')
    shutil.copy2(ROOT / 'backend/DEERFLOW_LICENSE', RESOURCES / 'DEERFLOW_LICENSE')
    entries = ('python/python.exe', 'node/node.exe', 'git/cmd/git.exe', 'powershell/pwsh.exe') if sys.platform == 'win32' else (
        'python/bin/python3', 'node/bin/node', 'git/bin/git', 'powershell/pwsh')
    for relative in entries:
        if not (RESOURCES / relative).is_file():
            raise RuntimeError(f'Missing bundled runtime: {relative}')
        if sys.platform == 'darwin' and not os.access(RESOURCES / relative, os.X_OK):
            raise RuntimeError(f'Bundled runtime is not executable: {relative}')
    # Record current source hashes for release verification; no private configuration is included.
    hashes = {str(f.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(f.read_bytes()).hexdigest()
              for base in ('backend/app', 'backend/packages/harness/deerflow', 'backend/office_library', 'client/src')
              for f in (ROOT / base).rglob('*') if f.is_file() and '__pycache__' not in f.parts and f.suffix != '.pyc'}
    manifest = {'version': package['version'], 'platform': sys.platform, 'source_files': hashes,
                'chat_model_count': 0, 'rag_shared_key_included': False,
                'search_shared_key_included': False, 'release_data_overrides': []}
    (RESOURCES / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Release staging ready: tracked defaults, no developer configuration or credentials.')


if __name__ == '__main__':
    prepare()
