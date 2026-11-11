"""Assemble relocatable Windows runtimes in ignored build output."""
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / 'output' / 'packaging'
CACHE = BUILD / 'cache'
RESOURCES = BUILD / 'resources'
RUNTIMES = json.loads((Path(__file__).with_name('runtimes.json')).read_text(encoding='utf-8'))


def run(*args, cwd=ROOT):
    subprocess.run([str(x) for x in args], cwd=cwd, check=True)


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'SmallOffice-Packager'}), timeout=120)


def download(url, filename, expected):
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / filename
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return target
    print('Downloading', filename, flush=True)
    with request(url) as response, target.with_suffix('.part').open('wb') as output:
        shutil.copyfileobj(response, output)
    temporary = target.with_suffix('.part')
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f'Checksum mismatch: {filename}')
    temporary.replace(target)
    return target


def extract_zip(archive, destination, strip_root=False):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            parts = Path(item.filename).parts[1:] if strip_root else Path(item.filename).parts
            if not parts:
                continue
            target = destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('Archive path escapes its destination')
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)


def reset_runtime(destination):
    target = destination.resolve()
    if not target.is_relative_to(RESOURCES.resolve()) or target == RESOURCES.resolve():
        raise ValueError('Refusing to replace a runtime outside resources')
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)


def install_zip_runtime(name):
    spec = RUNTIMES[name]
    archive = download(spec['url'], spec['url'].rsplit('/', 1)[1], spec['sha256'])
    destination = RESOURCES / name
    marker = destination / '.office-archive-sha256'
    if not marker.is_file() or marker.read_text() != spec['sha256']:
        reset_runtime(destination)
        extract_zip(archive, destination, strip_root=name == 'node')
        marker.write_text(spec['sha256'])
    return destination


def require_version(executable, args, expected):
    actual = subprocess.check_output([str(executable), *args], encoding='utf-8').strip()
    if actual != expected:
        raise RuntimeError(f'Unexpected runtime version: {executable}: {actual}; expected {expected}')
    print(actual, flush=True)


def prepare_powershell():
    destination = install_zip_runtime('powershell')
    require_version(destination / 'pwsh.exe', ['-NoProfile', '-NonInteractive', '-Command',
        '$PSVersionTable.PSVersion.ToString()'], RUNTIMES['powershell']['version'])


def prepare():
    if sys.platform != 'win32':
        raise RuntimeError('Windows x64 runtimes must be prepared on Windows')
    CACHE.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    python_version = (ROOT / 'backend/.python-version').read_text().strip()
    # uv's pinned version supplies the Python download URL and SHA256 manifest.
    import tomllib
    uv_version = tomllib.loads((ROOT / 'backend/pyproject.toml').read_text(encoding='utf-8'))['tool']['uv']['required-version'].removeprefix('==')
    actual_uv = subprocess.check_output(['uv', '--version'], encoding='utf-8').split()[1]
    if actual_uv != uv_version:
        raise RuntimeError(f'Build requires uv {uv_version}; found {actual_uv}')
    python_source = CACHE / 'python' / f'cpython-{python_version}-windows-x86_64-none'
    if not (python_source / 'python.exe').is_file():
        run('uv', 'python', 'install', python_version, '--install-dir', CACHE / 'python', '--no-bin', '--no-registry')
    python_target = RESOURCES / 'python'
    python_marker = python_target / '.office-python-version'
    if not python_marker.is_file() or python_marker.read_text() != python_version:
        reset_runtime(python_target)
        shutil.copytree(python_source, python_target, dirs_exist_ok=True)
        python_marker.write_text(python_version)
    require_version(python_target / 'python.exe', ['--version'], f'Python {python_version}')
    lock_hash = hashlib.sha256((ROOT / 'backend/uv.lock').read_bytes()).hexdigest()
    dependency_marker = python_target / '.office-lock-sha256'
    if not dependency_marker.exists() or dependency_marker.read_text() != lock_hash:
        requirements = BUILD / 'requirements.txt'
        subprocess.run(['uv', 'export', '--locked', '--no-dev', '--no-emit-workspace', '--no-editable',
            '--output-file', str(requirements)], cwd=ROOT / 'backend', check=True, stdout=subprocess.DEVNULL)
        run('uv', 'pip', 'sync', '--python', python_target / 'python.exe',
            '--require-hashes', '--break-system-packages', requirements)
        dependency_marker.write_text(lock_hash)

    node = install_zip_runtime('node')
    git = install_zip_runtime('git')
    prepare_powershell()
    run(python_target / 'python.exe', '-c', 'import uvicorn, lancedb, langgraph, yaml; print("Bundled Python imports OK")')
    require_version(node / 'node.exe', ['--version'], 'v' + RUNTIMES['node']['version'])
    require_version(git / 'cmd/git.exe', ['--version'], 'git version ' + RUNTIMES['git']['version'])
    print('Packaged runtimes ready.', flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--powershell-only']:
        prepare_powershell()
    else:
        prepare()
