import hashlib
import importlib.util
import io
from pathlib import Path

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / 'desktop/packaging/prepare-runtimes.py'
    spec = importlib.util.spec_from_file_location('office_prepare_runtimes', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(module, 'RESOURCES', tmp_path / 'resources')
    return module


def test_powershell_preparation_uses_verified_official_archive_without_host_shell(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, 'download', lambda *args: calls.append(args) or Path('runtime.zip'))
    monkeypatch.setattr(runtime, 'extract_zip', lambda archive, destination: calls.append((archive, destination)))
    monkeypatch.setattr(runtime, 'run', lambda *args: calls.append(args))
    runtime.prepare_powershell()
    assert calls[0] == (
        'https://github.com/PowerShell/PowerShell/releases/download/v7.4.13/PowerShell-7.4.13-win-x64.zip',
        'PowerShell-7.4.13-win-x64.zip',
        '8fb52d2172d285b230c2857a90ba4dd28ecf6477ba4a91f91b6854a647b33b65',
    )
    assert calls[1][1] == runtime.RESOURCES / 'powershell'
    assert calls[2][0] == runtime.RESOURCES / 'powershell/pwsh.exe'


def test_existing_bundled_powershell_is_verified_without_download(runtime, monkeypatch):
    executable = runtime.RESOURCES / 'powershell/pwsh.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'test')
    monkeypatch.setattr(runtime, 'download', lambda *args: pytest.fail('must not download'))
    calls = []
    monkeypatch.setattr(runtime, 'run', lambda *args: calls.append(args))
    runtime.prepare_powershell()
    assert calls[0][0] == executable


def test_corrupt_runtime_download_is_not_installed(runtime, monkeypatch):
    runtime.CACHE.mkdir()
    monkeypatch.setattr(runtime, 'request', lambda url: io.BytesIO(b'corrupt archive'))
    with pytest.raises(RuntimeError, match='Checksum mismatch'):
        runtime.download('https://example.test/runtime.zip', 'runtime.zip', hashlib.sha256(b'expected').hexdigest())
    assert not (runtime.CACHE / 'runtime.zip').exists()
    assert not runtime.RESOURCES.exists()
