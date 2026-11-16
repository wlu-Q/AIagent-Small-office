from types import SimpleNamespace

import pytest

from deerflow.office import terminal


@pytest.mark.parametrize('system', ['nt', 'posix'])
def test_powershell_probe_only_uses_windows_creation_flags_on_windows(tmp_path, monkeypatch, system):
    executable = tmp_path / 'pwsh'
    executable.write_bytes(b'probe')
    monkeypatch.setenv('OFFICE_POWERSHELL_EXECUTABLE', str(executable))
    monkeypatch.setenv('OPENAI_API_KEY', 'not-for-child')
    monkeypatch.setattr(terminal, 'os', SimpleNamespace(name=system, environ=terminal.os.environ))
    captured = {}
    def probe(args, **kwargs):
        captured.update(kwargs)
        assert args[0] == str(executable.resolve())
        return SimpleNamespace(returncode=0, stdout='7.6.9\n')
    monkeypatch.setattr(terminal.subprocess, 'run', probe)
    if system == 'nt':
        monkeypatch.setattr(terminal.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    else:
        monkeypatch.delattr(terminal.subprocess, 'CREATE_NO_WINDOW', raising=False)
    assert terminal.powershell_executable() == (str(executable.resolve()), '7.6.9')
    assert captured['creationflags'] == (0x08000000 if system == 'nt' else 0)
    assert 'OPENAI_API_KEY' not in captured['env']


def test_explicit_missing_powershell_does_not_fall_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setenv('OFFICE_POWERSHELL_EXECUTABLE', str(tmp_path / 'missing'))
    monkeypatch.setattr(terminal.shutil, 'which', lambda name: pytest.fail('Unexpected fallback'))
    with pytest.raises(RuntimeError, match='absolute path'):
        terminal.powershell_executable()
