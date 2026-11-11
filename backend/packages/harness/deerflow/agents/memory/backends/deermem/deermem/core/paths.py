"""DeerMem's own storage path resolution (no deer-flow ``get_paths`` / ``AGENT_NAME_PATTERN``).

DeerMem stores data under ``config.storage_path`` (absolute or relative),
``$DEERMEM_DATA_DIR``, or ``~/.deermem/``. Every memory document is scoped by
user, with an optional employee-specific bucket beneath that user.

user_id is sanitized in-process (``[A-Za-z0-9_-]`` + SHA-256 digest for lossy
ids) and agent_name validated against an inlined pattern -- DeerMem does not
import the host's ``make_safe_user_id`` / ``_validate_user_id`` /
``AGENT_NAME_PATTERN``.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import DeerMemConfig

# user_id charset + sanitization (mirrors the host's make_safe_user_id so
# existing per-user buckets line up after migration).
_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_UNSAFE_USER_ID_CHAR_RE = re.compile(r"[^A-Za-z0-9_\-]")
_SAFE_USER_ID_DIGEST_HEX_LEN = 16

# agent_name validation (inlined; was deer-flow's AGENT_NAME_PATTERN).
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


def safe_user_id(raw: str) -> str:
    """Normalize an external identity into the user-id charset (``[A-Za-z0-9_-]``).

    Idempotent: already-safe ids pass through; lossy ones get a short SHA-256
    digest suffix so two distinct inputs never share a bucket.
    """
    if not raw:
        raise ValueError("user_id must be a non-empty string.")
    sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw)
    if sanitized == raw:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_SAFE_USER_ID_DIGEST_HEX_LEN]
    return f"{sanitized}-{digest}"


def validate_agent_name(name: str) -> None:
    """Validate that the agent name is safe to use in filesystem paths."""
    if not name:
        raise ValueError("Agent name must be a non-empty string.")
    if not AGENT_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid agent name {name!r}: names must match {AGENT_NAME_PATTERN.pattern}")


def _default_root() -> Path:
    """DeerMem's default data root: ``$DEERMEM_DATA_DIR`` or ``~/.deermem/``."""
    env = os.environ.get("DEERMEM_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".deermem"


def memory_file_path(
    config: DeerMemConfig,
    agent_name: str | None = None,
    *,
    user_id: str,
) -> Path:
    """Resolve a user's memory file under DeerMem's data root."""
    root = Path(config.storage_path) if config.storage_path else _default_root()
    user_root = root / "users" / safe_user_id(user_id)
    if agent_name is not None:
        validate_agent_name(agent_name)
        return user_root / "agents" / agent_name.lower() / "memory.json"
    return user_root / "memory.json"
