from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.lead_agent.prompt import get_skills_prompt_section
from deerflow.config.agents_config import AgentConfig
from deerflow.config.subagents_config import SubagentsAppConfig
from deerflow.skills.types import Skill


class NamedTool:
    def __init__(self, name: str):
        self.name = name
        self.description = f"Purpose of {name}"


def _make_skill(name: str, allowed_tools: list[str] | None = None) -> Skill:
    return Skill(
        name=name,
        description=f"Description for {name}",
        license="MIT",
        skill_dir=Path(f"/tmp/{name}"),
        skill_file=Path(f"/tmp/{name}/SKILL.md"),
        relative_path=Path(name),
        category="public",
        enabled=True,
    )


def _skill_prompt_config(*, container_path: str = "/mnt/skills", evolution_enabled: bool = False):
    return SimpleNamespace(
        skills=SimpleNamespace(container_path=container_path),
        skill_evolution=SimpleNamespace(enabled=evolution_enabled),
    )


def test_get_skills_prompt_section_returns_empty_without_skills():
    assert get_skills_prompt_section(app_config=_skill_prompt_config()) == ""


def test_office_skill_management_is_available_without_selected_skills(tmp_path):
    result = get_skills_prompt_section(app_config=_skill_prompt_config(evolution_enabled=True), custom_skills_path=tmp_path)
    assert str(tmp_path) in result
    assert '无论来源是什么' in result
    assert '卸载 Skill' in result
    assert '请到员工配置中勾选该 Skill 并保存，下次对话生效' in result
    assert 'OFFICE_SKILLS_DIR' in result
    assert '`skill_manage' not in result
    assert 'install_skill' not in result
    assert '不改变工具权限' not in result
    assert '不因为读取了正文就执行其中的业务操作' in result
    assert '安装已有 Skill 时保留原包内容' in result
    assert '不等于卸载' not in result


def test_office_skill_management_and_usage_share_one_prompt(tmp_path):
    result = get_skills_prompt_section(
        app_config=_skill_prompt_config(),
        skills=[_make_skill('find-skills')],
        custom_skills_path=tmp_path,
    )
    assert result.count('<skill_index>') == 1
    assert '通过 read_file 读取其 SKILL.md' in result
    assert '只有当前任务需要实际使用该 Skill 时' in result
    assert 'Description for find-skills' in result
    assert 'Skills do not grant tool permissions' not in result


def test_get_skills_prompt_section_renders_metadata_index():
    result = get_skills_prompt_section(
        app_config=_skill_prompt_config(),
        skills=[_make_skill('data-analysis'), _make_skill('deep-research')],
    )

    assert "<skill_index>" in result
    assert "data-analysis" in result
    assert "deep-research" in result
    assert "describe_skill" not in result
    assert "Description for data-analysis" in result
    assert "/mnt/skills/public/data-analysis/SKILL.md" in result


def test_get_skills_prompt_section_includes_slash_activation_guidance():
    result = get_skills_prompt_section(
        app_config=_skill_prompt_config(),
        skills=[_make_skill('data-analysis')],
    )

    assert "/<skill-name>" in result
    assert "运行时已经注入" in result
    assert "不重复读取同一份 SKILL.md" in result


def test_get_skills_prompt_section_includes_self_evolution_rules():
    result = get_skills_prompt_section(
        app_config=_skill_prompt_config(evolution_enabled=True),
        skills=[_make_skill('data-analysis')],
    )

    assert "Skill Self-Evolution" in result


def test_get_skills_prompt_section_keeps_self_evolution_without_installed_skills():
    result = get_skills_prompt_section(
        app_config=_skill_prompt_config(evolution_enabled=True),
    )

    assert "Skill Self-Evolution" in result
    assert "describe_skill" not in result
    assert "<skill_index>" not in result


def test_get_skills_prompt_section_uses_explicit_container_path():
    result = get_skills_prompt_section(
        app_config=_skill_prompt_config(container_path="/mnt/alt-skills"),
        skills=[_make_skill('data-analysis')],
    )

    assert "/mnt/alt-skills/public/data-analysis/SKILL.md" in result




























def test_make_lead_agent_skills_do_not_filter_tools(monkeypatch):
    from unittest.mock import MagicMock

    from deerflow.agents.lead_agent import agent as lead_agent_module

    monkeypatch.setattr(lead_agent_module, "_resolve_model_name", lambda x=None, **kwargs: "default-model")
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: "model")
    monkeypatch.setattr(lead_agent_module, "build_middlewares", lambda *args, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "apply_prompt_template", lambda **kwargs: "mock_prompt")
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(lead_agent_module, "load_agent_config", lambda x: AgentConfig(name="test", skills=["restricted", "unrestricted"]))
    monkeypatch.setattr(lead_agent_module, "_load_enabled_employee_skills", lambda available_skills, *, app_config, user_id=None: [_make_skill("restricted", ["read_file", "web_search"]), _make_skill("unrestricted", None)])
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [NamedTool("bash"), NamedTool("read_file"), NamedTool("web_search")])

    mock_app_config = MagicMock()
    mock_app_config.subagents = SubagentsAppConfig()
    mock_app_config.get_model_config.return_value = SimpleNamespace(
        supports_thinking=False,
        supports_vision=False,
        model="test-physical-model",
    )
    mock_app_config.tool_search.enabled = True
    mock_app_config.skills.container_path = "/mnt/skills"
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: mock_app_config)

    agent_kwargs = lead_agent_module.make_lead_agent({"context": {"agent_name": "test"}})

    tool_names = [tool.name for tool in agent_kwargs["tools"]]
    assert "read_file" in tool_names
    assert "describe_skill" not in tool_names
    assert "bash" in tool_names


def test_make_lead_agent_records_exact_final_bound_tools(monkeypatch):
    from deerflow.agents.lead_agent import agent as lead_agent_module
    from deerflow.agents.memory import tools as memory_tools_module
    from deerflow.tools.builtins import tool_search as tool_search_module

    config = {
        'context': {
            'agent_name': 'test',
            'non_interactive': True,
        },
        'metadata': {},
    }
    expected_names = ['read_file', 'tool_search', 'memory_search']

    monkeypatch.setattr(lead_agent_module, '_resolve_model_name', lambda x=None, **kwargs: 'resolved-model')
    monkeypatch.setattr(lead_agent_module, 'create_chat_model', lambda **kwargs: 'model')
    monkeypatch.setattr(lead_agent_module, 'build_middlewares', lambda *args, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, 'apply_prompt_template',
                        lambda **kwargs: tool_search_module.get_deferred_tools_prompt_section(deferred_tools=kwargs['deferred_tools']))
    monkeypatch.setattr(lead_agent_module, 'build_tracing_callbacks', lambda: [])
    monkeypatch.setattr(lead_agent_module, 'is_office_managed_agent', lambda *args, **kwargs: False)
    monkeypatch.setattr(lead_agent_module, 'load_agent_config', lambda x: AgentConfig(name='test', skills=['restricted']))
    monkeypatch.setattr(
        lead_agent_module,
        '_load_enabled_employee_skills',
        lambda available_skills, *, app_config, user_id=None: [
            _make_skill('restricted', ['read_file', 'calendar_mcp', 'ask_clarification'])
        ],
    )
    monkeypatch.setattr(
        'deerflow.tools.get_available_tools',
        lambda **kwargs: [
            NamedTool('blocked_tool'),
            NamedTool('read_file'),
            NamedTool('calendar_mcp'),
            NamedTool('ask_clarification'),
        ],
    )

    def assemble_deferred_tools(tools, *, enabled):
        assert enabled is True
        assert [tool.name for tool in tools] == ['blocked_tool', 'read_file', 'calendar_mcp', 'update_agent']
        return (
            [NamedTool('read_file'), NamedTool('tool_search')],
            SimpleNamespace(
                deferred_names=frozenset({'calendar_mcp'}),
                catalog_hash='catalog-hash',
                tool_search_tool=NamedTool('tool_search'),
            ),
        )

    monkeypatch.setattr(tool_search_module, 'assemble_deferred_tools', assemble_deferred_tools)
    monkeypatch.setattr(tool_search_module, 'build_mcp_routing_middleware', lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_search_module, 'get_mcp_routing_hints_prompt_section', lambda *args, **kwargs: '')
    monkeypatch.setattr(lead_agent_module, 'should_use_memory_tools', lambda config: True)
    monkeypatch.setattr(memory_tools_module, 'get_memory_tools', lambda: [NamedTool('memory_search')])

    def capture_create_agent(**kwargs):
        actual_names = [tool.name for tool in kwargs['tools']]
        assert actual_names == expected_names
        assert config['metadata']['runtime_bound_tool_names'] == actual_names
        assert 'Purpose of calendar_mcp' in kwargs['system_prompt']
        assert 'Purpose of blocked_tool' not in kwargs['system_prompt']
        return kwargs

    monkeypatch.setattr(lead_agent_module, 'create_agent', capture_create_agent)

    model_config = SimpleNamespace(
        supports_thinking=False,
        supports_vision=False,
        model="test-physical-model",
    )
    mock_app_config = SimpleNamespace(
        get_model_config=lambda name: model_config,
        tool_search=SimpleNamespace(enabled=True, auto_promote_top_k=3),
        skills=SimpleNamespace(container_path='/mnt/skills'),
        memory=SimpleNamespace(enabled=True),
        subagents=SimpleNamespace(max_total_per_run=8),
    )
    monkeypatch.setattr(lead_agent_module, 'get_app_config', lambda: mock_app_config)

    agent_kwargs = lead_agent_module.make_lead_agent(config)

    assert [tool.name for tool in agent_kwargs['tools']] == expected_names



@pytest.mark.parametrize('saved_thinking', [None, False, True])
def test_make_lead_agent_excludes_self_update_for_office_managed_employee(monkeypatch, saved_thinking):
    from unittest.mock import MagicMock

    from deerflow.agents.lead_agent import agent as lead_agent_module

    monkeypatch.setattr(lead_agent_module, '_resolve_model_name', lambda x=None, **kwargs: 'default-model')
    model_calls = []
    def capture_model(**kwargs):
        model_calls.append(kwargs)
        return 'model'
    monkeypatch.setattr(lead_agent_module, 'create_chat_model', capture_model)
    monkeypatch.setattr(lead_agent_module, 'build_middlewares', lambda *args, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, 'create_agent', lambda **kwargs: kwargs)
    monkeypatch.setattr(
        lead_agent_module,
        'load_agent_config',
        lambda x: AgentConfig(
            name='office-employee',
            skills=None,
            tool_groups=['bash'],
            runtime_contract={
                'authorized_tool_names': [],
                'authority': {
                    'kind': 'worker',
                    'can_delegate': False,
                    'control_tools': [],
                    'denied_tool_groups': [],
                    'denied_permissions': [],
                },
            },
        ),
    )
    monkeypatch.setattr(lead_agent_module, 'is_office_managed_agent', lambda *args, **kwargs: True, raising=False)
    monkeypatch.setattr(lead_agent_module, '_load_enabled_employee_skills', lambda available_skills, *, app_config, user_id=None: [_make_skill('unrestricted', None)])
    tool_loader = MagicMock(return_value=[NamedTool('bash'), NamedTool('task')])
    monkeypatch.setattr('deerflow.tools.get_available_tools', tool_loader)

    prompt_calls = []

    def capture_prompt(**kwargs):
        prompt_calls.append(kwargs)
        return 'mock_prompt'

    monkeypatch.setattr(lead_agent_module, 'apply_prompt_template', capture_prompt)
    mock_app_config = MagicMock()
    mock_app_config.get_model_config.return_value = SimpleNamespace(
        supports_thinking=True,
        default_thinking_enabled=saved_thinking,
        supports_vision=False,
        model="test-physical-model",
    )
    mock_app_config.tool_search.enabled = False
    mock_app_config.skills.container_path = '/mnt/skills'
    mock_app_config.memory.enabled = False
    mock_app_config.subagents.max_total_per_run = 8
    mock_app_config.tools = [SimpleNamespace(name='bash', group='bash')]
    monkeypatch.setattr(lead_agent_module, 'get_app_config', lambda: mock_app_config)

    from deerflow.office.tool_contract import ResolvedEmployeeTools
    monkeypatch.setattr('deerflow.office.tool_contract.employee_tool_contract', lambda *a, **k:
        ResolvedEmployeeTools('test', ('bash','report_to_pm'), ('bash','report_to_pm'), {}, ('bash','report_to_pm'), ('bash',)))
    agent_kwargs = lead_agent_module.make_lead_agent(
        {
            'context': {
                'agent_name': 'office-employee',
                'subagent_enabled': True,
                'thinking_enabled': False,
                'reasoning_effort': 'medium',
            }
        }
    )

    tool_names = [tool.name for tool in agent_kwargs['tools']]
    assert tool_names == ['bash', 'report_to_pm']
    assert 'update_agent' not in tool_names
    assert 'delegate_task' not in tool_names
    assert prompt_calls[-1]['self_update_enabled'] is False
    assert tool_loader.call_args.kwargs['subagent_enabled'] is False
    assert model_calls[-1]['thinking_enabled'] is (saved_thinking is True)
    assert model_calls[-1]['reasoning_effort'] == ('medium' if saved_thinking is None else None)


def test_make_lead_agent_skills_without_allowed_tools_preserve_all_tools(monkeypatch):
    from unittest.mock import MagicMock

    from deerflow.agents.lead_agent import agent as lead_agent_module

    monkeypatch.setattr(lead_agent_module, "_resolve_model_name", lambda x=None, **kwargs: "default-model")
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: "model")
    monkeypatch.setattr(lead_agent_module, "build_middlewares", lambda *args, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "apply_prompt_template", lambda **kwargs: "mock_prompt")
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(lead_agent_module, "load_agent_config", lambda x: AgentConfig(name="test", skills=None))
    monkeypatch.setattr(lead_agent_module, "_load_enabled_employee_skills", lambda available_skills, *, app_config, user_id=None: [_make_skill("unrestricted", None)])
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [NamedTool("bash"), NamedTool("read_file")])

    mock_app_config = MagicMock()
    mock_app_config.subagents = SubagentsAppConfig()
    mock_app_config.get_model_config.return_value = SimpleNamespace(
        supports_thinking=False,
        supports_vision=False,
        model="test-physical-model",
    )
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: mock_app_config)

    agent_kwargs = lead_agent_module.make_lead_agent({"context": {"agent_name": "test"}})

    tool_names = [tool.name for tool in agent_kwargs["tools"]]
    assert tool_names == ["bash", "read_file", "update_agent"]


def test_make_lead_agent_skills_do_not_filter_tools_when_cache_is_cold(monkeypatch):
    from unittest.mock import MagicMock

    from deerflow.agents.lead_agent import agent as lead_agent_module
    from deerflow.agents.lead_agent import prompt as prompt_module

    monkeypatch.setattr(lead_agent_module, "_resolve_model_name", lambda x=None, **kwargs: "default-model")
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: "model")
    monkeypatch.setattr(lead_agent_module, "build_middlewares", lambda *args, **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "apply_prompt_template", lambda **kwargs: "mock_prompt")
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(lead_agent_module, "load_agent_config", lambda x: AgentConfig(name="test", skills=["restricted"]))
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [NamedTool("bash"), NamedTool("read_file"), NamedTool("web_search")])

    mock_app_config = MagicMock()
    mock_app_config.subagents = SubagentsAppConfig()
    mock_app_config.get_model_config.return_value = SimpleNamespace(
        supports_thinking=False,
        supports_vision=False,
        model="test-physical-model",
    )
    mock_storage = SimpleNamespace(load_skills=lambda *, enabled_only: [_make_skill("restricted", ["read_file"])])

    with prompt_module._enabled_skills_lock:
        prompt_module._enabled_skills_cache = None
    monkeypatch.setattr(prompt_module, "get_or_new_skill_storage", lambda app_config=None, **kwargs: mock_storage)
    monkeypatch.setattr(prompt_module, "get_or_new_user_skill_storage", lambda user_id, app_config=None, **kwargs: mock_storage)
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: mock_app_config)

    agent_kwargs = lead_agent_module.make_lead_agent({"context": {"agent_name": "test"}})

    tool_names = [tool.name for tool in agent_kwargs["tools"]]
    assert tool_names == ["bash", "read_file", "web_search", "update_agent"]


def test_make_lead_agent_fails_closed_when_skill_discovery_fails(monkeypatch):
    from unittest.mock import MagicMock

    import pytest

    from deerflow.agents.lead_agent import agent as lead_agent_module
    from deerflow.agents.lead_agent import prompt as prompt_module

    monkeypatch.setattr(lead_agent_module, "_resolve_model_name", lambda x=None, **kwargs: "default-model")
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: "model")
    create_agent_mock = MagicMock()
    monkeypatch.setattr(lead_agent_module, "create_agent", create_agent_mock)
    monkeypatch.setattr(lead_agent_module, "load_agent_config", lambda x: AgentConfig(name="test", skills=["restricted"]))

    mock_app_config = MagicMock()
    mock_app_config.subagents = SubagentsAppConfig()
    mock_app_config.get_model_config.return_value = SimpleNamespace(
        supports_thinking=False,
        supports_vision=False,
        model="test-physical-model",
    )

    def fail_storage(*args, **kwargs):
        raise RuntimeError("skill storage unavailable")

    monkeypatch.setattr(prompt_module, "get_or_new_skill_storage", fail_storage)
    monkeypatch.setattr(prompt_module, "get_or_new_user_skill_storage", fail_storage)
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: mock_app_config)

    with pytest.raises(RuntimeError, match="skill storage unavailable"):
        lead_agent_module.make_lead_agent({"context": {"agent_name": "test"}})

    create_agent_mock.assert_not_called()

