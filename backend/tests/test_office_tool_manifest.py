from pathlib import Path

import pytest


def test_manifest_roundtrip_and_conflict(tmp_path):
    from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, manifest_revision, save_tool_manifest

    manifest = EmployeeToolManifest(1, ('read_file', 'report_to_pm'))
    revision = save_tool_manifest(tmp_path, manifest, expected_revision='')
    assert load_tool_manifest(tmp_path) == manifest
    assert revision == manifest_revision(manifest)
    with pytest.raises(RuntimeError, match='revision'):
        save_tool_manifest(tmp_path, manifest, expected_revision='stale')


@pytest.mark.parametrize('content', ['tools: []', 'version: 2\ntools: []', 'version: 1\ntools: null', 'version: 1\ntools: [read_file, read_file]', 'version: 1\ntools: ["*"]'])
def test_manifest_rejects_invalid_input(tmp_path, content):
    from deerflow.office.tool_manifest import load_tool_manifest

    (tmp_path / 'tools.yaml').write_text(content, encoding='utf-8')
    with pytest.raises(ValueError):
        load_tool_manifest(tmp_path)


def test_missing_manifest_never_means_all(tmp_path):
    from deerflow.office.tool_manifest import load_tool_manifest

    with pytest.raises(FileNotFoundError, match='migrat'):
        load_tool_manifest(tmp_path)


def test_skill_external_allowed_tools_is_inert(tmp_path):
    from deerflow.skills.parser import parse_skill_file

    path = tmp_path / 'SKILL.md'
    path.write_text('---\nname: example\ndescription: Example\nallowed-tools: Read,Write\n---\nInstructions', encoding='utf-8')
    from deerflow.skills.types import SkillCategory
    skill = parse_skill_file(path, SkillCategory.PUBLIC)
    assert skill is not None
    assert not hasattr(skill, 'allowed_tools')


def test_mcp_manifest_identity_selects_prefixed_runtime_tool():
    from langchain_core.tools import StructuredTool

    from deerflow.office.tool_contract import ResolvedEmployeeTools, select_contract_tools

    tool = StructuredTool.from_function(
        lambda value: value,
        name='browser_search',
        description='Search with the browser MCP server',
    )
    tool.metadata = {'office_tool_id': 'mcp/browser/search'}
    contract = ResolvedEmployeeTools(
        revision='revision',
        authorized_ids=('mcp/browser/search',),
        available_ids=('mcp/browser/search',),
        unavailable_reasons={},
        runtime_names=('browser_search',),
        derived_groups=(),
    )

    assert select_contract_tools([tool], contract) == [tool]


@pytest.mark.parametrize('name', ['file:write', 'file:read', 'web', 'powershell_terminal', '编辑文件', 'write_file', 'describe_skill'])
def test_required_tools_reject_groups_labels_and_retired_names(name):
    from deerflow.office.tool_contract import validate_required_tool_names

    with pytest.raises(ValueError, match='Invalid required_tools') as error:
        validate_required_tool_names([name])
    assert name in str(error.value)
    assert 'Correct the arguments' in str(error.value)


def test_required_tools_use_runtime_names_not_mcp_manifest_ids(monkeypatch):
    from langchain_core.tools import StructuredTool
    from deerflow.office.tool_contract import validate_required_tool_names

    external = StructuredTool.from_function(lambda value: value, name='browser_search', description='Search')
    external.metadata = {'office_tool_id': 'mcp/browser/search'}
    monkeypatch.setattr('deerflow.mcp.cache.get_cached_mcp_tools', lambda: [external])
    validate_required_tool_names(['read_file', 'apply_patch', 'web_search', 'powershell_run', 'browser_search'])
    validate_required_tool_names([])
    validate_required_tool_names(None)
    with pytest.raises(ValueError, match='mcp/browser/search'):
        validate_required_tool_names(['mcp/browser/search'])
