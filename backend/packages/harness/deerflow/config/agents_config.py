"""Configuration and loaders for user-scoped custom agents."""

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


def validate_agent_name(name: str | None) -> str | None:
    """Validate a custom agent name before using it in filesystem paths."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Invalid agent name. Expected a string or None.")
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    return name


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None
    # skills controls which skills are loaded into the agent's prompt:
    # - None (or omitted): load all enabled skills (default fallback behavior)
    # - [] (explicit empty list): disable all skills
    # - ["skill1", "skill2"]: load only the specified skills
    skills: list[str] | None = None
    # Server-owned, persisted facts materialized from office.yaml. The agent
    # consumes this alongside tool_groups/skills; clients cannot override it.
    runtime_contract: dict[str, Any] | None = None


def normalize_agent_soul(content: str | None) -> str:
    return str(content or '').replace('\r\n', '\n').replace('\r', '\n').strip()


def resolve_agent_dir(name: str, *, user_id: str | None = None) -> Path:
    """Return the current user's on-disk agent directory."""
    validated_name = validate_agent_name(name)
    if validated_name is None:
        raise ValueError("Agent name is required")
    effective_user = user_id or get_effective_user_id()
    return get_paths().user_agent_dir(effective_user, validated_name)


def is_office_managed_agent(name: str | None, *, user_id: str | None = None) -> bool:
    '''Return whether name is an employee managed by Little Office.

    ``office.yaml`` is the ownership marker in the current user's agent directory.
    '''
    validated_name = validate_agent_name(name)
    if validated_name is None:
        return False
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()
    return (paths.user_agent_dir(effective_user, validated_name) / 'office.yaml').is_file()


def load_agent_config(name: str | None, *, user_id: str | None = None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory.

    Args:
        name: The agent name.
        user_id: Owner of the agent. Defaults to the effective user from the
            current request context.

    Returns:
        AgentConfig instance, or ``None`` if ``name`` is ``None``.

    Raises:
        FileNotFoundError: If the agent directory or config.yaml does not exist.
        ValueError: If config.yaml cannot be parsed.
    """

    if name is None:
        return None

    name = validate_agent_name(name)
    agent_dir = resolve_agent_dir(name, user_id=user_id)
    config_file = agent_dir / "config.yaml"

    if not agent_dir.exists():
        raise FileNotFoundError(f"Agent directory not found: {agent_dir}")

    if not config_file.exists():
        raise FileNotFoundError(f"Agent config not found: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse agent config {config_file}: {e}") from e

    # Ensure name is set from directory name if not in file
    if "name" not in data:
        data["name"] = name

    return AgentConfig(**data)


def load_agent_soul(agent_name: str | None, *, user_id: str | None = None) -> str | None:
    """Read the current user's SOUL.md for a custom agent."""
    if agent_name:
        soul_path = resolve_agent_dir(agent_name, user_id=user_id) / SOUL_FILENAME
    else:
        soul_path = get_paths().base_dir / SOUL_FILENAME
    if not soul_path.exists():
        return None
    content = normalize_agent_soul(soul_path.read_text(encoding="utf-8"))
    return content or None


def list_custom_agents(*, user_id: str | None = None) -> list[AgentConfig]:
    """Return valid custom agents owned by the current user."""
    effective_user = user_id or get_effective_user_id()
    root = get_paths().user_agents_dir(effective_user)
    if not root.exists():
        return []

    agents: list[AgentConfig] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not (entry / "config.yaml").is_file():
            continue
        try:
            agent_cfg = load_agent_config(entry.name, user_id=effective_user)
            if agent_cfg is not None:
                agents.append(agent_cfg)
        except Exception as exc:
            logger.warning("Skipping agent %r: %s", entry.name, exc)
    agents.sort(key=lambda agent: agent.name)
    return agents
