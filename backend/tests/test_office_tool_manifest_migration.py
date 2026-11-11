from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _legacy_employee(agent_dir: Path, *, state: str = "idle") -> None:
    agent_dir.mkdir(parents=True)
    (agent_dir / "office.yaml").write_text(
        yaml.safe_dump({"projection": {"state": state}}, sort_keys=False),
        encoding="utf-8",
    )
    (agent_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": "profile-primary",
                "tool_groups": ["filesystem"],
                "runtime_contract": {
                    "tool_groups": ["filesystem"],
                    "authorized_tool_names": [],
                    "runtime_tool_names": ["read_file"],
                    "authority": {"can_delegate": False},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_migration_dry_run_apply_and_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    from types import SimpleNamespace

    script_path = Path(__file__).parents[1] / "scripts" / "migrate_employee_tool_manifests.py"
    spec = importlib.util.spec_from_file_location("office_tool_manifest_migration", script_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    from deerflow.office.tool_manifest import EmployeeToolManifest

    agent_dir = tmp_path / "users" / "default" / "agents" / "frontend-a"
    _legacy_employee(agent_dir)
    manifest = EmployeeToolManifest(1, ("read_file", "report_to_pm"))
    monkeypatch.setattr(migration, "default_tool_manifest", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(migration, "build_tool_catalog", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        migration,
        "resolve_employee_tools",
        lambda *args, **kwargs: SimpleNamespace(
            runtime_names=("read_file", "report_to_pm"),
            unavailable_reasons={},
        ),
    )

    dry_run = migration.migrate_employee(agent_dir)
    assert dry_run["status"] == "dry_run"
    assert not (agent_dir / "tools.yaml").exists()
    assert "tool_groups" in yaml.safe_load((agent_dir / "config.yaml").read_text(encoding="utf-8"))

    applied = migration.migrate_employee(agent_dir, apply=True)
    assert applied["status"] == "migrated"
    assert yaml.safe_load((agent_dir / "tools.yaml").read_text(encoding="utf-8"))["tools"] == list(manifest.tools)
    migrated_config = yaml.safe_load((agent_dir / "config.yaml").read_text(encoding="utf-8"))
    assert "tool_groups" not in migrated_config
    assert "tool_groups" not in migrated_config["runtime_contract"]
    assert (agent_dir / "config.before-tools-migration.yaml").exists()

    retry = migration.migrate_employee(agent_dir, apply=True)
    assert retry["status"] == "already_migrated"


def test_migration_blocks_working_employee(tmp_path: Path) -> None:
    import importlib.util

    script_path = Path(__file__).parents[1] / "scripts" / "migrate_employee_tool_manifests.py"
    spec = importlib.util.spec_from_file_location("office_tool_manifest_migration", script_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    agent_dir = tmp_path / "agents" / "frontend-a"
    _legacy_employee(agent_dir, state="working")

    with pytest.raises(RuntimeError, match="active/interrupted"):
        migration.migrate_employee(agent_dir)
