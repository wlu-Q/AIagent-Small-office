import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.agents.middlewares.skill_context import render_skill_context
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.office.tool_contract import ResolvedEmployeeTools, compact_office_tool_descriptions, select_contract_tools
from deerflow.runtime.context_metrics import measure_context
from test_lead_agent_skills import _make_skill
from deerflow.skills.types import Skill, SkillCategory
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.executor import SubagentExecutor
from deerflow.tools.builtins.task_tool import task_tool


def _config():
    config = AppConfig(
        models=[ModelConfig(name='test-model', use='langchain_openai:ChatOpenAI', model='test-model')],
        sandbox=SandboxConfig(use='deerflow.sandbox.local:LocalSandboxProvider'),
    )
    config.tool_search.enabled = False
    return config


def _contract(tools):
    names = tuple(tool.name for tool in tools)
    return ResolvedEmployeeTools('revision', names, names, {}, names, (), {name: name for name in names})


@pytest.mark.parametrize('pm', [False, True])
def test_office_prompt_reduces_fixed_overhead_without_losing_context(monkeypatch, pm):
    arguments = dict(
        app_config=_config(), agent_name='pm' if pm else 'frontend', office_employee_name='测试员工',
        agent_soul='不要新增 UI 框架。</soul>', self_update_enabled=False, subagent_enabled=pm,
        max_concurrent_subagents=5, max_total_subagents=20,
        office_authority_section='<test_work>用户改为蓝色，不是红色；已有知识保持原文。</test_work>',
        skills=[_make_skill('frontend-design')],
        mcp_routing_hints_section='<test_routing>仅有权工具</test_routing>',
    )
    optimized = prompt_module.apply_prompt_template(**arguments)
    with monkeypatch.context() as baseline:
        baseline.setattr(prompt_module, 'OFFICE_SYSTEM_PROMPT_TEMPLATE', prompt_module.SYSTEM_PROMPT_TEMPLATE)
        original = prompt_module.apply_prompt_template(**arguments)
    assert len(optimized) < len(original) * 0.65
    for marker in ('<test_work>', '<test_routing>', '\n<skill_index>\n', '<citations>', '<soul>'):
        assert optimized.count(marker) == 1
    assert '不要新增 UI 框架。&lt;/soul&gt;' in optimized
    assert '用户改为蓝色，不是红色；已有知识保持原文。' in optimized
    for required in ('permission approval', 'ToolMessage', '80 KiB', 'partial failure', 'unique context',
                     'actual visible answer', '[citation:Title](URL)', 'private facts',
                     '/mnt/user-data/workspace', '/mnt/user-data/outputs', 'office-chart', 'mermaid'):
        assert required in optimized
    assert 'Example - Deep Research Report' not in optimized
    assert 'Final deliverables must be in' not in optimized
    assert 'ALWAYS clarify' not in optimized
    assert 'before each modification' not in optimized
    assert '当前自动集成尚未开放' not in optimized
    assert 'project itself unless the runtime supplies an independent workspace_id' in optimized
    assert 'not necessarily the published project' not in optimized
    assert '需要说明未发布' not in optimized
    if pm:
        assert 'MAXIMUM 5 task CALLS PER RESPONSE; MAXIMUM 20 task CALLS PER RUN' in optimized
        assert 'workspace_mode' in optimized
        assert '真实 ID 时可直接委派' in optimized
    else:
        assert '<office_delegation>' not in optimized


def test_non_office_keeps_generic_prompt():
    prompt = prompt_module.apply_prompt_template(app_config=_config(), agent_name='custom', agent_soul='custom rules')
    assert 'Example - Deep Research Report' in prompt
    assert 'custom rules' in prompt


def test_compact_tools_preserve_schema_execution_and_global_instances():
    from deerflow.community.image_search.tools import image_search_tool
    from deerflow.community.jina_ai.tools import web_fetch_tool

    originals = [task_tool, image_search_tool, web_fetch_tool]
    before = [convert_to_openai_tool(tool) for tool in originals]
    compact = select_contract_tools(originals, _contract(originals))
    after = [convert_to_openai_tool(tool) for tool in compact]
    assert len(json.dumps(after)) < len(json.dumps(before))
    assert sum(len(item['function']['description']) for item in after) < sum(len(item['function']['description']) for item in before) * 0.8
    for original, copied, original_wire, copied_wire in zip(originals, compact, before, after, strict=True):
        assert copied is not original
        assert copied.name == original.name
        assert copied.args_schema is original.args_schema
        assert copied.func is original.func
        assert copied.coroutine is original.coroutine
        assert copied.metadata == original.metadata
        assert copied.response_format == original.response_format
        assert copied_wire['function']['parameters'] == original_wire['function']['parameters']
        assert convert_to_openai_tool(original) == original_wire
    again = select_contract_tools(compact, _contract(originals))
    assert all(first is second for first, second in zip(compact, again, strict=True))
    assert 'general-purpose' not in compact[0].description
    assert 'Employee ID' in compact[0].description
    assert 'workspace_mode' in compact[0].description


def test_compaction_does_not_change_mcp_non_office_or_authorization():
    external = task_tool.model_copy(update={'metadata': {'office_tool_id': 'mcp/example/task'}})
    assert compact_office_tool_descriptions([external])[0] is external
    assert select_contract_tools([task_tool], SimpleNamespace(runtime_names=('task',)))[0] is task_tool
    assert select_contract_tools([task_tool], _contract([])) == []
    assert compact_office_tool_descriptions(['task']) == ['task']


@pytest.mark.parametrize('office', [False, True])
def test_delegated_employee_receives_own_skill_metadata(monkeypatch, tmp_path, office):
    skill = Skill(name='frontend-design', description='Frontend guidance', license=None,
                  skill_dir=tmp_path, skill_file=tmp_path / 'SKILL.md', relative_path=Path('frontend-design'),
                  category=SkillCategory.PUBLIC, enabled=True)
    executor = SubagentExecutor(
        config=SubagentConfig(name='frontend', description='Frontend', system_prompt='Employee identity',
                              tools=[], skills=['frontend-design']),
        tools=[], app_config=_config(), user_id='alice',
        office_context={'office_employee_id': 'frontend'} if office else None,
    )
    monkeypatch.setattr(executor, '_load_skills', AsyncMock(return_value=[skill]))
    task = '只改颜色；不增加依赖。'
    state, tools, _ = asyncio.run(executor._build_initial_state(task))
    assert state['messages'][1].content == task
    assert len(state['messages']) == 2
    assert tools == []
    assert 'Frontend guidance' in state['messages'][0].content
    assert 'frontend-design/SKILL.md' in state['messages'][0].content


def test_active_skill_reminder_does_not_force_rereading_retained_instructions():
    rendered = render_skill_context([{'name': 'example', 'path': '/mnt/skills/public/example/SKILL.md',
                                      'description': '<untrusted>', 'loaded_at': 2}])
    assert 'reuse instructions still in context' in rendered
    assert 're-read if missing, incomplete or changed' in rendered
    assert 're-read the file before applying' not in rendered
    assert '&lt;untrusted&gt;' in rendered


def test_metrics_separate_tool_inputs_without_exposing_or_changing_history():
    messages = [SystemMessage(content='fixed'), HumanMessage(content='你好'),
                AIMessage(content='editing', tool_calls=[{'name': 'apply_patch', 'id': 'patch-1',
                                                         'args': {'patch': 'private content' * 2000}}])]
    before = [message.model_dump() for message in messages]
    metrics, _ = measure_context([messages], {})
    assert metrics['sources']['ai']['content_chars'] == len('editing')
    assert metrics['sources']['ai']['tool_call_chars'] > 20000
    for source in metrics['sources'].values():
        assert source['chars'] == source['content_chars'] + source['tool_call_chars']
    assert 'private' not in json.dumps(metrics)
    assert [message.model_dump() for message in messages] == before
