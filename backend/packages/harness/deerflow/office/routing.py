"""Query live employee capabilities and report gaps without scheduling work."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Literal

from langchain.tools import BaseTool, tool

from deerflow.config.paths import get_paths
from deerflow.office.capability_registry import EmployeeCapabilityRegistry
from deerflow.office.employees import OfficeEmployeeService


def build_capability_report(
    *,
    source_employee_id: str,
    original_goal: str,
    missing_capabilities: list[str],
    evidence: list[str],
    completed_work: list[str] | None = None,
    files: list[str] | None = None,
    acceptance_remaining: list[str] | None = None,
) -> dict[str, Any]:
    return {
        'version': 1,
        'kind': 'office_capability_report',
        'status': 'needs_pm',
        'source_employee_id': source_employee_id,
        'target': 'pm',
        'goal': original_goal,
        'completed_work': list(completed_work or []),
        'missing_capabilities': list(missing_capabilities),
        'files': list(files or []),
        'evidence': list(evidence),
        'acceptance_remaining': list(acceptance_remaining or []),
    }


def _candidate_employees(
    entries: list[Mapping[str, Any]],
    *,
    skill_descriptions: Mapping[str, str],
) -> list[dict[str, Any]]:
    return [
        {
            'employee_id': entry['employee_id'],
            'display_name': entry['display_name'],
            'working': entry['working'],
            'category': entry['category'],
            'tags': entry['tags'],
            'specialties': entry['specialties'],
            'skills': [name for name in entry['skills'] if name in skill_descriptions],
            'tools': list(entry['tools']),
        }
        for entry in entries
    ]


def build_search_employee_registry_tool(user_id: str) -> BaseTool:
    @tool('search_employee_registry', parse_docstring=True)
    async def search_employee_registry(
        categories: list[str] | None = None,
        tags: list[str] | None = None,
        specialties: list[str] | None = None,
        required_tools: list[str] | None = None,
        exclude_employee_ids: list[str] | None = None,
        mode: Literal['match', 'all'] = 'match',
    ) -> dict[str, Any]:
        """Find matching employees.

        Prefer an exact role/capability match using known category, tag or specialty values.
        These filters use exact string matching, not natural-language search; a match in any
        of the three is sufficient. If no suitable available employee matches or filter values are unknown,
        you can pass mode="all" to get the full employee list.
        Based on the goal and returned capabilities, choose an available employee whose abilities fit
        the goal or can accomplish it; an exact role match is not required. Check that the chosen
        employee has the tools needed for the task. If no suitable available employee remains,
        report the missing capability or busy staff to the user rather than dispatching or claiming a queue.
        employees contains available candidates;
        busy_employees explains matching staff currently unavailable, not candidates to dispatch.
        Each employee's tools and skills list authorized names; tool_catalog and skill_catalog explain them once.
        required_tools takes exact names from employees[].tools (e.g. apply_patch), not groups like file:write or display labels. Invalid names must be corrected.
        Skills describe methods, not tool permissions. This query does not start work.

        Args:
            categories: Exact category values from employee records, used in match mode. This is not a free-text job request.
            tags: Exact role or capability tags from employee records, such as 前端开发, used in match mode.
            specialties: Exact technical specialties from employee records, such as Vue or React, not a task description. Prefer exact matching; if no suitable available employee matches, use mode="all", then choose an available employee whose abilities fit the goal or can accomplish it. If none fits, report the blocker to the user.
            required_tools: In match mode, all tool names the employee must have, using employees[].tools such as apply_patch, not tool groups or descriptions.
            exclude_employee_ids: Employee IDs to leave out of match-mode results.
            mode: match applies the supplied filters; all ignores all filters and returns every enabled regular employee in the current office, separated into available and busy lists. Neither mode limits the number of results.
        """
        if mode == 'all':
            categories = tags = specialties = required_tools = exclude_employee_ids = None
        registry = EmployeeCapabilityRegistry(OfficeEmployeeService(paths=get_paths(), thread_store=None, checkpointer=None, user_id=user_id))
        matches = await registry.search(
            categories=categories or [],
            tags=tags or [],
            specialties=specialties or [],
            hard_requirements=required_tools or [],
            exclude_employee_ids=set(exclude_employee_ids or []),
            include_working=True,
        )
        from deerflow.office.tool_contract import build_tool_catalog
        from deerflow.skills.storage import get_or_new_user_skill_storage

        tool_descriptions = {entry.name: entry.purpose for entry in build_tool_catalog().values() if not entry.members}
        storage = await asyncio.to_thread(get_or_new_user_skill_storage, user_id)
        skills = await asyncio.to_thread(storage.load_skills, enabled_only=True)
        skill_descriptions = {skill.name: skill.description for skill in skills}
        return {
            'employees': _candidate_employees([entry for entry in matches if not entry['working']], skill_descriptions=skill_descriptions),
            'busy_employees': _candidate_employees([entry for entry in matches if entry['working']], skill_descriptions=skill_descriptions),
            'tool_catalog': {name: tool_descriptions[name] for name in sorted({name for entry in matches for name in entry['tools']})},
            'skill_catalog': {name: skill_descriptions[name] for name in sorted({name for entry in matches for name in entry['skills']})
                              if name in skill_descriptions},
        }

    search_employee_registry.metadata = {'office_registry_tool': True}
    return search_employee_registry


def build_report_to_pm_tool(source_employee_id: str) -> BaseTool:
    normalized = str(source_employee_id or '').strip()
    if not normalized:
        raise ValueError('source_employee_id is required')

    @tool('report_to_pm', parse_docstring=True)
    def report_to_pm(
        goal: str,
        missing_capabilities: list[str],
        evidence: list[str],
        completed_work: list[str] = [],
        files: list[str] = [],
        acceptance_remaining: list[str] = [],
    ) -> dict[str, Any]:
        """Report a capability gap that prevents completing the assigned work and provide a handoff to PM.

        Use when the task needs capabilities, access or information you do not have.
        Describe the actual blocker and work already done so PM can decide how to proceed.
        This records a report; it does not contact the user, start another Run or end this Run.
        It is not a routine progress update or proof that the task passed acceptance.

        Args:
            goal: The assigned objective that is blocked, not a newly invented task.
            missing_capabilities: Specific capabilities, permissions or information needed to finish.
            evidence: Observed facts supporting the gap, such as a tool error or unavailable input; do not invent evidence.
            completed_work: Array of completed work items, for example ["Draft written"]. Omit if none; do not encode the array as a string.
            files: Array of relevant file paths or existing deliverables PM can use, for example ["docs/README.md"]. Do not encode the array as a string.
            acceptance_remaining: Array of requirements or verification still unfinished, not claims of success. Omit if none; do not encode the array as a string.
        """
        return build_capability_report(
            source_employee_id=normalized,
            original_goal=goal,
            missing_capabilities=missing_capabilities,
            evidence=evidence,
            completed_work=completed_work,
            files=files,
            acceptance_remaining=acceptance_remaining,
        )

    report_to_pm.metadata = {
        **(report_to_pm.metadata or {}),
        'office_routing_tool': True,
        'source_employee_id': normalized,
    }
    return report_to_pm
