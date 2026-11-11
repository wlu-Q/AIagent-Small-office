"""Narrow existing file-reading tools to the selected Skill directories.

The original sandbox still owns file access, user isolation and disabled-skill
checks. This wrapper only removes access; it never adds a tool or reads a body.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import posixpath
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from deerflow.constants import DEFAULT_SKILLS_CONTAINER_PATH
from deerflow.skills.types import Skill

_read_scope: ContextVar[Callable[[str], str | None] | None] = ContextVar("skill_read_scope", default=None)


def skill_read_allowed(path: str | Path) -> bool:
    """Check a Sandbox-resolved path within this tool invocation only."""
    check = _read_scope.get()
    return check is None or check(str(path)) is None


def require_skill_read_access(path: str | Path) -> None:
    if not skill_read_allowed(path):
        raise PermissionError("Skill file is outside this agent's available Skills.")


def _normalized(path: str) -> str:
    path = posixpath.normpath(path.replace("\\", "/"))
    return path.casefold() if os.name == "nt" else path


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def bind_skill_read_tools(tools: list, skills: list[Skill], *, app_config, user_id: str | None = None,
                         manage_custom: bool = False) -> list:
    """Preserve readers' schema/injection and sync/async implementation."""
    skills_config = getattr(app_config, "skills", None)
    container = getattr(skills_config, "container_path", DEFAULT_SKILLS_CONTAINER_PATH)
    virtual_root = _normalized(container)
    virtual_dirs = [(_normalized(s.get_container_path(container)), s.skill_dir) for s in skills]
    host_roots: list[str] = []
    get_host_path = getattr(skills_config, "get_skills_path", None)
    if callable(get_host_path):
        host_roots.append(_normalized(str(get_host_path())))
    if user_id:
        from deerflow.config.paths import get_paths, make_safe_user_id

        host_roots.append(_normalized(str(get_paths().user_custom_skills_dir(make_safe_user_id(user_id)))))
    host_roots = list({*host_roots, *(_normalized(str(Path(root).resolve())) for root in host_roots)})
    host_dirs = [_normalized(str(s.skill_dir.resolve())) for s in skills]
    if manage_custom and user_id:
        custom_root = get_paths().user_custom_skills_dir(make_safe_user_id(user_id))
        virtual_dirs.append((_normalized(f"{container}/custom"), custom_root))
        host_dirs.append(_normalized(str(custom_root.resolve())))

    def check(path: str) -> str | None:
        raw = path.replace("\\", "/")
        normalized = _normalized(raw)
        before_traversal = _normalized(raw.split("/../", 1)[0])
        if _within(normalized, virtual_root) or _within(before_traversal, virtual_root):
            for directory, host in virtual_dirs:
                if _within(normalized, directory):
                    # Do not permit supporting-resource symlinks to leave the
                    # selected directory even when the sandbox mounts all Skills.
                    relative = normalized[len(directory):].lstrip("/")
                    if (host / relative).resolve().is_relative_to(host.resolve()):
                        return None
            return "Error: Skill file is outside this agent's available Skills."
        resolved = _normalized(str(Path(path).resolve()))
        if any(_within(normalized, root) or _within(before_traversal, root) or _within(resolved, root) for root in host_roots):
            if not any(_within(resolved, root) for root in host_dirs):
                return "Error: Skill file is outside this agent's available Skills."
        return None

    def wrap(original, *, asynchronous: bool):
        signature = inspect.signature(original)

        def error(args, kwargs):
            path = signature.bind_partial(*args, **kwargs).arguments.get("path")
            return check(path) if isinstance(path, str) else None

        if asynchronous:
            @wraps(original)
            async def guarded_async(*args, **kwargs):
                denied = await asyncio.to_thread(error, args, kwargs)
                if denied:
                    return denied
                token = _read_scope.set(check)
                try:
                    return await original(*args, **kwargs)
                finally:
                    _read_scope.reset(token)
            return guarded_async

        @wraps(original)
        def guarded(*args, **kwargs):
            denied = error(args, kwargs)
            if denied:
                return denied
            token = _read_scope.set(check)
            try:
                return original(*args, **kwargs)
            finally:
                _read_scope.reset(token)
        return guarded

    result = []
    for tool in tools:
        if tool.name not in {"read_file", "ls", "glob", "grep"}:
            result.append(tool)
            continue
        updates = {}
        if callable(getattr(tool, "func", None)):
            updates["func"] = wrap(tool.func, asynchronous=False)
        if callable(getattr(tool, "coroutine", None)):
            updates["coroutine"] = wrap(tool.coroutine, asynchronous=True)
        result.append(tool.model_copy(update=updates) if updates else tool)
    return result
