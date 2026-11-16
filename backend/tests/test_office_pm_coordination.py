# 本文件属于 Office PM 协作边界测试模块：只验证能力候选、PM 排除规则和委派所需的员工事实。
# 不负责 PM 模型推理质量、公共任务表、第二调度器或 Subagent 内部 Agent Loop 测试。
# 关联：capability_registry.py、routing.py 与 packages.py 为 DeerFlow 原生 task 工具提供员工能力包。
from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from deerflow.config.paths import Paths
from deerflow.office.authority import (
    DEFAULT_AUTHORITY,
    DELEGATING_AUTHORITY,
    build_authority_prompt,
)
from deerflow.office.capability_registry import EmployeeCapabilityRegistry
from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.group_messages import GroupMessageStore
from deerflow.office.messages import build_pm_group_input
from deerflow.office.routing import (
    build_report_to_pm_tool,
    build_search_employee_registry_tool,
)
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.runs.manager import RunManager


def test_pm_authority_uses_routed_employee_ids_for_native_task_calls() -> None:
    prompt = build_authority_prompt(DELEGATING_AUTHORITY)

    assert "真实 employee_id 作为 task 的 subagent_type" in prompt
    assert "本次工作已明确负责员工及其 ID 时可直接委派，不重复路由" in prompt
    assert "general-purpose 和 bash 在 Office Run 中无效" in prompt
    assert "task 失败时根据真实错误判断" in prompt


def test_pm_authority_exposes_resolved_preselected_employee_ids() -> None:
    prompt = build_authority_prompt(
        DELEGATING_AUTHORITY,
        preselected_employee_ids=["sunhe"],
    )

    assert "本次用户已 @ 的员工 ID：sunhe" in prompt
    assert "不要因为员工忙碌或搜索结果为空而判定其不存在" in prompt


def test_pm_authority_separates_clarification_from_missing_capability() -> None:
    prompt = build_authority_prompt(DELEGATING_AUTHORITY)

    assert "信息不足时必须调用 ask_clarification" in prompt
    assert "不得仅用普通文字提问代替" in prompt
    assert "普通回复中不得提出澄清问题" in prompt
    assert "必须立即调用 ask_clarification" in prompt
    assert "需求已明确但能力不存在时，不调用 ask_clarification 或 task" in prompt
    assert "能力路由返回 ready 后才调用 task" not in prompt
    assert "使用能力路由时根据其结果选择员工" in prompt


def test_regular_employee_reports_to_the_task_requester() -> None:
    prompt = build_authority_prompt(DEFAULT_AUTHORITY)

    assert "按本次任务来源向用户或 PM 说明结果和阻碍" in prompt
    assert "不能只用普通文字说明" not in prompt
    from deerflow.office.presentation import FILE_DELIVERY_GUIDANCE
    assert '用户直接交办时，直接向用户说明结果和阻碍' in FILE_DELIVERY_GUIDANCE
    assert '收到 PM 委派时，向 PM 返回结果和能力缺口' in FILE_DELIVERY_GUIDANCE


def test_pm_handoff_names_integration_target_without_a_reading_checklist():
    prompt = build_authority_prompt(DELEGATING_AUTHORITY)
    assert '选定一个工作区 ID 作为 target，其余 ID 作为 source_workspace_ids' in prompt
    assert '默认在绑定项目执行时也需传 target' in prompt
    assert '不附加无差别重读文件清单' in prompt
    assert '员工按实际修改需要读取' in prompt


def test_pm_preview_report_uses_the_tested_url_and_scope():
    prompt = build_authority_prompt(DELEGATING_AUTHORITY)
    assert '准确对应实际检查的 URL 和参数' in prompt
    assert '不把一个地址的证据套给另一个地址' in prompt
    assert '仅做 HTTP 请求就只说明服务可访问' in prompt
    assert '页面渲染与交互尚未验证' in prompt


def test_report_to_pm_accepts_list_fields_from_model() -> None:
    report_tool = build_report_to_pm_tool("frontend-a")

    report = report_tool.invoke(
        {
            "goal": "检查日程系统的前端实现",
            "missing_capabilities": ["three.js"],
            "evidence": ["当前员工没有 three.js Skill"],
            "completed_work": ["已读取前端目录"],
            "files": ["src/App.vue"],
            "acceptance_remaining": ["需要补充 Three.js 交互"],
        }
    )

    assert report["completed_work"] == ["已读取前端目录"]
    assert report["acceptance_remaining"] == ["需要补充 Three.js 交互"]


@pytest.mark.parametrize('field', ['completed_work', 'files', 'acceptance_remaining'])
@pytest.mark.parametrize('value', ['已经完成文档', '["已经完成文档"]', None])
def test_report_to_pm_rejects_string_lists_at_schema_boundary(field, value):
    from pydantic import ValidationError

    report_tool = build_report_to_pm_tool('frontend-a')
    schema = report_tool.args_schema.model_json_schema()['properties'][field]
    assert schema['type'] == 'array'
    assert schema['default'] == []
    assert 'anyOf' not in schema
    with pytest.raises(ValidationError):
        report_tool.invoke({'goal': '目标', 'missing_capabilities': ['权限'], 'evidence': ['执行被拒绝'], field: value})


def test_report_to_pm_omitted_lists_are_independent_empty_arrays():
    report_tool = build_report_to_pm_tool('frontend-a')
    arguments = {'goal': '目标', 'missing_capabilities': ['权限'], 'evidence': ['执行被拒绝']}
    first = report_tool.invoke(arguments)
    for field in ('completed_work', 'files', 'acceptance_remaining'):
        first[field].append('previous report')
    second = report_tool.invoke(arguments)
    assert all(second[field] == [] for field in ('completed_work', 'files', 'acceptance_remaining'))


def test_pm_authority_recovers_empty_employee_search_before_stopping() -> None:
    prompt = build_authority_prompt(DELEGATING_AUTHORITY)

    assert "busy_employees 仅解释忙碌人员" in prompt
    assert "无合适空闲员工或必要工具不足时向用户报告" in prompt
    assert "不重复派工或假称已排队" in prompt


def _service(tmp_path: Path) -> OfficeEmployeeService:
    return OfficeEmployeeService(
        paths=Paths(tmp_path / ".deer-flow"),
        thread_store=MemoryThreadMetaStore(InMemoryStore()),
        checkpointer=InMemorySaver(),
        run_manager=RunManager(),
        user_id="default",
        model_profile_exists=lambda name: name == "profile-primary",
        workstation_ids=("desk-01", "desk-02", "desk-03"),
    )


def test_pm_group_input_uses_cursor_delta_and_includes_pm_replies(tmp_path: Path) -> None:
    store = GroupMessageStore(
        paths=Paths(tmp_path / ".deer-flow"),
        user_id="default",
    )
    store.append(
        message_id="user-1",
        sender_kind="user",
        sender_id="default",
        content="先看一下需求",
        mention_employee_ids=("pm",),
    )
    store.append(
        message_id="pm-1",
        sender_kind="pm",
        sender_display_name="测试员工",
        sender_id="pm",
        content="我已经确认过第一轮需求",
        source_run_id="run-old",
    )
    store.advance_pm_cursor(2)
    store.append(
        message_id="employee-1",
        sender_kind="employee",
        sender_display_name="测试员工",
        sender_id="frontend-a",
        content="前端检查完成",
        source_run_id="run-frontend",
    )
    store.append(
        message_id="user-2",
        sender_kind="user",
        sender_id="default",
        content="@PM 请继续安排后端",
        mention_employee_ids=("pm", "backend-b"),
    )
    store.append(
        message_id="pm-2",
        sender_kind="pm",
        sender_display_name="测试员工",
        sender_id="pm",
        content="这条回复已经在 PM Thread 中",
        source_run_id="run-current",
    )

    delta = store.read_pm_delta()
    messages = build_pm_group_input(
        delta.messages,
        current_message_id="user-2",
    )

    assert delta.sequence_end == 5
    assert messages == [
        {
            "role": "user",
            "content": (
                "[群聊增量上下文]\n"
                "[群聊上下文 message_id=employee-1 群聊员工 frontend-a] 前端检查完成\n"
                "[群聊上下文 message_id=pm-2 群聊 PM pm] 这条回复已经在 PM Thread 中\n"
                "[当前 @PM 请求 message_id=user-2]\n"
                "@PM 请继续安排后端"
            ),
        }
    ]
    assert store.get_pm_cursor() == 2


def test_pm_group_input_is_one_current_turn_for_same_thread(tmp_path: Path) -> None:
    store = GroupMessageStore(
        paths=Paths(tmp_path / ".deer-flow"),
        user_id="default",
    )
    store.append(
        message_id="old-user",
        sender_kind="user",
        sender_id="default",
        content="旧需求只作为背景",
        mention_employee_ids=("pm",),
    )
    store.append(
        message_id="old-employee",
        sender_kind="employee",
        sender_display_name="测试员工",
        sender_id="frontend-a",
        content="旧员工结果只作为背景",
        source_run_id="run-old",
    )
    store.append(
        message_id="current-user",
        sender_kind="user",
        sender_id="default",
        content="当前需求必须触发新的前端委派",
        mention_employee_ids=("pm", "frontend-a"),
    )

    messages = build_pm_group_input(
        store.read_pm_delta().messages,
        current_message_id="current-user",
    )

    assert messages == [
        {
            "role": "user",
            "content": (
                "[群聊增量上下文]\n"
                "[群聊上下文 message_id=old-user 群聊用户 default] 旧需求只作为背景\n"
                "[群聊上下文 message_id=old-employee 群聊员工 frontend-a] 旧员工结果只作为背景\n"
                "[当前 @PM 请求 message_id=current-user]\n"
                "当前需求必须触发新的前端委派"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_registry_uses_template_facts_and_excludes_pm_candidates(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    await service.ensure_builtin_pm(default_model_profile_id="profile-primary")
    await service.create_employee(
        {
            "id": "frontend-a",
            "templateId": "frontend-engineer",
            "displayName": "周礼",
            "avatar": "adam",
            "modelProfileId": "profile-primary",
        }
    )
    await service.save_personal_info(
        "frontend-a",
        {"skills": ["find-skills", "threejs"]},
    )

    registry = EmployeeCapabilityRegistry(service)
    assert (await registry.get("pm"))["can_delegate"] is True
    assert [entry["employee_id"] for entry in await registry.snapshot()] == [
        "frontend-a"
    ]
    entry = await registry.get("frontend-a")
    assert entry == {
        "employee_id": "frontend-a",
        "display_name": "周礼",
        "template_id": "frontend-engineer",
        "model_profile_id": "profile-primary",
        "is_pm": False,
        "enabled": True,
        "working": False,
        "can_delegate": False,
        "category": "软件开发",
        "tags": ["前端开发", "界面实现", "组件开发", "前端工程化"],
        "specialties": [
            "React",
            "Vue",
            "TypeScript",
            "HTML/CSS",
            "Vite",
            "响应式",
            "可访问性",
            "性能优化",
        ],
        "skills": [
            "find-skills",
            "threejs",
        ],
        "tool_groups": ["file:read", "file:write", "powershell_terminal", "web"],
        "tools": [
            "apply_patch",
            "glob",
            "grep",
            "image_search",
            "ls",
            "powershell_list",
            "powershell_read",
            "powershell_run",
            "powershell_stop",
            "present_files",
            "publish_task_result",
            "read_file",
            "report_to_pm",
            "review_skill_package",
            "search_office_library",
            "tool_search",
            "web_fetch",
            "web_search",
        ],
    }


@pytest.mark.asyncio
async def test_registry_search_uses_marketplace_layers_without_scores(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    await service.create_employee(
        {
            "id": "frontend-a",
            "templateId": "frontend-engineer",
            "displayName": "周礼",
            "avatar": "adam",
            "modelProfileId": "profile-primary",
        }
    )
    registry = EmployeeCapabilityRegistry(service)

    matches = await registry.search(
        categories=["软件开发"],
        tags=["前端开发"],
        specialties=["React"],
        hard_requirements=["apply_patch"],
    )

    assert [entry["employee_id"] for entry in matches] == ["frontend-a"]
    assert "score" not in matches[0]
    assert "domains" not in matches[0]
    assert "areas" not in matches[0]


@pytest.mark.asyncio
async def test_pm_registry_reads_current_state_and_separates_busy(tmp_path, monkeypatch):
    from deerflow.office import routing

    service = _service(tmp_path)
    await service.create_employee({
        "id": "frontend-a", "templateId": "frontend-engineer",
        "displayName": "周礼", "avatar": "adam", "modelProfileId": "profile-primary",
    })
    monkeypatch.setattr(routing, "OfficeEmployeeService", lambda **kwargs: service)
    tool = build_search_employee_registry_tool("user-a")
    result = await tool.ainvoke({})
    assert [entry["employee_id"] for entry in result["employees"]] == ["frontend-a"]
    assert "tool_groups" not in result["employees"][0]
    assert "ask_clarification" not in set(result["employees"][0]["tools"])
    assert result["busy_employees"] == []
    service.update_runtime_projection("frontend-a", {"state": "working", "statusDetail": "working", "currentTaskId": "other"})
    result = await tool.ainvoke({})
    assert result["employees"] == []
    assert [entry["employee_id"] for entry in result["busy_employees"]] == ["frontend-a"]
    assert "tool_groups" not in result["busy_employees"][0]
    missing = await tool.ainvoke({"required_tools": ["ask_clarification"]})
    assert missing == {"employees": [], "busy_employees": [], "tool_catalog": {}, "skill_catalog": {}}
    document = await tool.ainvoke({"required_tools": ["apply_patch"]})
    assert document["busy_employees"][0]["employee_id"] == "frontend-a"


def test_pm_search_filters_are_optional_and_no_busy_dispatch_switch():
    schema = build_search_employee_registry_tool("user-a").args_schema.model_json_schema()
    assert not schema.get("required")
    assert "include_working" not in schema["properties"]
    assert "required_tools" in schema["properties"]


def test_pm_search_schema_explains_exact_matching_and_browsing():
    from langchain_core.utils.function_calling import convert_to_openai_function
    from deerflow.office.tool_contract import compact_office_tool_descriptions

    query = compact_office_tool_descriptions([build_search_employee_registry_tool("default")])[0]
    definition = convert_to_openai_function(query)
    properties = definition["parameters"]["properties"]
    assert definition["description"].startswith("Find matching employees.")
    assert 'you can pass mode="all" to get the full employee list.' in definition["description"]
    assert "This returns the complete list without filters or a result-count limit." not in definition["description"]
    assert set(properties) == {"categories", "tags", "specialties", "required_tools", "exclude_employee_ids", "mode"}
    assert not definition["parameters"].get("required")
    assert all(value.get("description") for value in properties.values())
    assert "Exact technical specialties" in properties["specialties"]["description"]
    assert 'use mode="all"' in properties["specialties"]["description"]
    assert "whose abilities fit the goal or can accomplish it" in properties["specialties"]["description"]
    assert "report the blocker to the user" in properties["specialties"]["description"]
    assert properties["mode"]["enum"] == ["match", "all"]
    assert properties["mode"]["default"] == "match"
    assert "all ignores all filters" in properties["mode"]["description"]
    assert "Neither mode limits the number of results" in properties["mode"]["description"]
    assert "limit" not in properties


@pytest.mark.asyncio
@pytest.mark.parametrize("role_filter", ["categories", "tags", "specialties"])
async def test_pm_search_can_list_all_after_exact_miss_and_still_filter_in_match_mode(
    tmp_path, monkeypatch, role_filter,
):
    from deerflow.office import routing

    service = _service(tmp_path)
    for employee_id, template_id in [("frontend-a", "frontend-engineer"), ("document-a", "research-documentation")]:
        await service.create_employee({
            "id": employee_id, "templateId": template_id,
            "displayName": employee_id, "avatar": "adam", "modelProfileId": "profile-primary",
        })
    monkeypatch.setattr(routing, "OfficeEmployeeService", lambda **kwargs: service)
    query = build_search_employee_registry_tool("default")
    exact = await query.ainvoke({"specialties": ["Vue"], "required_tools": ["apply_patch"]})
    assert [entry["employee_id"] for entry in exact["employees"]] == ["frontend-a"]
    missing = await query.ainvoke({role_filter: ["安装一个技能包"], "required_tools": ["apply_patch"]})
    assert missing == {"employees": [], "busy_employees": [], "tool_catalog": {}, "skill_catalog": {}}
    browsed = await query.ainvoke({"mode": "all"})
    assert {entry["employee_id"] for entry in browsed["employees"]} == {"frontend-a", "document-a"}
    assert browsed["busy_employees"] == []
    service.update_runtime_projection("frontend-a", {"state": "working", "currentTaskId": "other"})
    available = await query.ainvoke({"mode": "all"})
    assert [entry["employee_id"] for entry in available["employees"]] == ["document-a"]
    assert [entry["employee_id"] for entry in available["busy_employees"]] == ["frontend-a"]
    excluded = await query.ainvoke({"mode": "match", "required_tools": ["apply_patch"], "exclude_employee_ids": ["document-a"]})
    assert excluded["employees"] == []
    assert [entry["employee_id"] for entry in excluded["busy_employees"]] == ["frontend-a"]
    unavailable = await query.ainvoke({"required_tools": ["ask_clarification"]})
    assert unavailable == {"employees": [], "busy_employees": [], "tool_catalog": {}, "skill_catalog": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"mode": "all"},
    {"mode": "match", "specialties": ["Vue"]},
    {
        "mode": "all", "categories": ["不存在的分类"], "tags": ["不存在的标签"],
        "specialties": ["不存在的专长"], "required_tools": ["powershell_run"],
        "exclude_employee_ids": ["employee-0"],
    },
])
async def test_pm_search_returns_every_available_and_busy_employee_without_count_limit(monkeypatch, arguments):
    entries = [{
        "employee_id": f"employee-{number}", "display_name": f"员工{number}",
        "enabled": number < 60, "working": number % 2 == 1,
        "category": "软件开发", "tags": ["前端开发"], "specialties": ["Vue"],
        "skills": [], "tools": ["read_file"],
    } for number in range(62)]

    async def snapshot(registry):
        assert registry.employee_service.user_id == "user-a"
        return entries

    monkeypatch.setattr(EmployeeCapabilityRegistry, "snapshot", snapshot)
    result = await build_search_employee_registry_tool("user-a").ainvoke(arguments)
    assert {entry["employee_id"] for entry in result["employees"]} == {
        f"employee-{number}" for number in range(0, 60, 2)
    }
    assert {entry["employee_id"] for entry in result["busy_employees"]} == {
        f"employee-{number}" for number in range(1, 60, 2)
    }


@pytest.mark.asyncio
async def test_pm_search_all_returns_empty_lists_for_empty_office(monkeypatch):
    async def snapshot(registry):
        return []

    monkeypatch.setattr(EmployeeCapabilityRegistry, "snapshot", snapshot)
    assert await build_search_employee_registry_tool("user-a").ainvoke({"mode": "all"}) == {
        "employees": [], "busy_employees": [], "tool_catalog": {}, "skill_catalog": {},
    }


@pytest.mark.asyncio
async def test_registry_shares_purposes_without_hiding_middle_candidates(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from langchain_core.messages import ToolMessage
    from deerflow.agents.middlewares.tool_output_budget_middleware import _patch_model_messages, _patch_tool_message
    from deerflow.config.tool_output_config import ToolOutputConfig
    from deerflow.sandbox.powershell_tools import powershell_run

    async def snapshot(registry):
        return [dict(employee_id=f'frontend-{number}', display_name=f'前端{number}', enabled=True,
                     working=False, category='开发', tags=['前端'], specialties=['Vue'],
                     tools=['powershell_run', 'powershell_read'], skills=['frontend-design']) for number in range(60)]

    monkeypatch.setattr(EmployeeCapabilityRegistry, 'snapshot', snapshot)
    monkeypatch.setattr('deerflow.skills.storage.get_or_new_user_skill_storage', lambda user_id:
                        SimpleNamespace(load_skills=lambda **kwargs: [SimpleNamespace(name='frontend-design', description='制作清晰美观的前端页面')]))
    result = await build_search_employee_registry_tool('user-a').ainvoke({'mode': 'all'})
    assert result['tool_catalog']['powershell_run'] == '通过 PowerShell 执行命令。'
    assert len(result['tool_catalog']) == 2
    assert result['skill_catalog'] == {'frontend-design': '制作清晰美观的前端页面'}
    content = json.dumps(result, ensure_ascii=False)
    assert content.count('制作清晰美观的前端页面') == 1
    assert content.count('通过 PowerShell 执行命令。') == 1
    assert powershell_run.description not in content
    message = ToolMessage(content=content, name='search_employee_registry', tool_call_id='lookup')
    config = ToolOutputConfig(externalize_min_chars=100, fallback_max_chars=100)
    assert _patch_tool_message(message, config, str(tmp_path)) is message
    assert _patch_model_messages([message], config) is None
    assert len(json.loads(message.content)['employees']) == 60


@pytest.mark.asyncio
async def test_employee_query_describes_authorized_tools_and_enabled_skills(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from deerflow.office import routing
    from deerflow.office.tool_contract import build_tool_catalog

    service = _service(tmp_path)
    await service.create_employee({
        "id": "document-a", "templateId": "research-documentation",
        "displayName": "萧以宁", "avatar": "adam", "modelProfileId": "profile-primary",
    })
    await service.save_personal_info("document-a", {"skills": ["code-documentation", "disabled-skill"]})
    description = "梳理代码职责与调用关系，面向使用者编写项目说明和接口文档。"

    def load_skills(*, enabled_only):
        assert enabled_only is True
        return [SimpleNamespace(name='code-documentation', description=description),
                SimpleNamespace(name='not-selected', description='Not selected for this employee')]

    def user_storage(user_id):
        assert user_id == 'default'
        return SimpleNamespace(load_skills=load_skills)

    monkeypatch.setattr('deerflow.skills.storage.get_or_new_user_skill_storage', user_storage)
    monkeypatch.setattr(routing, 'OfficeEmployeeService', lambda **kwargs: service)
    query = build_search_employee_registry_tool('default')
    catalog = build_tool_catalog()
    for working in (False, True):
        if working:
            service.update_runtime_projection('document-a', {'state': 'working', 'currentTaskId': 'other'})
        result = await query.ainvoke({'required_tools': ['apply_patch']})
        candidate = result['busy_employees' if working else 'employees'][0]
        assert candidate['skills'] == ['code-documentation']
        assert result['skill_catalog'] == {'code-documentation': description}
        tools = candidate['tools']
        assert result['tool_catalog']['apply_patch'] == catalog['apply_patch'].purpose
        assert 'powershell_run' not in tools
        assert 'ask_clarification' not in tools
        assert set(result['tool_catalog']) == set(tools)


def test_report_to_pm_explains_parameters_without_changing_contract():
    from langchain_core.utils.function_calling import convert_to_openai_function

    report = build_report_to_pm_tool('document-a')
    definition = convert_to_openai_function(report)
    properties = definition['parameters']['properties']
    assert set(properties) == {'goal', 'missing_capabilities', 'evidence', 'completed_work', 'files', 'acceptance_remaining'}
    assert definition['parameters']['required'] == ['goal', 'missing_capabilities', 'evidence']
    assert all(len(value['description']) > 25 for value in properties.values())
    assert 'not a routine progress update' in definition['description']
    assert report.return_direct is False


@pytest.mark.asyncio
async def test_document_employee_query_matches_delegated_package(tmp_path, monkeypatch):
    from test_office_employee_lifecycle import employee_app_config
    from deerflow.config import paths
    from deerflow.office import routing
    from deerflow.office.packages import resolve_employee_subagent_package

    app = employee_app_config()
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: app)
    service = _service(tmp_path)
    await service.create_employee({
        "id": "document-a", "templateId": "research-documentation",
        "displayName": "萧以宁", "avatar": "adam", "modelProfileId": "profile-primary",
    })
    await service.save_personal_info("document-a", {"skills": []})
    monkeypatch.setattr(paths, "_paths", service.paths)
    monkeypatch.setattr(routing, "OfficeEmployeeService", lambda **kwargs: service)
    search = build_search_employee_registry_tool("default")
    with pytest.raises(ValueError, match="Invalid required_tools: file:write"):
        await search.ainvoke({"required_tools": ["file:write"]})
    result = await search.ainvoke({"required_tools": ["read_file", "ls", "grep", "apply_patch"]})
    assert [entry["employee_id"] for entry in result["employees"]] == ["document-a"]
    candidate = result["employees"][0]
    package = resolve_employee_subagent_package("document-a", "default", app)
    assert candidate["tools"] == list(package.tool_contract.runtime_names)
    assert all(result["tool_catalog"][name] for name in candidate["tools"])
    assert "tool_groups" not in candidate
    assert "report_to_pm" in set(candidate["tools"])
    assert "ask_clarification" not in set(candidate["tools"])
    assert "ask_clarification" in service.get_capability_index("document-a")["runtimeAvailableTools"]
    assert await search.ainvoke({"required_tools": ["ask_clarification"]}) == {"employees": [], "busy_employees": [], "tool_catalog": {}, "skill_catalog": {}}
    assert candidate["skills"] == []


@pytest.mark.asyncio
async def test_invalid_registry_filter_can_be_corrected_in_native_loop(tmp_path, monkeypatch):
    from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from deerflow.office import routing

    service = _service(tmp_path)
    await service.create_employee({
        "id": "document-a", "templateId": "research-documentation",
        "displayName": "萧以宁", "avatar": "adam", "modelProfileId": "profile-primary",
    })
    monkeypatch.setattr(routing, "OfficeEmployeeService", lambda **kwargs: service)

    class RoutingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "routing-regression"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            results = [message for message in messages if isinstance(message, ToolMessage)]
            if len(results) == 2:
                reply = AIMessage(content="交给文档同事处理。")
            else:
                reply = AIMessage(content="", tool_calls=[dict(
                    name="search_employee_registry", id=f"search-{len(results)}",
                    args={"required_tools": ["apply_patch" if results else "file:write"]},
                )])
            return ChatResult(generations=[ChatGeneration(message=reply)])

    graph = create_agent(model=RoutingModel(), tools=[build_search_employee_registry_tool("default")],
                         middleware=[ToolErrorHandlingMiddleware()])
    state = await graph.ainvoke({"messages": [HumanMessage(content="找位同事写个产品介绍")]}, {"recursion_limit": 10})
    results = [message for message in state["messages"] if isinstance(message, ToolMessage)]
    assert [message.status for message in results] == ["error", "success"]
    assert "Invalid required_tools: file:write" in results[0].content
    assert "document-a" in results[1].content


@pytest.mark.parametrize('invalid_first', [False, True])
def test_report_to_pm_continues_native_loop_and_can_correct_arguments(invalid_first):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ReportModel(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self):
            return 'report-regression'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            results = [message for message in messages if isinstance(message, ToolMessage)]
            if results and results[-1].status != 'error':
                reply = AIMessage(content='缺少接口说明，已整理交接内容。')
            else:
                args = dict(goal='完成页面', missing_capabilities=['接口说明'], evidence=['接口未提供'],
                            files='src/Home.vue' if invalid_first and not results else ['src/Home.vue'])
                reply = AIMessage(content='', tool_calls=[dict(name='report_to_pm', args=args, id=f'report-{self.calls}')])
            return ChatResult(generations=[ChatGeneration(message=reply)])

    model = ReportModel()
    report = build_report_to_pm_tool('front')
    assert report.return_direct is False
    graph = create_agent(model=model, tools=[report])
    state = graph.invoke({'messages': [HumanMessage(content='把目前情况交接一下')]}, {'recursion_limit': 10})
    results = [message for message in state['messages'] if isinstance(message, ToolMessage)]
    assert [message.status for message in results] == (['error', 'success'] if invalid_first else ['success'])
    assert model.calls == (3 if invalid_first else 2)
    assert state['messages'][-1].type == 'ai'
    assert state['messages'][-1].content == '缺少接口说明，已整理交接内容。'
