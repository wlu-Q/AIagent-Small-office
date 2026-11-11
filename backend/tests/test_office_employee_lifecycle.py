# 本文件属于 Office 员工生命周期测试模块：只验证固定模板员工的创建、修改、禁用、删除和 Thread 身份。
# 不负责消息路由、员工排队、DeerFlow Run 执行、Subagent 或前端交互测试。
# 关联：office/employees.py、templates.py 与 thread_ids.py 共同提供被测员工生命周期。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from deerflow.config.paths import Paths
from deerflow.office.employees import DEFAULT_WORKSTATION_IDS, OfficeEmployeeService
from deerflow.office.templates import SCOPE_BOUNDARY_RULE, get_employee_template
from deerflow.office.thread_ids import employee_thread_id
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus

MODEL_PROFILE_ID = "profile-primary"


def employee_app_config():
    """Use public defaults and an explicit model, independent of developer config."""
    from deerflow.config.app_config import AppConfig
    defaults = Path(__file__).resolve().parents[2] / 'desktop/packaging/defaults/config.yaml'
    payload = yaml.safe_load(defaults.read_text(encoding='utf-8'))
    payload['models'] = [{
        'name': MODEL_PROFILE_ID,
        'use': 'langchain_openai:ChatOpenAI',
        'model': 'gpt-4.1',
        'api_key': 'test-only',
        'base_url': 'http://127.0.0.1:1/v1',
    }]
    return AppConfig.model_validate(payload)


def test_default_workstation_pool_matches_the_map_capacity() -> None:
    assert DEFAULT_WORKSTATION_IDS == tuple(
        f"workstation-{index:02d}" for index in range(1, 13)
    )


def build_employee_service(
    tmp_path: Path,
    *,
    workstation_ids: tuple[str, ...] = ("desk-01", "desk-02", "desk-03"),
) -> tuple[OfficeEmployeeService, MemoryThreadMetaStore, InMemorySaver]:
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    checkpointer = InMemorySaver()
    service = OfficeEmployeeService(
        paths=Paths(tmp_path / ".deer-flow"),
        thread_store=thread_store,
        checkpointer=checkpointer,
        run_manager=RunManager(),
        user_id="default",
        model_profile_exists=lambda profile_id: profile_id == MODEL_PROFILE_ID,
        workstation_ids=workstation_ids,
    )
    return service, thread_store, checkpointer


def frontend_employee_payload(employee_id: str = "frontend-a") -> dict[str, str]:
    return {
        "id": employee_id,
        "templateId": "frontend-engineer",
        "displayName": "周礼",
        "avatar": "adam",
        "modelProfileId": MODEL_PROFILE_ID,
    }


@pytest.mark.asyncio
async def test_template_employee_creation_persists_final_agent_package_and_threads(
    tmp_path: Path,
) -> None:
    service, thread_store, checkpointer = build_employee_service(tmp_path)

    employee = await service.create_employee(frontend_employee_payload())

    assert employee == {
        **employee,
        "id": "frontend-a",
        "templateId": "frontend-engineer",
        "displayName": "周礼",
        "avatar": "adam",
        "modelProfileId": MODEL_PROFILE_ID,
        "workstationId": "desk-01",
        "isPm": False,
    }
    agent_dir = service.paths.user_agent_dir("default", "frontend-a")
    config = yaml.safe_load((agent_dir / "config.yaml").read_text(encoding="utf-8"))
    office = yaml.safe_load((agent_dir / "office.yaml").read_text(encoding="utf-8"))

    assert config["name"] == "frontend-a"
    assert config["model"] == MODEL_PROFILE_ID
    assert "tool_groups" not in config
    assert "report_to_pm" in yaml.safe_load((agent_dir / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    assert config["skills"] == ["find-skills", "frontend-design", "web-design-guidelines"]
    assert config["runtime_contract"]["template_id"] == "frontend-engineer"
    assert config["runtime_contract"]["authority"]["can_delegate"] is False
    assert "capability_profile" not in config["runtime_contract"]
    assert office["template_id"] == "frontend-engineer"
    assert office["workstation_id"] == "desk-01"
    assert "default_thread_id" not in office
    soul = (agent_dir / "SOUL.md").read_text(encoding="utf-8")
    assert soul.startswith("你是前端工程师")
    # 模板默认文案自带岗位边界规则；用户自己写的 Prompt 则一个字都不加（见下方自定义 Prompt 用例）。
    assert SCOPE_BOUNDARY_RULE in soul

    for kind in ("private", "group"):
        thread_id = employee_thread_id("default", "frontend-a", kind)
        thread = await thread_store.get(thread_id, user_id="default")
        checkpoint = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        )
        assert thread is not None
        assert thread["metadata"]["office_conversation_kind"] == kind
        assert checkpoint is not None
        assert service.paths.thread_dir(thread_id, user_id="default").is_dir()


@pytest.mark.asyncio
async def test_template_employee_creation_uses_office_default_model_when_request_omits_it(
    tmp_path: Path,
) -> None:
    service, _, _ = build_employee_service(tmp_path)
    service.set_default_model_profile_id(MODEL_PROFILE_ID)

    employee = await service.create_employee(
        {
            "id": "frontend-default-model",
            "templateId": "frontend-engineer",
            "displayName": "周礼",
            "avatar": "adam",
        }
    )

    assert employee["modelProfileId"] == MODEL_PROFILE_ID


@pytest.mark.asyncio
async def test_employee_creation_applies_prompt_and_additional_skills_without_mutating_template(
    tmp_path: Path,
) -> None:
    service, _, _ = build_employee_service(tmp_path)
    template_skills = list(get_employee_template("frontend-engineer").skills)

    await service.create_employee(
        {
            **frontend_employee_payload(),
            "prompt": "你是一个专注于可访问性的前端工程师。",
            "skills": ["find-skills", "custom-frontend-skill", "custom-frontend-skill"],
        }
    )

    agent_dir = service.paths.user_agent_dir("default", "frontend-a")
    config = yaml.safe_load((agent_dir / "config.yaml").read_text(encoding="utf-8"))

    assert (agent_dir / "SOUL.md").read_text(encoding="utf-8") == (
        "你是一个专注于可访问性的前端工程师。\n"
    )
    assert config["skills"] == [*template_skills, "custom-frontend-skill"]
    assert list(get_employee_template("frontend-engineer").skills) == template_skills


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["soul", "tools", "permissions", "enabled"])
async def test_employee_creation_rejects_free_form_runtime_fields(
    tmp_path: Path,
    field: str,
) -> None:
    service, _, _ = build_employee_service(tmp_path)
    payload = frontend_employee_payload()
    payload[field] = "forged"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="Unsupported employee creation fields"):
        await service.create_employee(payload)


@pytest.mark.asyncio
async def test_employee_creation_rejects_unknown_model_and_exhausted_workstations(
    tmp_path: Path,
) -> None:
    service, _, _ = build_employee_service(tmp_path, workstation_ids=("desk-01",))
    invalid_model = frontend_employee_payload()
    invalid_model["modelProfileId"] = "missing-profile"

    with pytest.raises(ValueError, match="Unknown model profile"):
        await service.create_employee(invalid_model)

    await service.create_employee(frontend_employee_payload())
    with pytest.raises(RuntimeError, match="No office workstation is available"):
        await service.create_employee(
            {
                **frontend_employee_payload("frontend-b"),
                "displayName": "周礼二号",
            }
        )

    agents_root = service.paths.user_agents_dir("default")
    assert not (agents_root / "frontend-b").exists()
    assert not list(agents_root.glob(".frontend-b.staging-*"))


@pytest.mark.asyncio
async def test_builtin_pm_bootstrap_is_idempotent_and_group_only(tmp_path: Path) -> None:
    service, thread_store, _ = build_employee_service(tmp_path)

    first = await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)
    second = await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)

    assert first["id"] == "pm"
    assert first["templateId"] == "pm"
    assert first["isPm"] is True
    assert second["createdAt"] == first["createdAt"]
    assert [employee["id"] for employee in await service.list_employees()] == ["pm"]
    assert await thread_store.get(
        employee_thread_id("default", "pm", "group", is_pm=True),
        user_id="default",
    ) is not None
    assert await thread_store.get(
        "office__default__pm__private",
        user_id="default",
    ) is None


@pytest.mark.asyncio
async def test_builtin_pm_backfills_missing_model_profile(tmp_path: Path) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service._create_employee_unlocked(
        {
            "id": "pm",
            "templateId": "pm",
            "displayName": "项目经理",
            "avatar": "adam",
            "modelProfileId": None,
        },
        allow_pm=True,
    )

    repaired = await service.ensure_builtin_pm(
        default_model_profile_id=MODEL_PROFILE_ID,
    )

    assert repaired["modelProfileId"] == MODEL_PROFILE_ID


@pytest.mark.asyncio
async def test_public_employee_creation_cannot_create_pm(tmp_path: Path) -> None:
    service, _, _ = build_employee_service(tmp_path)

    with pytest.raises(ValueError, match="PM is created by the system"):
        await service.create_employee(
            {
                "id": "another-pm",
                "templateId": "pm",
                "displayName": "另一个项目经理",
                "avatar": "adam",
                "modelProfileId": MODEL_PROFILE_ID,
            }
        )


@pytest.mark.asyncio
async def test_personal_info_updates_name_prompt_and_skills_only(tmp_path: Path) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())

    detail = await service.save_personal_info(
        "frontend-a",
        {
            "displayName": "新周礼",
            "prompt": "你现在专注于可访问性前端实现。",
            "skills": ["find-skills", "custom-frontend-skill"],
        },
    )

    employee = detail["employee"]
    assert employee["displayName"] == "新周礼"
    assert employee["avatar"] == "adam"
    assert employee["modelProfileId"] == MODEL_PROFILE_ID
    config = yaml.safe_load(
        (
            service.paths.user_agent_dir("default", "frontend-a") / "config.yaml"
        ).read_text(encoding="utf-8")
    )
    assert config["model"] == MODEL_PROFILE_ID
    assert (
        service.paths.user_agent_dir("default", "frontend-a") / "SOUL.md"
    ).read_text(encoding="utf-8") == "你现在专注于可访问性前端实现。\n"
    assert config["skills"] == ["find-skills", "custom-frontend-skill"]
    assert "prompt" not in config  # SOUL.md is the only Prompt source.


@pytest.mark.asyncio
async def test_personal_configuration_is_independent_and_can_remove_all_skills(tmp_path: Path) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    await service.create_employee(frontend_employee_payload("frontend-b"))
    before = service.get_employee_detail("frontend-b")
    await service.save_personal_info("frontend-a", {"prompt": "  New personal prompt  ", "skills": []})
    assert service.get_employee_detail("frontend-a")["personalInfo"] == {
        "displayName": "周礼", "prompt": "New personal prompt\n", "skills": [],
        "modelProfileId": MODEL_PROFILE_ID,
    }
    assert service.get_employee_detail("frontend-b") == before
    assert service.load_agent_config("frontend-a")["runtime_contract"]["skills"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [None, 1, {}, [], "", "   "])
async def test_personal_prompt_must_be_nonempty_text(tmp_path: Path, prompt: object) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    with pytest.raises(ValueError, match="prompt must be a non-empty string"):
        await service.save_personal_info("frontend-a", {"prompt": prompt})


@pytest.mark.asyncio
async def test_personal_save_rolls_back_all_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    agent_dir = service.paths.user_agent_dir("default", "frontend-a")
    before = {name: (agent_dir / name).read_bytes() for name in ("config.yaml", "office.yaml", "SOUL.md")}

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(service, "_save_office", fail)
    with pytest.raises(OSError, match="simulated disk failure"):
        await service.save_personal_info("frontend-a", {"prompt": "New prompt", "skills": []})
    assert {name: (agent_dir / name).read_bytes() for name in before} == before


@pytest.mark.asyncio
async def test_disabled_employee_cannot_be_renamed(tmp_path: Path) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    service.disable_employee("frontend-a")
    with pytest.raises(RuntimeError, match="空闲"):
        await service.save_personal_info("frontend-a", {"displayName": "New name"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["private", "group"])
@pytest.mark.parametrize("status,finalizing", [(RunStatus.pending, False), (RunStatus.running, False), (RunStatus.success, True)])
async def test_real_run_state_blocks_edits_even_when_projection_is_idle(tmp_path: Path, kind: str, status: RunStatus, finalizing: bool) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())
    record = await service.run_manager.create(
        thread_id=service.thread_id("frontend-a", kind), assistant_id="frontend-a", user_id="default",
    )
    record.status = status
    record.finalizing = finalizing
    before = service.get_employee_detail("frontend-a")
    for patch in ({"displayName": "new"}, {"prompt": "new"}, {"skills": []}, {"modelProfileId": MODEL_PROFILE_ID}):
        with pytest.raises(RuntimeError, match="正在工作"):
            await service.save_personal_info("frontend-a", patch)
    assert service.get_employee_detail("frontend-a") == before


@pytest.mark.asyncio
async def test_loaded_deerflow_packages_keep_old_prompt_skills_and_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deerflow.config import agents_config
    from deerflow.office.packages import resolve_employee_subagent_package

    service, _, _ = build_employee_service(tmp_path)
    monkeypatch.setattr(agents_config, "get_paths", lambda: service.paths)
    await service.create_employee(frontend_employee_payload())
    app = employee_app_config()
    old = resolve_employee_subagent_package("frontend-a", "default", app)
    old_prompt = old.config.system_prompt
    old_skills = list(old.config.skills)
    await service.save_personal_info("frontend-a", {"prompt": "Next run prompt", "skills": []})
    new = resolve_employee_subagent_package("frontend-a", "default", app)
    from deerflow.office.identity import RESPONSE_LANGUAGE_GUIDANCE, employee_identity
    from deerflow.office.presentation import FILE_DELIVERY_GUIDANCE

    assert new.config.system_prompt == employee_identity(new.config.description) + "\n\n" + FILE_DELIVERY_GUIDANCE + "\n\nNext run prompt\n\n" + RESPONSE_LANGUAGE_GUIDANCE
    assert new.config.skills == []
    assert old.config.system_prompt == old_prompt != new.config.system_prompt
    assert old.config.skills == old_skills != new.config.skills
    assert old.config.model == new.config.model == MODEL_PROFILE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["soul", "tools", "permissions", "templateId", "avatar"])
async def test_personal_info_rejects_template_owned_fields(
    tmp_path: Path,
    field: str,
) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())

    with pytest.raises(ValueError, match="Unsupported employee personal information fields"):
        await service.save_personal_info("frontend-a", {field: "forged"})


@pytest.mark.asyncio
async def test_personal_info_rejects_unknown_model_and_working_employee(
    tmp_path: Path,
) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.create_employee(frontend_employee_payload())

    with pytest.raises(ValueError, match="Unknown model profile"):
        await service.save_personal_info(
            "frontend-a",
            {"modelProfileId": 'unknown-model'},
        )

    service.update_runtime_projection("frontend-a", {"state": "working"})
    with pytest.raises(
        RuntimeError,
        match="员工正在工作暂时不能修改个人信息",
    ):
        await service.save_personal_info("frontend-a", {"displayName": "不能修改"})


@pytest.mark.asyncio
async def test_delete_employee_removes_both_threads_and_releases_workstation(
    tmp_path: Path,
) -> None:
    service, thread_store, checkpointer = build_employee_service(
        tmp_path,
        workstation_ids=("desk-01",),
    )
    await service.create_employee(frontend_employee_payload())
    thread_ids = [
        employee_thread_id("default", "frontend-a", kind)
        for kind in ("private", "group")
    ]

    deleted = await service.delete_employee("frontend-a")

    assert deleted["workstationId"] == "desk-01"
    assert not service.paths.user_agent_dir("default", "frontend-a").exists()
    for thread_id in thread_ids:
        assert await thread_store.get(thread_id, user_id="default") is None
        assert await checkpointer.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        ) is None
        assert not service.paths.thread_dir(thread_id, user_id="default").exists()

    replacement = await service.create_employee(
        {
            **frontend_employee_payload("frontend-b"),
            "displayName": "新员工",
        }
    )
    assert replacement["workstationId"] == "desk-01"


@pytest.mark.asyncio
async def test_builtin_pm_cannot_be_disabled_or_deleted(tmp_path: Path) -> None:
    service, _, _ = build_employee_service(tmp_path)
    await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)

    with pytest.raises(ValueError, match="Builtin PM cannot be disabled"):
        service.disable_employee("pm")
    with pytest.raises(ValueError, match="Builtin PM cannot be deleted"):
        await service.delete_employee("pm")



@pytest.mark.asyncio
async def test_subagent_package_recomputes_configured_tools_from_current_runtime_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deerflow.config import agents_config
    from deerflow.office.packages import resolve_employee_subagent_package
    from deerflow.tools import tools as tools_module

    service, _, _ = build_employee_service(tmp_path)
    monkeypatch.setattr(agents_config, "get_paths", lambda: service.paths)
    await service.create_employee(frontend_employee_payload())
    agent_config_path = service.paths.user_agent_dir("default", "frontend-a") / "config.yaml"
    persisted = yaml.safe_load(agent_config_path.read_text(encoding="utf-8"))
    persisted["runtime_contract"]["runtime_tool_names"] = ["read_file", "apply_patch"]
    persisted["runtime_contract"]["unavailable_tool_groups"] = ["bash"]
    agent_config_path.write_text(
        yaml.safe_dump(persisted, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    from deerflow.config import get_app_config
    current_app_config = employee_app_config()
    package = resolve_employee_subagent_package('frontend-a', 'default', current_app_config)
    assert 'read_file' in package.allowed_configured_tool_names
    assert 'apply_patch' in package.allowed_configured_tool_names
    assert 'report_to_pm' in package.config.tools
    assert 'ask_clarification' not in package.config.tools
    assert package.tool_contract.revision





@pytest.mark.asyncio
async def test_employee_summary_exposes_stable_role_label(tmp_path):
    service, _thread_store, _checkpointer = build_employee_service(tmp_path)
    employee = await service.create_employee({**frontend_employee_payload('front'), 'displayName': '\u5c0f\u524d'})
    assert employee['role'] == '\u524d\u7aef\u5de5\u7a0b\u5e08'
