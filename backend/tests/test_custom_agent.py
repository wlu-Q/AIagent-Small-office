"""Tests for custom agent support."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base_dir: Path):
    """Return a Paths instance pointing to base_dir."""
    from deerflow.config.paths import Paths

    return Paths(base_dir=base_dir)


def _write_agent(
    base_dir: Path,
    name: str,
    config: dict,
    soul: str = "You are helpful.",
    *,
    user_id: str = "test-user-autouse",
) -> None:
    """Write a current user-scoped agent directory."""
    agent_dir = base_dir / "users" / user_id / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_copy = dict(config)
    if "name" not in config_copy:
        config_copy["name"] = name

    with open(agent_dir / "config.yaml", "w") as f:
        yaml.dump(config_copy, f)

    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")


# ===========================================================================
# 1. Paths class – agent path methods
# ===========================================================================


class TestPaths:
    def test_user_agent_paths(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.user_agents_dir("user-one") == tmp_path / "users" / "user-one" / "agents"
        assert paths.user_agent_dir("user-one", "code-reviewer") == tmp_path / "users" / "user-one" / "agents" / "code-reviewer"


# ===========================================================================
# 2. AgentConfig – Pydantic parsing
# ===========================================================================


class TestAgentConfig:
    def test_minimal_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(name="my-agent")
        assert cfg.name == "my-agent"
        assert cfg.description == ""
        assert cfg.model is None
        assert cfg.tool_groups is None

    def test_full_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(
            name="code-reviewer",
            description="Specialized for code review",
            model="deepseek-v3",
            tool_groups=["file:read", "bash"],
        )
        assert cfg.name == "code-reviewer"
        assert cfg.model == "deepseek-v3"
        assert cfg.tool_groups == ["file:read", "bash"]

    def test_config_from_dict(self):
        from deerflow.config.agents_config import AgentConfig

        data = {"name": "test-agent", "description": "A test", "model": "gpt-4"}
        cfg = AgentConfig(**data)
        assert cfg.name == "test-agent"
        assert cfg.model == "gpt-4"
        assert cfg.tool_groups is None


# ===========================================================================
# 3. load_agent_config
# ===========================================================================


class TestLoadAgentConfig:
    def test_load_valid_config(self, tmp_path):
        config_dict = {"name": "code-reviewer", "description": "Code review agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "code-reviewer", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("code-reviewer")

        assert cfg.name == "code-reviewer"
        assert cfg.description == "Code review agent"
        assert cfg.model == "deepseek-v3"

    def test_load_missing_agent_raises(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("nonexistent-agent")

    def test_load_missing_config_yaml_raises(self, tmp_path):
        # Create directory without config.yaml
        (tmp_path / "users" / "test-user-autouse" / "agents" / "broken-agent").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("broken-agent")

    def test_load_config_infers_name_from_dir(self, tmp_path):
        """Config without 'name' field should use directory name."""
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "inferred-name"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("description: My agent\n")
        (agent_dir / "SOUL.md").write_text("Hello")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("inferred-name")

        assert cfg.name == "inferred-name"

    def test_load_config_with_tool_groups(self, tmp_path):
        config_dict = {"name": "restricted", "tool_groups": ["file:read", "file:write"]}
        _write_agent(tmp_path, "restricted", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("restricted")

        assert cfg.tool_groups == ["file:read", "file:write"]

    def test_load_config_with_skills_empty_list(self, tmp_path):
        config_dict = {"name": "no-skills-agent", "skills": []}
        _write_agent(tmp_path, "no-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("no-skills-agent")

        assert cfg.skills == []

    def test_load_config_with_skills_omitted(self, tmp_path):
        config_dict = {"name": "default-skills-agent"}
        _write_agent(tmp_path, "default-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("default-skills-agent")

        assert cfg.skills is None


# ===========================================================================
# 3b. resolve_agent_dir — memory-only directory fallback (#3390)
# ===========================================================================


# ===========================================================================
# 4. load_agent_soul
# ===========================================================================


class TestLoadAgentSoul:
    def test_reads_soul_file(self, tmp_path):
        expected_soul = "You are a specialized code review expert."
        _write_agent(tmp_path, "code-reviewer", {"name": "code-reviewer"}, soul=expected_soul)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="code-reviewer")
            soul = load_agent_soul(cfg.name)

        assert soul == expected_soul

    def test_missing_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "no-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: no-soul\n")
        # No SOUL.md created

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="no-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_empty_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "empty-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: empty-soul\n")
        (agent_dir / "SOUL.md").write_text("   \n   ")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="empty-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_loads_soul_without_config_yaml(self, tmp_path):
        """SOUL.md should load even when the agent dir has no config.yaml (#4135)."""
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "soul-only"
        agent_dir.mkdir(parents=True)
        # Deliberately no config.yaml – the agent is configured externally
        (agent_dir / "SOUL.md").write_text("You are a brave agent.", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="test-user-autouse"):
            from deerflow.config.agents_config import load_agent_soul

            soul = load_agent_soul("soul-only")

        assert soul == "You are a brave agent."


# ===========================================================================
# 5. list_custom_agents
# ===========================================================================


class TestListCustomAgents:
    def test_empty_when_no_agents_dir(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert agents == []

    def test_discovers_multiple_agents(self, tmp_path):
        _write_agent(tmp_path, "agent-a", {"name": "agent-a"})
        _write_agent(tmp_path, "agent-b", {"name": "agent-b", "description": "B"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_skips_dirs_without_config_yaml(self, tmp_path):
        # Valid agent
        _write_agent(tmp_path, "valid-agent", {"name": "valid-agent"})
        # Invalid dir (no config.yaml)
        (tmp_path / "users" / "test-user-autouse" / "agents" / "invalid-dir").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "valid-agent"

    def test_skips_non_directory_entries(self, tmp_path):
        # Create the agents dir with a file (not a dir)
        agents_dir = tmp_path / "users" / "test-user-autouse" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "not-a-dir.txt").write_text("hello")
        _write_agent(tmp_path, "real-agent", {"name": "real-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "real-agent"

    def test_returns_sorted_by_name(self, tmp_path):
        _write_agent(tmp_path, "z-agent", {"name": "z-agent"})
        _write_agent(tmp_path, "a-agent", {"name": "a-agent"})
        _write_agent(tmp_path, "m-agent", {"name": "m-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert names == sorted(names)


# ===========================================================================
# 7. Memory isolation: _get_memory_file_path
# ===========================================================================


