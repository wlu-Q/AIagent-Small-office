# 本文件属于 Office 固定岗位模板测试模块：只验证模板事实及其只读公开投影。
# 不负责员工实例生命周期、动态画像、消息路由、员工排队或 Agent 执行测试。
# 关联：office/templates.py 保存岗位事实，Gateway Office Router 只投影可创建岗位的公开字段。
from __future__ import annotations

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import office
from deerflow.office.templates import (
    SCOPE_BOUNDARY_RULE,
    EmployeeTemplate,
    get_employee_template,
    list_employee_templates,
)

EXPECTED_TEMPLATE_IDS = {
    "pm",
    "product-manager",
    "frontend-engineer",
    "backend-engineer",
    "test-engineer",
    "research-documentation",
}
REAL_TOOL_GROUPS = {"web", "file:read", "file:write", "bash"}


def test_chinese_template_resources_match_current_role_text():
    import json
    from pathlib import Path
    resources = json.loads((Path(__file__).parents[2] / 'contracts/locales/zh-CN.json').read_text(encoding='utf-8'))
    for template in list_employee_templates():
        for field in ('role', 'category', 'tags', 'specialties', 'prompt'):
            value = getattr(template, field)
            expected = list(value) if isinstance(value, tuple) else value
            assert resources[f'templates.{template.template_id}.{field}'] == expected

EXPECTED_PROMPT_FACTS = {
    "pm": (
        "原始需求",
        "关键歧义",
        "ask_clarification",
        "不得用普通回复代替",
        "依赖",
        "验收标准",
        "用户预选员工",
        "不自行扩展",
        "不直接修改",
    ),
    "product-manager": ("用户问题", "业务目标", "非目标", "成功指标", "证据", "取舍", "技术现实"),
    "frontend-engineer": ("现有架构", "设计语言", "响应式", "可访问性", "性能", "真实运行验证"),
    "backend-engineer": ("现有架构", "接口契约", "数据模型", "认证授权", "数据一致性", "容错", "必要测试"),
    "test-engineer": ("接口契约", "正向", "逆向", "边界", "并发", "黑盒", "白盒", "证据"),
    "research-documentation": ("读者", "交付用途", "可靠来源", "事实", "推断", "不确定性", "示例可验证"),
}


def test_only_the_six_fixed_employee_templates_are_published() -> None:
    templates = list_employee_templates()

    assert {template.template_id for template in templates} == EXPECTED_TEMPLATE_IDS
    assert all(isinstance(template, EmployeeTemplate) for template in templates)
    assert sum(template.is_pm for template in templates) == 1


def test_templates_expose_compact_routing_facts_and_real_runtime_assets() -> None:
    for template in list_employee_templates():
        assert template.category.strip()
        assert 3 <= len(template.tags) <= 5
        assert 1 <= len(template.specialties) <= 8
        assert len(template.prompt) <= (800 if template.is_pm else 240)
        assert "find-skills" in template.skills
        assert set(template.tool_groups).issubset(REAL_TOOL_GROUPS)
        assert len(template.tags) == len(set(template.tags))
        assert len(template.specialties) == len(set(template.specialties))
        assert len(template.skills) == len(set(template.skills))


def test_template_prompts_preserve_the_selected_agency_role_principles() -> None:
    for template_id, required_facts in EXPECTED_PROMPT_FACTS.items():
        prompt = get_employee_template(template_id).prompt

        assert all(fact in prompt for fact in required_facts)


def test_pm_delegation_preserves_user_requirements_without_inventing_restrictions() -> None:
    prompt = get_employee_template('pm').prompt
    assert '不把自己的偏好表述成用户的禁止要求' in prompt
    assert '不改变用户已经明确的要求' in prompt
    assert '不把相互矛盾的要求交给员工执行' in prompt
    assert '若阻碍来自自己额外添加的条件' in prompt


def test_hirable_templates_state_the_scope_boundary_and_pm_does_not() -> None:
    """越界请求要先说明岗位边界；PM 是转派而不是拒绝，故不在这条规则覆盖范围内。"""
    templates = list_employee_templates()

    assert all(
        template.prompt.endswith(SCOPE_BOUNDARY_RULE)
        for template in templates
        if not template.is_pm
    )
    assert SCOPE_BOUNDARY_RULE not in get_employee_template("pm").prompt


def test_pm_is_only_delegating_template_and_defaults_to_editing_and_planning() -> None:
    pm = get_employee_template("pm")
    regular_templates = [
        template for template in list_employee_templates() if not template.is_pm
    ]

    assert pm.authority.can_delegate is True
    assert "file:write" not in pm.authority.denied_tool_groups
    assert "bash" not in pm.authority.denied_tool_groups
    assert "file:write" in pm.tool_groups
    assert "bash" not in pm.tool_groups
    assert pm.default_tools == ("write_todos",)
    assert all(template.authority.can_delegate is False for template in regular_templates)


def test_unknown_employee_template_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown employee template"):
        get_employee_template("custom-role")


@pytest.mark.asyncio
async def test_new_pm_gets_independent_skills_without_overwriting_existing_pm(tmp_path, monkeypatch):
    from test_office_employee_lifecycle import MODEL_PROFILE_ID, build_employee_service

    from deerflow.config import agents_config

    service, _, _ = build_employee_service(tmp_path)
    monkeypatch.setattr(agents_config, "get_paths", lambda: service.paths)
    await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)
    agent_dir = service.paths.user_agents_dir("default") / "pm"
    config_path = agent_dir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert set(config["skills"]) >= {
        "office-requirements-analysis", "office-architecture-design", "office-task-planning",
    }
    assert "office-visualization" not in config["skills"]
    assert "office-visualization" not in config["runtime_contract"]["skills"]
    tools = yaml.safe_load((agent_dir / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    assert {"apply_patch", "write_todos", "powershell_list", "powershell_read"} <= set(tools)
    assert not {"powershell_terminal", "powershell_run", "powershell_stop", "bash"} & set(tools)
    assert all("office-visualization" not in template.skills for template in list_employee_templates() if not template.is_pm)
    # Simulate a persisted, customized employee; startup must not migrate it.
    config["skills"] = ["find-skills"]
    config["runtime_contract"]["authority"]["allowed_configured_tool_names"] = ["read_file"]
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    (agent_dir / "SOUL.md").write_text("保留我的个性化提示", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in agent_dir.iterdir() if p.is_file()}
    await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)
    assert {p.name: p.read_bytes() for p in agent_dir.iterdir() if p.is_file()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("template_id", ["frontend-engineer", "backend-engineer"])
async def test_developer_templates_default_to_all_five_visible_tool_choices(tmp_path, template_id):
    from test_office_employee_lifecycle import MODEL_PROFILE_ID, build_employee_service

    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee({
        "id": "developer", "displayName": "开发员工", "templateId": template_id,
        "modelProfileId": MODEL_PROFILE_ID, "avatar": "adam",
    })
    agent_dir = service.paths.user_agents_dir("default") / "developer"
    tools = set(yaml.safe_load((agent_dir / "tools.yaml").read_text(encoding="utf-8"))["tools"])
    assert {"read_file", "ls", "glob", "grep", "apply_patch", "web_search", "web_fetch",
            "image_search", "search_office_library", "powershell_terminal"} <= tools
    assert "bash" not in tools
    assert "write_todos" not in tools


def test_gateway_lists_only_creatable_template_public_fields() -> None:
    app = FastAPI()
    app.include_router(office.router)

    with TestClient(app) as client:
        response = client.get("/api/office/employee-templates")

    assert response.status_code == 200
    templates = response.json()["templates"]
    assert {template["templateId"] for template in templates} == EXPECTED_TEMPLATE_IDS - {"pm"}
    assert all(
        set(template) == {
            "templateId",
            "role",
            "category",
            "tags",
            "specialties",
            "prompt",
            "skills",
        }
        for template in templates
    )
