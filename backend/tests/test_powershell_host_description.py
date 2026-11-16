import runpy

import pytest
from langchain_core.utils.function_calling import convert_to_openai_function

from deerflow.office.tool_contract import compact_office_tool_descriptions
from deerflow.sandbox import powershell_tools


@pytest.mark.parametrize('system,label,example', [
    ('Darwin', 'macOS', '.venv/bin/python'),
    ('Windows', 'Windows', '.venv/Scripts/python.exe'),
])
def test_host_description_reaches_model_without_changing_parameters(monkeypatch, system, label, example):
    monkeypatch.setattr('platform.system', lambda: system)
    loaded = runpy.run_path(powershell_tools.__file__)
    actual = compact_office_tool_descriptions([loaded['powershell_run']])[0]
    definition = convert_to_openai_function(actual)
    assert definition['description'].startswith(f'Current host: {label}; shell: PowerShell 7.')
    assert example in definition['description']
    assert 'Bash/Zsh' not in definition['description']
    assert definition['parameters'] == convert_to_openai_function(powershell_tools.powershell_run)['parameters']
    assert definition['name'] == 'powershell_run'
    assert actual.coroutine is not None
    for name in ('powershell_read', 'powershell_list', 'powershell_stop'):
        assert convert_to_openai_function(loaded[name]) == convert_to_openai_function(getattr(powershell_tools, name))
