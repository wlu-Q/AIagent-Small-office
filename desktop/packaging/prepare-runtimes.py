"""Assemble native Windows x64 or macOS arm64 runtimes in ignored build output."""
import argparse
import copy
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / 'output' / 'packaging'
CACHE = BUILD / 'cache'
RESOURCES = BUILD / 'resources'
RUNTIMES = json.loads((Path(__file__).with_name('runtimes.json')).read_text(encoding='utf-8'))


def runtime_spec(system=None, machine=None):
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system == 'win32' and machine in ('amd64', 'x86_64'):
        return RUNTIMES
    if system == 'darwin' and machine in ('arm64', 'aarch64'):
        return json.loads(Path(__file__).with_name('runtimes.macos.json').read_text(encoding='utf-8'))
    raise RuntimeError(f'Runtime preparation requires Windows x64 or native macOS arm64; found {system}/{machine}')


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


def extract_tar(archive, destination, strip_root=False):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as bundle:
        def checked_member(member, target):
            member = copy.copy(member)
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts:
                raise ValueError('Archive path escapes its destination')
            if strip_root:
                if len(name.parts) < 2:
                    return None
                member.name = str(PurePosixPath(*name.parts[1:]))
            return tarfile.data_filter(member, target)
        bundle.extractall(destination, filter=checked_member)


def reset_runtime(destination):
    target = destination.resolve()
    if not target.is_relative_to(RESOURCES.resolve()) or target == RESOURCES.resolve():
        raise ValueError('Refusing to replace a runtime outside resources')
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)


def install_runtime(name, specs):
    spec = specs[name]
    archive = download(spec['url'], spec['url'].rsplit('/', 1)[1], spec['sha256'])
    destination = RESOURCES / name
    marker = destination / '.office-archive-sha256'
    if not marker.is_file() or marker.read_text() != spec['sha256']:
        reset_runtime(destination)
        if spec['url'].endswith('.tar.gz'):
            extract_tar(archive, destination, strip_root=name == 'node')
        else:
            extract_zip(archive, destination, strip_root=name == 'node')
        if sys.platform == 'darwin' and name == 'powershell':
            executable = destination / 'pwsh'
            executable.chmod(executable.stat().st_mode | 0o111)
        marker.write_text(spec['sha256'])
    return destination


def require_version(executable, args, expected):
    actual = subprocess.check_output([str(executable), *args], encoding='utf-8').strip()
    if actual != expected:
        raise RuntimeError(f'Unexpected runtime version: {executable}: {actual}; expected {expected}')
    print(actual, flush=True)


def prepare_powershell():
    specs = runtime_spec()
    destination = install_runtime('powershell', specs)
    require_version(destination / ('pwsh.exe' if sys.platform == 'win32' else 'pwsh'),
        ['-NoProfile', '-NonInteractive', '-Command', '$PSVersionTable.PSVersion.ToString()'], specs['powershell']['version'])


def prepare():
    specs = runtime_spec()
    windows = sys.platform == 'win32'
    CACHE.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    python_version = (ROOT / 'backend/.python-version').read_text().strip()
    # uv's pinned version supplies the Python download URL and SHA256 manifest.
    import tomllib
    uv_version = tomllib.loads((ROOT / 'backend/pyproject.toml').read_text(encoding='utf-8'))['tool']['uv']['required-version'].removeprefix('==')
    actual_uv = subprocess.check_output(['uv', '--version'], encoding='utf-8').split()[1]
    if actual_uv != uv_version:
        raise RuntimeError(f'Build requires uv {uv_version}; found {actual_uv}')
    python_platform = 'windows-x86_64-none' if windows else 'macos-aarch64-none'
    python_executable = 'python.exe' if windows else 'bin/python3'
    python_source = CACHE / 'python' / f'cpython-{python_version}-{python_platform}'
    if not (python_source / python_executable).is_file():
        run('uv', 'python', 'install', f'cpython-{python_version}-{python_platform}',
            '--install-dir', CACHE / 'python', '--no-bin', *(['--no-registry'] if windows else []))
    python_target = RESOURCES / 'python'
    python_marker = python_target / '.office-python-version'
    if (not python_marker.is_file() or python_marker.read_text() != python_version
            or not (python_target / python_executable).is_file()):
        reset_runtime(python_target)
        shutil.copytree(python_source, python_target, dirs_exist_ok=True, symlinks=not windows)
        python_marker.write_text(python_version)
    require_version(python_target / python_executable, ['--version'], f'Python {python_version}')
    lock_hash = hashlib.sha256((ROOT / 'backend/uv.lock').read_bytes()).hexdigest()
    dependency_marker = python_target / '.office-lock-sha256'
    if not dependency_marker.exists() or dependency_marker.read_text() != lock_hash:
        requirements = BUILD / 'requirements.txt'
        subprocess.run(['uv', 'export', '--locked', '--no-dev', '--no-emit-workspace', '--no-editable',
            '--output-file', str(requirements)], cwd=ROOT / 'backend', check=True, stdout=subprocess.DEVNULL)
        run('uv', 'pip', 'sync', '--python', python_target / python_executable,
            '--require-hashes', '--break-system-packages', requirements)
        dependency_marker.write_text(lock_hash)

    node = install_runtime('node', specs)
    git = install_runtime('git', specs)
    prepare_powershell()
    run(python_target / python_executable, '-c', 'import uvicorn, lancedb, langgraph, yaml; print("Bundled Python imports OK")')
    require_version(node / ('node.exe' if windows else 'bin/node'), ['--version'], 'v' + specs['node']['version'])
    require_version(git / ('cmd/git.exe' if windows else 'bin/git'), ['--version'], 'git version ' + specs['git']['version'])
    print('Packaged runtimes ready.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--powershell-only', action='store_true')
    if parser.parse_args().powershell_only:
        prepare_powershell()
    else:
        prepare()
