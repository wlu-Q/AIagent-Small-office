# 本文件属于 Office 员工能力查询模块：只从员工与固定模板事实构造可查询的能力注册表。
# 不负责生成能力评分、持久化能力画像、选择执行员工、排队或启动 Run。
# 关联：employees.py 与 templates.py 提供事实，routing.py 按需查询当前候选。
from __future__ import annotations

from typing import Any

from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.templates import get_employee_template


def _values(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _search_entries(
    entries: list[dict[str, Any]],
    *,
    categories: list[str],
    tags: list[str],
    specialties: list[str],
    hard_requirements: list[str],
    exclude_employee_ids: set[str],
    include_working: bool = False,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    has_capability_filters = bool(categories or tags or specialties)
    for entry in entries:
        if entry["employee_id"] in exclude_employee_ids or not entry["enabled"]:
            continue
        if entry["working"] and not include_working:
            continue
        hard_values = set(entry.get("tools", []))
        if not set(hard_requirements).issubset(hard_values):
            continue
        if has_capability_filters and not (
            entry["category"] in categories
            or set(entry["tags"]).intersection(tags)
            or set(entry["specialties"]).intersection(specialties)
        ):
            continue
        candidates.append(entry)
    return sorted(
        candidates,
        key=lambda entry: (
            -len(set(entry["tags"]).intersection(tags)),
            -len(set(entry["specialties"]).intersection(specialties)),
            entry["employee_id"],
        ),
    )


class EmployeeCapabilityRegistry:
    """Read capability facts from fixed templates and current employee config."""

    def __init__(self, employee_service: OfficeEmployeeService) -> None:
        self.employee_service = employee_service

    async def get(self, employee_id: str) -> dict[str, Any]:
        normalized = self.employee_service.validate_employee_id(employee_id)
        summary = self.employee_service.get_employee_summary(normalized)
        template = get_employee_template(summary["templateId"])
        config = self.employee_service.load_agent_config(normalized)
        capability_index = self.employee_service.get_capability_index(normalized, execution_kind='delegated')
        return {
            "employee_id": normalized,
            "display_name": summary["displayName"],
            "template_id": template.template_id,
            "model_profile_id": summary["modelProfileId"],
            "is_pm": template.is_pm,
            "enabled": bool(summary["enabled"]),
            "working": await self.employee_service.is_employee_working(normalized),
            "can_delegate": template.authority.can_delegate,
            "category": template.category,
            "tags": list(template.tags),
            "specialties": list(template.specialties),
            "skills": _values(config.get("skills")),
            "tool_groups": capability_index['effectiveTools'],
            "tools": capability_index['runtimeAvailableTools'],
        }

    async def snapshot(self) -> list[dict[str, Any]]:
        employees = await self.employee_service.list_employees()
        entries = [await self.get(str(employee["id"])) for employee in employees]
        return [entry for entry in entries if not entry["is_pm"]]

    async def search(
        self,
        *,
        categories: list[str],
        tags: list[str],
        specialties: list[str],
        hard_requirements: list[str],
        exclude_employee_ids: set[str] | None = None,
        include_working: bool = False,
    ) -> list[dict[str, Any]]:
        from deerflow.office.tool_contract import validate_required_tool_names

        validate_required_tool_names(hard_requirements)
        return _search_entries(
            await self.snapshot(),
            categories=categories,
            tags=tags,
            specialties=specialties,
            hard_requirements=hard_requirements,
            exclude_employee_ids=set(exclude_employee_ids or set()),
            include_working=include_working,
        )
