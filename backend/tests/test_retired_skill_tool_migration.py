import importlib.util
from pathlib import Path

import pytest

from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, save_tool_manifest


@pytest.mark.parametrize(('script', 'retired_tool', 'backup_name'), [
    ('remove_retired_skill_tool', 'describe_skill', 'tools.before-skill-directory.yaml'),
    ('remove_retired_routing_tool', 'resolve_employee_routing', 'tools.before-routing-removal.yaml'),
])
def test_explicit_removal_preserves_employee_choices_and_is_idempotent(tmp_path, script, retired_tool, backup_name):
    path = Path(__file__).parents[1] / f'scripts/{script}.py'
    spec = importlib.util.spec_from_file_location(script, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    employee = tmp_path / 'users/alice/agents/front'
    employee.mkdir(parents=True)
    original = EmployeeToolManifest(1, tuple(sorted(('apply_patch', retired_tool, 'read_file', 'report_to_pm'))))
    save_tool_manifest(employee, original, expected_revision='')
    assert len(module.migrate(tmp_path)) == 1
    assert load_tool_manifest(employee) == original
    assert len(module.migrate(tmp_path, apply=True)) == 1
    assert load_tool_manifest(employee).tools == ('apply_patch', 'read_file', 'report_to_pm')
    assert (employee / backup_name).is_file()
    assert module.migrate(tmp_path, apply=True) == []
